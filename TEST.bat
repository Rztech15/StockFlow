@echo off
setlocal
title StockFlow - run checks
cd /d "%~dp0backend"
if not exist ".venv\Scripts\activate.bat" (
  echo Run START.bat once first.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
echo === 1. code check ===
ruff check . || goto :fail
echo === 2. unit tests ===
python -m pytest -m "not db" -q || goto :fail
echo === 3. database tests ===
python -m pytest -m "db and not rls and not guard" -q || goto :fail
echo === 4. tenant isolation tests ===
python -m pytest -m rls -q || goto :fail
echo === 5. RLS guard ===
python -m pytest -m guard -q || goto :fail
echo.
echo ===== ALL CHECKS PASSED =====
pause
exit /b 0
:fail
echo.
echo ===== A check failed. Take a screenshot of this window and send it to Claude. =====
pause
exit /b 1
