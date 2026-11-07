@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ======================================================
echo  제주 데이터 길잡이 - 설치 Windows
echo ======================================================

rem where python 은 Microsoft Store 바로가기도 찾으므로, 실제로 실행해 3.10 이상인지 확인한다.
set "PY="
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
if not defined PY (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  echo [오류] Python 3.10 이상을 찾지 못했습니다.
  echo        https://www.python.org/downloads/ 에서 설치하고, 설치 첫 화면의 "Add python.exe to PATH" 를 체크하세요.
  echo        설치 후 명령 프롬프트를 새로 열어 setup.bat 을 다시 실행하세요.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] 가상환경 .venv 를 만듭니다...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [오류] 가상환경을 만들지 못했습니다.
    pause
    exit /b 1
  )
)
set "VPY=%~dp0.venv\Scripts\python.exe"

echo [2/4] 패키지를 설치합니다. 처음에는 몇 분 걸릴 수 있습니다...
"%VPY%" -m pip install --upgrade pip >nul
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [오류] 패키지 설치에 실패했습니다. 인터넷 연결을 확인하세요.
  pause
  exit /b 1
)

if not exist .env (
  echo [3/4] .env.example 을 복사해 .env 파일을 만듭니다.
  copy .env.example .env >nul
  echo        이제 .env 파일을 메모장으로 열어 ANTHROPIC_API_KEY 를 입력하세요.
) else (
  echo [3/4] .env 파일이 이미 있습니다.
)

echo [4/4] 환경을 점검합니다...
"%VPY%" -m jeju_agent.cli doctor --offline
echo.
echo 설치가 끝났습니다. run.bat 을 더블클릭하면 화면이 열립니다.
echo 바탕화면 바로가기: run.bat 을 마우스 오른쪽 클릭, 보내기, 바탕 화면에 바로 가기 만들기
pause
