@echo off
cd /d "%~dp0"
if not exist .venv (
    echo [live-translate] Creating virtual environment...
    python -m venv .venv || (echo Python 3.11+ required & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt || (echo pip install failed & pause & exit /b 1)
start "" .venv\Scripts\pythonw.exe main.py
