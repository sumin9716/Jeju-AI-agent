@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 먼저 setup.bat 을 실행해 설치하세요.
  pause
  exit /b 1
)
echo 제주 데이터 길잡이를 시작합니다. 브라우저가 자동으로 열립니다. 끝내려면 이 창을 닫으세요.
".venv\Scripts\python.exe" -m streamlit run app.py
pause
