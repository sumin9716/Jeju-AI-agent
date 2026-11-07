@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ======================================================
echo  제주 데이터 길잡이 - 설치 (Windows)
echo ======================================================
where python >nul 2>nul
if errorlevel 1 (
  echo [오류] Python 이 설치되어 있지 않습니다. https://www.python.org/downloads/ 에서 3.10 이상을 설치하고
  echo        설치 화면에서 "Add python.exe to PATH" 를 체크한 뒤 다시 실행하세요.
  pause
  exit /b 1
)
if not exist .venv (
  echo [1/4] 가상환경(.venv) 생성...
  python -m venv .venv || (echo [오류] 가상환경 생성 실패 & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
echo [2/4] 패키지 설치 (처음에는 몇 분 걸릴 수 있습니다)...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || (echo [오류] 패키지 설치 실패. 인터넷 연결을 확인하세요. & pause & exit /b 1)
if not exist .env (
  echo [3/4] .env 파일 생성 (.env.example 복사)...
  copy .env.example .env >nul
  echo      -> .env 파일을 메모장으로 열어 ANTHROPIC_API_KEY 를 입력하세요.
) else (
  echo [3/4] .env 파일이 이미 있습니다.
)
echo [4/4] 환경 점검...
python -m jeju_agent.cli doctor --offline
echo.
echo 설치가 끝났습니다. run.bat 을 더블클릭하면 화면이 열립니다.
echo (바탕화면 바로가기: run.bat 을 마우스 오른쪽 클릭 - 보내기 - 바탕화면에 바로가기 만들기)
pause
