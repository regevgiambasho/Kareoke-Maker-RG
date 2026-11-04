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

".venv\Scripts\python.exe" -m streamlit run app.py ^
  --server.address 0.0.0.0 ^
  --server.port 8501 ^
  --server.headless true
