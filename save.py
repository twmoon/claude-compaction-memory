#!/usr/bin/env python
"""PreCompact hook: 컴팩션 직전 대화 델타를 mem0에 영속화 + 진행중 에이전트 플래그.

stdin  : JSON hook input {session_id, transcript_path, cwd, trigger, ...}
stdout : JSON {"systemMessage": "..."} (투명성용). 절대 컴팩션을 block 하지 않는다.
exit   : 항상 0. 어떤 실패든 graceful — 컴팩션 자체를 막으면 안 된다.

실제 복원 주입은 restore.py(SessionStart)가 담당한다. 여기서는 '저장'만 한다.
"""
import sys
import os
import json
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))


def _read_input():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _emit(system_message=None):
    out = {}
    if system_message:
        out["systemMessage"] = system_message
    if out:
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.exit(0)


def _detect_live_agents():
    """best-effort: 알려진 task 디렉터리에서 status=='in_progress' 항목 수집."""
    names = []
    home = Path.home()
    for d in (home / ".claude" / "tasks",):
        if not d.exists():
            continue
        for jf in d.rglob("*.json"):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict):
                items = data.get("tasks", [])
            elif isinstance(data, list):
                items = data
            else:
                items = []
            for it in items:
                if isinstance(it, dict) and it.get("status") == "in_progress":
                    nm = it.get("subject") or it.get("owner") or it.get("id")
                    if nm:
                        names.append(str(nm))
    return names


def main():
    inp = _read_input()
    transcript = inp.get("transcript_path") or ""
    cwd = inp.get("cwd") or os.getcwd()
    trigger = inp.get("trigger") or "?"

    try:
        import mem_lib
    except Exception as e:
        _emit(f"[compaction-memory] mem_lib import 실패(컴팩션은 계속): {e}")
        return

    branch = mem_lib.git_branch(cwd)
    uid = mem_lib.project_user_id(cwd, branch)

    delta = ""
    if transcript and os.path.exists(transcript):
        try:
            delta = mem_lib.extract_delta(transcript)
        except Exception:
            delta = ""

    saved = 0
    if delta:
        ts = int(time.time())
        # Tier 1(즉시·torch-free): restore 가 읽는 recency 캐시
        mem_lib.append_cache(uid, "compaction-delta", delta, ts)
        # Tier 2(비동기): mem0 의미검색 저장소 — 분리 프로세스로 던져 컴팩션 비블로킹
        mem_lib.spawn_ingest(uid, "compaction-delta", branch, delta, ts)
        saved = len(delta)

    live = _detect_live_agents()
    flag = BASE / "post-compact-pending.flag"
    if live:
        try:
            flag.write_text(
                json.dumps({"agents": live, "ts": int(time.time()), "branch": branch},
                           ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    msg = f"[compaction-memory] {branch}: 델타 {saved}자 캐시 적재 + mem0 비동기 색인"
    if live:
        msg += f" · 진행중 에이전트 {len(live)}개 플래그"
    _emit(msg)


if __name__ == "__main__":
    main()
