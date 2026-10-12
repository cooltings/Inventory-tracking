@echo off
REM Runs Zaf Inventory straight from the source code (no build step).
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv || goto :error
    call .venv\Scripts\activate.bat
    python -m pip install -r requirements.txt || goto :error
) else (
    call .venv\Scripts\activate.bat
)
start "" pythonw main.py
exit /b 0
:error
echo Could not set up Python. Install Python 3.10+ from python.org and tick "Add to PATH".
pause
