# claude-compaction-memory

Claude Code 의 **컴팩션(context compaction)** 과 **세션 종료**를 가로질러 대화의 맥락을 살아남게 하는, 100% 로컬·무키(no API key) 메모리 시스템.

Claude Code 는 컨텍스트가 길어지면 자동으로 대화를 요약(compaction)하고, 세션을 닫으면 그 맥락이 사라진다. 이 프로젝트는 두 개의 hook 으로 그 틈을 메운다:

- **`PreCompact`** — 컴팩션 직전에 대화의 최근 델타를 디스크에 저장한다.
- **`SessionStart`** — 세션이 (새로) 시작·재개·컴팩션될 때마다 저장해 둔 맥락을 `additionalContext` 로 자동 주입한다.

모델이 "메모리를 조회하는 걸 잊어버리는" 문제도, hook 단독의 "세션이 끝나면 소실되는" 문제도 없다 — hook 자동 주입과 영속 벡터 백엔드를 결합했기 때문이다.

---

## 핵심: 2계층 아키텍처 (핫패스는 torch 를 절대 로드하지 않는다)

세션 시작/컴팩션 같은 **핫패스**에서 무거운 임베딩 모델(torch, ~13초 로드)을 건드리면 세션이 매번 버벅인다. 그래서 회상을 두 계층으로 쪼갰다.

### Tier 1 — recency 캐시 (torch-free)
- `cache/{silo}.jsonl` — 평문 JSONL append-only.
- `save.py` 가 컴팩션 델타를 즉시 append, `restore.py` 가 최신순 n=6건을 읽어 주입.
- **핫패스는 오직 이것만 본다.** 측정 지연 **~0.08초**.

### Tier 2 — mem0 의미검색 (비동기, 분리 프로세스)
- chroma(로컬 sqlite 벡터 DB) + HuggingFace `all-MiniLM-L6-v2` 임베딩, OpenAI 키 불필요.
- `save.py` 는 `ingest.py` 를 **분리(detached) 프로세스**로 던지고 즉시 반환한다. torch 로드 ~13초는 부모(hook)와 무관하게 이 워커 안에서만 발생.
- 깊은 의미 회상이 필요할 때 on-demand 로 사용.

> **개편 효과: 세션 시작 13.4초 → 0.08초 (~160배).** 13.4초는 전부 임베딩 모델 로드였다.

---

## 동작이 검증되었는가? (production-level e2e)

"컴팩션 후 모델이 마커를 기억하더라"는 관찰만으로는 **이 도구의 기여를 증명하지 못한다.** 마커는 세 경로로 동시에 살아남기 때문이다:

- **(A)** Claude Code 의 **네이티브 컴팩션 요약** — 컴팩션 프롬프트가 *"모든 user 메시지를 verbatim 보존"* 하도록 강제한다. 그래서 user 메시지에 심은 마커는 이 도구가 없어도 100% 살아남는다(심지어 짧은 대화에선 assistant 출력의 우연한 hex 토큰까지 보존될 만큼 충실).
- **(B)** 이 도구의 `restore.py` SessionStart 주입.
- **(C)** 모델이 자체 메모리 파일에 적어두는 경로.

즉 컴팩션 리콜 테스트는 본질적으로 **confounded** 다. (B)의 인과 기여를 깨끗이 분리하려면 (A)가 **존재하지 않는** 상황을 써야 한다 — 바로 **새 세션(fresh startup)** 이다. fresh start 에는 네이티브 요약이 없으므로, 새 세션이 "아무도 알려주지 않은 마커"를 리콜하면 그건 `restore.py` 단독의 기여다.

**검증 결과(2026-06-04):** 캐시에 마커(`QX7-…`, `RUN-…`)를 적재한 사일로에서 fresh 세션을 띄우자 `compactSummary=0`인데도 두 마커를 정확히 리콜하고, 출처를 스스로 *"SessionStart hook 으로 복원된 Historical Context"* 라고 명시했다. 빈 사일로 음성대조에서는 `restore.py` 가 **0바이트**를 출력(무주입 → 리콜 불가). 따라서 *리콜 ⟺ restore 가 전달한 캐시* 로 인과가 격리된다.

---

## 설치

```bash
git clone https://github.com/twmoon/claude-compaction-memory.git
cd claude-compaction-memory
```

**Windows (PowerShell):**
```powershell
.\install.ps1
```

**Linux / macOS:**
```bash
./install.sh
```

설치 스크립트는 ① 격리된 `.venv` 생성 ② 고정 의존성 설치(CPU torch 포함) ③ 임베딩 모델 사전 다운로드 ④ `settings.json` 에 붙여넣을 hook 스니펫 출력 을 수행한다.

마지막으로 출력된 두 hook 을 `~/.claude/settings.json` 의 `"hooks"` 아래에 병합하면 된다(스크립트가 절대경로까지 채워 출력한다). 예시:

```json
{
  "hooks": {
    "PreCompact": [
      { "matcher": "manual|auto",
        "hooks": [ { "type": "command", "timeout": 45, "command": "<DIR>/.venv/.../python save.py" } ] }
    ],
    "SessionStart": [
      { "matcher": "startup|resume|compact",
        "hooks": [ { "type": "command", "timeout": 45, "command": "<DIR>/.venv/.../python restore.py" } ] }
    ]
  }
}
```

> **hook 은 Claude Code 세션을 재시작해야 적용된다.** (settings.json 저장만으로는 실행 중 세션에 반영되지 않음.)

---

## 구성 요소

| 파일 | 역할 |
|------|------|
| `mem_lib.py` | 공통 래퍼 — mem0(HF+chroma, `infer=False`, 무키), Tier 1 캐시 헬퍼(`append_cache`/`read_recent_cache`), `spawn_ingest`(OS별 분리 프로세스), `extract_delta`(transcript 파싱), 사일로 키 계산 |
| `save.py` | **PreCompact** hook — 델타 추출 → 캐시 append + `spawn_ingest`. 컴팩션을 **절대 차단하지 않는다.** |
| `restore.py` | **SessionStart** hook(`startup\|resume\|compact`) — 캐시 최신 n건을 `additionalContext` 로 주입. 회상거리 없으면 침묵 |
| `ingest.py` | 분리된 Tier 2 워커 — temp 파일 인자 → `add_anchor` → unlink |
| `braindump.py` | 수동 영속화 — 현재 맥락을 같은 2계층으로 직접 저장 |

---

## 사일로(silo) 격리 모델

회상은 **작업 폴더 경로**로 격리된다. 키 = `proj:{홈 기준 상대경로(슬래시 정규화·소문자)}:{branch}`.

- 예: `~/work/ctf/web1` → `proj:work/ctf/web1:HEAD`.
- **basename 이 아니라 전체 경로**를 쓴다. 그래서 `ctf-a/web1` 과 `ctf-b/web1` 이 같은 `proj:web1` 로 **조용히 병합되는** 사고가 없다.
- **`.memroot` 마커(opt-in):** 상위 폴더에 빈 `.memroot` 파일을 두면 그 폴더 아래 어디서 띄워도 한 사일로로 묶인다. 홈 디렉터리에 도달하면 무시한다(홈 아래 전체 병합 방지). `.git`/`.omc` 는 우연히 상위에 있을 수 있어 마커로 쓰지 않는다.

---

## 수동 brain dump

자동 컴팩션을 기다리지 않고 "지금 이 맥락을 기억해" 하고 싶을 때:

```bash
.venv/.../python braindump.py <dump_text_file> [cwd]
```

캐시 append + Tier 2 비동기 적재를 동일하게 수행한다. (원 저장소에서는 `/+` 슬래시 커맨드로 래핑해 쓴다.)

---

## 비자명 함정 (운영 노트)

- **핫패스에서 `get_memory()`/`add_anchor`/`search_anchors` 호출 금지.** 이것이 torch 를 로드하는 유일한 경로(~13초)다. `import mem_lib` 자체는 ~0.07초라 무해. 새 hook 을 추가할 땐 Tier 1 캐시만 쓰고 mem0 는 `spawn_ingest` 로 비동기화할 것.
- **mem0 2.0.4 API:** `search()`/`get_all()` 에 `user_id` 를 top-level 인자로 주면 안 된다 → 반드시 `filters={"user_id": ...}`. (`add()` 만 top-level `user_id` 허용.) 어기면 `ValueError` 가 try/except 에 삼켜져 "0 hits" 로 조용히 실패한다.
- **venv 격리 필수:** `.venv` 를 `--system-site-packages` 로 만들지 말 것. 시스템/anaconda 의 구버전 sklearn/numpy ABI 가 누출되면 `dtype size 96 vs 88` 충돌이 난다. install 스크립트는 완전 격리 + CPU torch 로 빌드한다.
- **활성화 타이밍:** hook 등록은 settings.json 저장 즉시가 아니라 **세션 재시작** 후 적용된다.
- **`extract_delta` 윈도우:** user 문자열 메시지 마지막 10개 + 마지막 assistant 텍스트 2500자만 캐시에 담는다(마지막 컴팩션 마커 이후 구간 기준). 마커가 PreCompact 시점에 이 최근 창 안에 있어야 살아남는다. 또 restore 의 per-hit 700자 절단이 있어 델타 뒤쪽 토큰은 잘릴 수 있다.

---

## 측정 지연 (2계층 적용 후)

| 동작 | 지연 |
|------|------|
| restore (콜드/빈 캐시) | 0.075s |
| restore (데이터 있음) | 0.080s |
| save (PreCompact) | 0.084s |

---

## 라이선스

[MIT](./LICENSE)
