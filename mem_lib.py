"""Compaction-resilient memory: mem0 wrapper (HuggingFace embed + chroma, keyless, infer=False).

이 모듈은 mem0를 OpenAI 키 없이 100% 로컬로 구동한다:
  - embedder : sentence-transformers/all-MiniLM-L6-v2 (로컬, 무키)
  - vector_store : chroma (로컬 파일 DB, 데몬 불필요)
  - llm : infer=False 로 미사용 (사실 추출은 hook의 extract_delta 가 수행)
hook 스크립트(save.py / restore.py)에서 import 한다. 전용 venv 인터프리터로 실행됨.
"""
import os
import re
import sys
import json
import time
import glob
import hashlib
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
STORE = str(BASE / "store")
CACHE_DIR = BASE / "cache"
try:
    HOME = Path.home().resolve()
except Exception:
    HOME = None

# mem0 의 Memory.from_config 가 default LLM(openai) 클라이언트를 '생성'할 때 키를 요구할 수
# 있으나, 우리는 add(infer=False)/search 만 호출하므로 실제 OpenAI 호출은 절대 발생하지 않는다.
# client 생성만 통과시키기 위한 no-op 키.
os.environ.setdefault("OPENAI_API_KEY", "sk-local-noop")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

COLLECTION = "cc_compaction_anchors"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_MEM = None


def get_memory():
    """mem0 Memory 싱글톤. 첫 호출 시 임베딩 모델을 로드(최초 1회 다운로드)."""
    global _MEM
    if _MEM is None:
        from mem0 import Memory
        cfg = {
            "embedder": {
                "provider": "huggingface",
                "config": {"model": EMBED_MODEL},
            },
            "vector_store": {
                "provider": "chroma",
                "config": {"collection_name": COLLECTION, "path": STORE},
            },
        }
        _MEM = Memory.from_config(cfg)
    return _MEM


# ---------------------------------------------------------------- git / id

def git_branch(cwd=None):
    try:
        r = subprocess.run(["git", "branch", "--show-current"], cwd=cwd,
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() or "HEAD"
    except Exception:
        return "HEAD"


def git_log(cwd=None, n=5):
    try:
        r = subprocess.run(["git", "log", "--oneline", "-n", str(n)], cwd=cwd,
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip()
    except Exception:
        return ""


def _rel_key(path):
    """폴더 경로를 격리 키 문자열로. 홈 기준 상대경로(슬래시 정규화, 소문자).
    홈 밖이면 드라이브 포함 절대경로를 쓴다(드라이브 간 충돌 방지).
    basename 이 아닌 '전체 경로'를 쓰므로 wctf/web1 과 defcon/web1 이 절대 안 겹친다.
    """
    try:
        p = Path(path).resolve()
    except Exception:
        p = Path(path)
    s = p.as_posix()
    hs = HOME.as_posix() if HOME else ""
    # 홈 접두 제거(Windows 대소문자 무시)
    if hs and s.lower().startswith(hs.lower() + "/"):
        s = s[len(hs):]
    elif hs and s.lower() == hs.lower():
        s = ""
    return s.strip("/").lower() or "root"


def _find_memroot(cwd):
    """cwd 에서 상위로 올라가며 사용자가 '의도적으로' 둔 .memroot 파일을 찾는다.
    여러 하위 폴더를 한 프로젝트로 묶기 위한 명시적 마커. 홈 디렉터리에 도달하면 중단해
    '홈 아래 전체가 한 사일로로 병합'되는 사고를 막는다. (.git/.omc 는 우연히 상위에
    존재할 수 있어 마커로 쓰지 않는다.) 없으면 None."""
    try:
        cur = Path(cwd).resolve()
    except Exception:
        return None
    hs = HOME.as_posix().lower() if HOME else None
    while True:
        if hs and cur.as_posix().lower() == hs:
            return None  # 홈 도달 → 마커 무시(전체 병합 방지)
        try:
            if (cur / ".memroot").exists():
                return cur
        except Exception:
            pass
        parent = cur.parent
        if parent == cur:  # 드라이브 루트
            return None
        cur = parent


def project_user_id(cwd=None, branch=None):
    """메모리 격리 키 = 폴더 '경로' 기반(홈 기준 상대경로) + 브랜치.
    basename 충돌(wctf/web1 vs defcon/web1)과 깊이 단편화를 막는다. 상위에 .memroot
    가 있으면 그 폴더로 묶어, 하위 어디서 띄워도 같은 사일로가 된다."""
    cwd = cwd or os.getcwd()
    root = _find_memroot(cwd)
    key = _rel_key(root if root is not None else cwd)
    if branch is None:
        branch = git_branch(cwd)
    return f"proj:{key}:{branch}"


# ---------------------------------------------------------------- mem0 ops

def add_anchor(text, user_id, metadata=None):
    if not text or not text.strip():
        return None
    m = get_memory()
    return m.add(text, user_id=user_id, metadata=metadata or {}, infer=False)


def search_anchors(query, user_id, limit=8):
    """mem0 2.0.x: user_id 는 top-level 인자가 아니라 filters 로 넘겨야 한다."""
    m = get_memory()
    res = None
    try:
        res = m.search(query, filters={"user_id": user_id}, limit=limit)
    except TypeError:
        # 구버전 호환(혹시 filters 미지원이면 top-level 시도)
        try:
            res = m.search(query, user_id=user_id, limit=limit)
        except Exception:
            return []
    except Exception:
        return []
    if isinstance(res, dict):
        return res.get("results", []) or []
    return res or []


# ---------------------------------------------------------------- recency cache
# Tier 1: torch 없이 즉시 읽고 쓰는 평문 JSONL 캐시. SessionStart 핫패스가 이것만 읽어
# torch/모델 로드(~13s)를 회피한다. mem0(Tier 2)는 장기 의미검색용으로 비동기 적재된다.

def _cache_path(user_id):
    # 가독 prefix + 전체 uid 해시 suffix → 슬래시→_ 치환 충돌(a/b vs a_b)까지 제거.
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", user_id) or "root"
    if len(safe) > 100:
        safe = safe[:100]
    h = hashlib.sha1(user_id.encode("utf-8")).hexdigest()[:10]
    return CACHE_DIR / f"{safe}_{h}.jsonl"


def append_cache(user_id, kind, text, ts=None, max_lines=200):
    """앵커 1건을 per-uid 캐시에 추가(append). 실패해도 조용히 넘어간다."""
    if not text or not text.strip():
        return
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = _cache_path(user_id)
        entry = {"ts": ts or int(time.time()), "kind": kind, "text": text.strip()}
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        # 크기 제한: 마지막 max_lines 줄만 유지
        lines = p.read_text(encoding="utf-8").splitlines()
        if len(lines) > max_lines:
            p.write_text("\n".join(lines[-max_lines:]) + "\n", encoding="utf-8")
    except Exception:
        return


def read_recent_cache(user_id, n=6):
    """최신순으로 최근 n건을 반환(torch 미사용). 없으면 []"""
    p = _cache_path(user_id)
    if not p.exists():
        return []
    try:
        lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        return []
    out = []
    for l in lines[-n:]:
        try:
            out.append(json.loads(l))
        except Exception:
            continue
    out.reverse()  # 최신이 먼저
    return out


def spawn_ingest(user_id, kind, branch, text, ts=None):
    """mem0(Tier 2) 적재를 분리(detached) pythonw 프로세스로 던진다 — 호출부 비블로킹.

    torch+모델 로드(~13s)는 자식 ingest.py 에서만 발생한다. 실패해도 무해(restore 는
    Tier 1 캐시를 읽으므로). Windows 는 DETACHED 플래그, POSIX 는 start_new_session 으로
    부모(hook) 종료와 무관하게 살아남는다.
    """
    if not text or not text.strip():
        return
    ts = ts or int(time.time())
    try:
        tf = BASE / f"_ingest_{ts}_{os.getpid()}.txt"
        tf.write_text(text, encoding="utf-8")
        if os.name == "nt":
            py = BASE / ".venv" / "Scripts" / "pythonw.exe"
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            flags = 0x00000008 | 0x00000200 | 0x08000000
            subprocess.Popen(
                [str(py), str(BASE / "ingest.py"), user_id, kind, branch, str(ts), str(tf)],
                creationflags=flags, close_fds=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            py = BASE / ".venv" / "bin" / "python"
            # POSIX: 새 세션으로 분리해 부모(hook) 종료와 무관하게 살아남게 한다.
            subprocess.Popen(
                [str(py), str(BASE / "ingest.py"), user_id, kind, branch, str(ts), str(tf)],
                start_new_session=True, close_fds=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass


# ---------------------------------------------------------------- transcript

COMPACT_MARKER = "This session is being continued"


def _iter_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def extract_delta(transcript_path, max_chars=6000):
    """마지막 컴팩션 마커 이후의 user 지시 + 마지막 assistant 텍스트를 LLM 없이 추출.

    user 메시지(content=str)는 사용자 의도/결정의 근거라 최우선 보존한다.
    tool_result(user role, content=list)와 컴팩션 마커, 시스템 태그(<...>)는 제외.
    """
    objs = list(_iter_jsonl(transcript_path))
    start = 0
    for i, o in enumerate(objs):
        if o.get("type") == "user":
            c = (o.get("message") or {}).get("content")
            if isinstance(c, str) and c.startswith(COMPACT_MARKER):
                start = i
    users, last_asst = [], ""
    for o in objs[start:]:
        t = o.get("type")
        c = (o.get("message") or {}).get("content")
        if t == "user" and isinstance(c, str):
            s = c.strip()
            if s and not s.startswith(COMPACT_MARKER) and not s.startswith("<"):
                users.append(s)
        elif t == "assistant" and isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                    last_asst = b["text"].strip()
    parts = []
    if users:
        parts.append("## User instructions (recent)\n" + "\n---\n".join(users[-10:]))
    if last_asst:
        parts.append("## Last assistant summary\n" + last_asst[:2500])
    return "\n\n".join(parts).strip()[:max_chars]
