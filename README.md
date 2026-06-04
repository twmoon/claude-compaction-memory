# claude-compaction-memory

Claude Code가 컨텍스트를 압축(compaction)하거나 세션을 닫으면 그동안 쌓인 맥락이 날아간다. 이걸 hook 두 개로 붙잡아 두는 도구다. 100% 로컬, API 키 필요 없음.

- **PreCompact** — 압축 직전에 최근 대화를 디스크에 적어둔다.
- **SessionStart** — 세션을 새로 켜거나 재개·압축할 때마다 적어둔 맥락을 자동으로 다시 넣어준다.

mem0만 쓰면 모델이 "메모리 조회를 깜빡하는" 문제가 있고, hook만 쓰면 세션 끝나면 사라진다. 둘을 합쳐서 양쪽 약점을 없앴다.

## 2계층 구조

세션 시작 같은 핫패스에서 임베딩 모델(torch)을 건드리면 매번 13초씩 멈춘다. 그래서 회상을 두 단으로 나눴다.

- **Tier 1 — 평문 JSONL 캐시.** 핫패스는 이것만 읽는다. torch를 안 건드려서 **0.08초**에 끝난다.
- **Tier 2 — mem0 + chroma 의미 검색.** torch 로드는 분리된 백그라운드 프로세스로 던져서 hook은 기다리지 않는다. 깊은 회상이 필요할 때만 쓴다.

세션 시작이 13.4초에서 0.08초로 줄었다(약 160배). 13.4초는 전부 임베딩 모델 로딩이었다.

## 설치

```bash
git clone https://github.com/twmoon/claude-compaction-memory.git
cd claude-compaction-memory
```

```powershell
.\install.ps1      # Windows
```
```bash
./install.sh       # Linux / macOS
```

격리된 `.venv`를 만들고 의존성을 깔고, 마지막에 `~/.claude/settings.json`에 붙여넣을 hook 스니펫(절대경로 포함)을 출력한다. 그걸 `"hooks"` 아래에 넣으면 끝.

> hook은 **Claude Code 세션을 재시작해야** 적용된다.

## 파일

| 파일 | 역할 |
|------|------|
| `mem_lib.py` | mem0 래퍼(무키) + 캐시 헬퍼 + 사일로 키 계산 |
| `save.py` | PreCompact hook — 캐시 저장 + 백그라운드 적재 |
| `restore.py` | SessionStart hook — 캐시 최근 6건 주입 |
| `ingest.py` | 분리 프로세스 Tier 2 워커 |
| `braindump.py` | 수동 저장 ("지금 이거 기억해") |

## 사일로

회상은 **작업 폴더 경로**로 나뉜다. basename이 아니라 전체 경로라서 `ctf-a/web1`과 `ctf-b/web1`이 섞이지 않는다. 상위 폴더에 빈 `.memroot`를 두면 그 아래는 한 사일로로 묶인다.

## 알아둘 것

- **핫패스에서 mem0를 직접 부르지 말 것.** `get_memory()`가 torch를 끌어와서 13초가 걸린다. 새 hook을 붙일 땐 캐시만 읽고, mem0 적재는 `spawn_ingest`로 백그라운드에 던져라.
- **mem0 2.0.4 주의:** `search()`/`get_all()`에 `user_id`를 직접 넘기면 안 되고 `filters={"user_id": ...}`로 줘야 한다. 안 그러면 조용히 0건이 나온다.
- **venv는 `--system-site-packages` 금지.** 시스템/anaconda의 옛 numpy ABI가 새어 들어오면 `dtype size 96 vs 88`로 터진다. install 스크립트가 완전 격리 + CPU torch로 깔아준다.

## 라이선스

MIT
