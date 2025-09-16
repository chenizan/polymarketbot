@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

echo.
echo  ========================================
echo   Polymarket Bot - One-Click Setup
echo  ========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python was not found.
  echo.
  echo 1^) Install Python 3.11+ from https://www.python.org/downloads/
  echo 2^) IMPORTANT: check "Add python.exe to PATH"
  echo 3^) Close this window and run scripts\setup.bat again
  echo.
  pause
  exit /b 1
)

echo [1/4] Creating virtual environment...
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Could not create .venv
    pause
    exit /b 1
  )
)

echo [2/4] Installing the bot...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\pip.exe" install -e .
if errorlevel 1 (
  echo [ERROR] Install failed. Scroll up for details.
  pause
  exit /b 1
)

echo [3/4] Creating folders + .env...
if not exist "data" mkdir data
if not exist "logs" mkdir logs
if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  echo       Created .env from .env.example
) else (
  echo       .env already exists
)

echo [4/4] Easy setup wizard...
echo.
".venv\Scripts\polymarketbot.exe" setup
set ERR=%ERRORLEVEL%

echo.
echo  Next steps:
echo    - Paper trade:   scripts\run-paper.bat
echo    - Fill .env:     polymarketbot setup-env
echo.
if not "%ERR%"=="0" (
  echo Setup finished with warnings.
  pause
  exit /b %ERR%
)
echo  SUCCESS
pause
endlocal

