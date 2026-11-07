#!/usr/bin/env bash
# 제주 데이터 길잡이 - 설치 (macOS / Linux)
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "[오류] python3 이 없습니다. Python 3.10 이상을 설치하세요."; exit 1
fi
if [ ! -d .venv ]; then
  echo "[1/4] 가상환경(.venv) 생성..."; "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "[2/4] 패키지 설치..."; python -m pip install --upgrade pip >/dev/null; python -m pip install -r requirements.txt
if [ ! -f .env ]; then
  echo "[3/4] .env 생성 (.env.example 복사) → ANTHROPIC_API_KEY 를 입력하세요."; cp .env.example .env
else
  echo "[3/4] .env 파일이 이미 있습니다."
fi
echo "[4/4] 환경 점검..."; python -m jeju_agent.cli doctor --offline
echo; echo "설치가 끝났습니다. ./run.sh 로 시작하세요."
