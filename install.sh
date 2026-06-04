#!/usr/bin/env bash
# Compaction-resilient memory 설치 (Linux / macOS)
# 격리된 venv 를 만들고 의존성을 고정 설치한 뒤, settings.json 에 붙여넣을 hook 을 출력한다.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"

PY="${PYTHON:-python3}"

echo "[1/4] 격리된 venv 생성 (.venv) ..."
# 중요: --system-site-packages 를 절대 쓰지 말 것.
# 시스템/anaconda 의 구버전 sklearn/numpy ABI 가 누출되면 'dtype size 96 vs 88' 충돌이 난다.
"$PY" -m venv .venv
venvpy="$here/.venv/bin/python"
[ -x "$venvpy" ] || { echo "venv 생성 실패: $venvpy 없음" >&2; exit 1; }

echo "[2/4] pip 업그레이드 ..."
"$venvpy" -m pip install --upgrade pip

echo "[3/4] 의존성 설치 (CPU torch 포함, 수 분 소요) ..."
req="requirements.txt"
if [ "$(uname -s)" = "Darwin" ]; then
  # macOS 휠에는 +cpu 접미사가 없다 → 임시 requirements 로 치환.
  req="$(mktemp -t reqs.XXXXXX)"
  sed 's/torch==2.12.0+cpu/torch==2.12.0/' requirements.txt > "$req"
fi
"$venvpy" -m pip install -r "$req"
[ "$req" != "requirements.txt" ] && rm -f "$req" || true

echo "[4/4] 임베딩 모델 사전 다운로드 (all-MiniLM-L6-v2) ..."
"$venvpy" -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

cat <<EOF

설치 완료.
아래 두 hook 을  ~/.claude/settings.json  의 "hooks" 아래에 병합하세요:

{
  "hooks": {
    "PreCompact": [
      { "matcher": "manual|auto",
        "hooks": [ { "type": "command", "timeout": 45, "command": "$venvpy $here/save.py" } ] }
    ],
    "SessionStart": [
      { "matcher": "startup|resume|compact",
        "hooks": [ { "type": "command", "timeout": 45, "command": "$venvpy $here/restore.py" } ] }
    ]
  }
}

※ hook 은 Claude Code 세션을 '재시작'해야 적용됩니다.
EOF
