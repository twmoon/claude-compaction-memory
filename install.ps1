#requires -Version 5
# Compaction-resilient memory 설치 (Windows / PowerShell)
# 격리된 venv 를 만들고 의존성을 고정 설치한 뒤, settings.json 에 붙여넣을 hook 을 출력한다.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host "[1/4] 격리된 venv 생성 (.venv) ..."
# 중요: --system-site-packages 를 절대 쓰지 말 것.
# anaconda base 의 구버전 sklearn/numpy ABI 가 누출되면 'dtype size 96 vs 88' 충돌이 난다.
python -m venv .venv
$venvPy = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { throw "venv 생성 실패: $venvPy 없음" }

Write-Host "[2/4] pip 업그레이드 ..."
& $venvPy -m pip install --upgrade pip | Out-Host

Write-Host "[3/4] 의존성 설치 (CPU torch 포함, 수 분 소요) ..."
& $venvPy -m pip install -r requirements.txt | Out-Host

Write-Host "[4/4] 임베딩 모델 사전 다운로드 (all-MiniLM-L6-v2) ..."
& $venvPy -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')" | Out-Host

$cmdSave    = "`"$venvPy`" `"$here\save.py`""
$cmdRestore = "`"$venvPy`" `"$here\restore.py`""

Write-Host ""
Write-Host "설치 완료." -ForegroundColor Green
Write-Host "아래 두 hook 을  ~/.claude/settings.json  의 `"hooks`" 아래에 병합하세요:"
Write-Host ""
$snippet = @"
{
  "hooks": {
    "PreCompact": [
      { "matcher": "manual|auto",
        "hooks": [ { "type": "command", "timeout": 45, "command": "$($cmdSave -replace '\\','\\')" } ] }
    ],
    "SessionStart": [
      { "matcher": "startup|resume|compact",
        "hooks": [ { "type": "command", "timeout": 45, "command": "$($cmdRestore -replace '\\','\\')" } ] }
    ]
  }
}
"@
Write-Host $snippet
Write-Host ""
Write-Host "※ hook 은 Claude Code 세션을 '재시작'해야 적용됩니다."
