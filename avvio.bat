@echo off
rem Avvio senza Docker (Windows): serve solo Python 3.11+ (python.org o Microsoft Store).
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Serve Python 3: scaricalo da https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist .venv python -m venv .venv
.venv\Scripts\pip install -q -r requirements.txt
.venv\Scripts\python launcher.py
pause
