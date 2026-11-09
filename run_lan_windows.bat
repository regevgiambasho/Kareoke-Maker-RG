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
echo Starting Karaoke Maker for your local network...
echo On your Android phone, open:
echo http://YOUR-PC-IP:8501
echo.
echo To find YOUR-PC-IP, run: ipconfig
echo Look for IPv4 Address on your Wi-Fi adapter.
echo.

rem launcher.py listens on 0.0.0.0:8501 and mounts the chunked-upload routes.
".venv\Scripts\python.exe" launcher.py
pause
