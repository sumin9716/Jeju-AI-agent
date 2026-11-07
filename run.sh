#!/usr/bin/env bash
# 제주 데이터 길잡이 - 실행 (macOS / Linux)
cd "$(dirname "$0")"
if [ ! -f .venv/bin/activate ]; then echo "먼저 ./setup.sh 를 실행해 설치하세요."; exit 1; fi
# shellcheck disable=SC1091
source .venv/bin/activate
exec python -m streamlit run app.py "$@"
