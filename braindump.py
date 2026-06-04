#!/usr/bin/env python
"""Manual Brain Dump: 현재 세션의 구조화 요약을 mem0에 즉시 영속화.

PreCompact(save.py)는 컴팩션 시점에 자동 저장하지만, Brain Dump 는 사용자가 원할 때
풍부한 9섹션 요약을 '강제로' 영속화하는 수동 트리거다.

usage: braindump.py <dump_text_file> [cwd]
  <dump_text_file> : 9섹션 마크다운 요약이 담긴 파일 경로
  [cwd]            : 프로젝트 디렉터리(uid 산출용). 생략 시 현재 작업 디렉터리.
"""
import sys
import os
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))


def main():
    if len(sys.argv) < 2:
        print("usage: braindump.py <dump_text_file> [cwd]")
        sys.exit(2)
    src = sys.argv[1]
    cwd = sys.argv[2] if len(sys.argv) > 2 else os.getcwd()

    try:
        text = Path(src).read_text(encoding="utf-8").strip()
    except Exception as e:
        print(f"[braindump] 파일 읽기 실패: {e}")
        sys.exit(1)
    if not text:
        print("[braindump] 빈 dump — 저장 생략")
        sys.exit(0)

    import mem_lib
    branch = mem_lib.git_branch(cwd)
    uid = mem_lib.project_user_id(cwd, branch)
    ts = int(time.time())
    # Tier 1(즉시): restore 가 읽는 recency 캐시 — 이게 저장의 source of truth.
    mem_lib.append_cache(uid, "brain-dump", text, ts)
    # Tier 2(비동기): mem0 의미검색 색인 — 분리 프로세스로 던져 즉시 반환.
    mem_lib.spawn_ingest(uid, "brain-dump", branch, text, ts)
    print(f"[braindump] {branch}: {len(text)}자 캐시 적재 + mem0 비동기 색인 (uid={uid})")


if __name__ == "__main__":
    main()
