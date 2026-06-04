#!/usr/bin/env python
"""SessionStart hook (matchers: startup|resume|compact):
   Tier 1 recency 캐시(torch-free)에서 최근 앵커를 읽어 'Historical Context' + 연속성 규칙
   + 진행중-에이전트 경고를 additionalContext 로 주입한다. torch/모델을 절대 로드하지 않아
   세션 시작이 즉시(<0.3s) 끝난다. 복원할 게 없으면 조용히 종료(신규 세션 오염 방지).

stdin  : JSON {session_id, transcript_path, cwd, source, ...}
stdout : JSON {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                       "additionalContext": "..."}}
exit   : 항상 0. 어떤 실패든 graceful.
"""
import sys
import os
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

PER_HIT_CAP = 700        # 앵커 1개당 최대 글자
RESTORE_BLOCK_CAP = 4000  # 복원 블록 전체 최대 글자

CONTINUITY_RULES = """## 연속성 규칙 (컴팩션 이후 반드시 준수)
1. WHAT 보다 WHY 우선 — 결정과 그 근거가 표면적 디테일보다 중요하다.
2. 미해결 Blocker 는 명시적으로 해소될 때까지 계속 들고 간다.
3. Decision / Why / Rejected-alternatives 3종 세트를 깨뜨리지 않는다.
4. Findings 우선순위: P0(차단) > P1(중요) > P2(개선).
5. 사용자 지시를 조용히 누락하지 말 것 — 미처리 지시는 다시 표면화한다.
6. 이미 밟은 Pitfall 은 반복하지 않는다.
7. 일반론적 best practice 보다 사용자가 명시한 제약을 우선한다.
8. 작업 목록 상태를 유지 — 이미 완료한 일을 재시작하지 않는다.
9. 상태가 모호하면 추측하지 말고 모호함을 명시한다.
10. 복원된 메모리는 stale 할 수 있으니, 행동 전 현재 파일로 검증한다."""


def _read_input():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _emit(ctx=None):
    if ctx:
        out = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": ctx,
            }
        }
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.exit(0)


def _hit_text(h):
    if isinstance(h, dict):
        return str(h.get("memory") or h.get("text") or "")
    return str(h)


def main():
    inp = _read_input()
    cwd = inp.get("cwd") or os.getcwd()

    try:
        import mem_lib
    except Exception:
        _emit(None)
        return

    branch = mem_lib.git_branch(cwd)
    uid = mem_lib.project_user_id(cwd, branch)

    # Tier 1 캐시에서 최근 앵커를 즉시 읽는다(torch 미사용 → SessionStart 비블로킹).
    # 리슘 시엔 의미검색보다 '최신순'이 더 정확하다. 깊은 의미 회상은 mem0 on-demand 몫.
    try:
        hits = mem_lib.read_recent_cache(uid, n=6)
    except Exception:
        hits = []

    # 진행중 에이전트 플래그 확인 → 경고 생성 후 플래그 소멸
    flag = BASE / "post-compact-pending.flag"
    live_warn = ""
    if flag.exists():
        try:
            data = json.loads(flag.read_text(encoding="utf-8"))
            agents = data.get("agents", [])
            if agents:
                listed = "\n".join(f"  - {a}" for a in agents[:12])
                live_warn = (
                    "## ⚠️ 컴팩션 시점 진행중이던 에이전트/작업\n"
                    "아래 작업이 컴팩션 직전 in_progress 상태였다. 상태를 재확인하고, "
                    "완료 여부가 불확실하면 결과부터 검증하라(중복 실행 금지).\n"
                    + listed
                )
        except Exception:
            pass
        finally:
            try:
                flag.unlink()
            except Exception:
                pass

    # 복원할 내용이 전혀 없으면 신규 세션을 오염시키지 않도록 조용히 종료
    if not hits and not live_warn:
        _emit(None)
        return

    parts = ["# 🧠 Compaction-Resilient Memory (자동 복원)"]
    if live_warn:
        parts.append(live_warn)

    if hits:
        rendered, total = [], 0
        for h in hits:
            txt = _hit_text(h).strip()
            if not txt:
                continue
            if len(txt) > PER_HIT_CAP:
                txt = txt[:PER_HIT_CAP] + " …"
            block = "- " + txt.replace("\n", "\n  ")
            if total + len(block) > RESTORE_BLOCK_CAP:
                break
            rendered.append(block)
            total += len(block)
        if rendered:
            parts.append(
                f"## Historical Context (캐시 복원, branch={branch}, {len(rendered)}건)\n"
                + "\n".join(rendered)
            )

    parts.append(CONTINUITY_RULES)
    _emit("\n\n".join(parts))


if __name__ == "__main__":
    main()
