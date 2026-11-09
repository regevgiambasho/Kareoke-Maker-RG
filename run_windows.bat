@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found.
  echo Run setup_windows.ps1 first.
  pause
  exit /b 1
)

echo.
echo Starting Karaoke Maker...
echo Open in your browser: http://localhost:8501
echo.

rem launcher.py mounts the chunked-upload routes on the same port as the UI.
".venv\Scripts\python.exe" launcher.py
pause
