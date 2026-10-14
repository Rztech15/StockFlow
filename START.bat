@echo off
setlocal
title StockFlow - setup and start
cd /d "%~dp0backend"

where py >nul 2>&1 || (
  echo Python is not installed. Install Python 3.12 or newer from python.org
  echo and tick "Add python.exe to PATH", then double-click START.bat again.
  goto :fail
)
where docker >nul 2>&1 || (
  echo Docker Desktop is not installed. Install it from docker.com, open it,
  echo wait until it says "running", then double-click START.bat again.
  goto :fail
)

if not exist ".venv" (
  echo [1/6] Creating Python environment...
  py -3 -m venv .venv || goto :fail
)
call ".venv\Scripts\activate.bat"

echo [2/6] Installing packages (first time takes a few minutes)...
python -m pip install --quiet --upgrade pip || goto :fail
python -m pip install --quiet -e ".[dev]" || goto :fail

echo [3/6] Creating local settings (.env)...
python -m scripts.init_env || goto :fail

echo [4/6] Starting the database (Docker must be running)...
docker compose -f ..\docker-compose.yml up -d --wait db mailpit || goto :fail

echo [5/6] Creating database roles and tables...
python -m scripts.bootstrap_db || goto :fail
alembic upgrade head || goto :fail

echo [6/6] Starting StockFlow API. Keep this window open.
echo Health page: http://127.0.0.1:8000/health
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000/health"
uvicorn app.main:create_app --factory
goto :eof

:fail
echo.
echo ===== Something failed. Take a screenshot of this window and send it to Claude. =====
pause
exit /b 1
