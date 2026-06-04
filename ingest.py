#!/usr/bin/env python
"""Detached ingest worker: mem0(Tier 2 의미검색 저장소)에 앵커 1건 비동기 영속화.

save.py / braindump.py 가 pythonw.exe 로 DETACHED 스폰한다. torch+임베딩 모델 로드
(~13s)는 오직 이 분리 프로세스에서만 발생하므로, hook 핫패스(SessionStart/PreCompact)는
절대 블로킹되지 않는다. 실패해도 영향 없음 — restore 는 Tier 1 캐시를 읽기 때문이다.

usage: ingest.py <uid> <kind> <branch> <ts> <textfile>
  textfile 은 처리 후 삭제된다.
"""
import sys
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))


def main():
    if len(sys.argv) < 6:
        sys.exit(2)
    uid, kind, branch, ts, textfile = sys.argv[1:6]
    try:
        text = Path(textfile).read_text(encoding="utf-8")
    except Exception:
        sys.exit(1)
    try:
        import mem_lib
        meta = {"kind": kind, "branch": branch}
        try:
            meta["ts"] = int(ts)
        except Exception:
            pass
        mem_lib.add_anchor(text, uid, metadata=meta)
    except Exception:
        # mem0/chroma 실패는 무해 — Tier 1 캐시가 복원을 책임진다.
        pass
    finally:
        try:
            os.unlink(textfile)
        except Exception:
            pass


if __name__ == "__main__":
    main()
