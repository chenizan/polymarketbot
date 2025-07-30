@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

if not exist ".venv\Scripts\polymarketbot.exe" (
  echo Bot is not installed yet.
  echo Run scripts\setup.bat first, then try again.
  pause
  exit /b 1
)

if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
)

echo.
echo  Starting PAPER trading ^(fake money^)...
echo  Press Ctrl+C to stop.
echo.
".venv\Scripts\polymarketbot.exe" run
echo.
pause
endlocal
