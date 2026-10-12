@echo off
REM ---------------------------------------------------------------------------
REM  Builds a standalone Windows app:  dist\ZafInventory\ZafInventory.exe
REM  Needs Python 3.10+ from python.org (tick "Add python.exe to PATH").
REM  The finished dist\ZafInventory folder runs on any Windows 10/11 PC -
REM  no Python needed there. Zip it up or copy it wherever you like.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"

if not exist .venv (
    echo Creating build environment...
    python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error

python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt pyinstaller || goto :error

python tools\make_icon.py

set ICON=
if exist app.ico set ICON=--icon app.ico

pyinstaller --noconfirm --clean --windowed --name ZafInventory %ICON% ^
    --exclude-module tkinter ^
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtCharts ^
    main.py || goto :error

echo.
echo ============================================================
echo  Done!  Your app is in:  dist\ZafInventory\ZafInventory.exe
echo.
echo  Portable mode (data kept next to the .exe, e.g. on a USB
echo  stick): create an empty file called portable.txt inside
echo  dist\ZafInventory
echo ============================================================
pause
exit /b 0

:error
echo.
echo Build failed - see the messages above.
pause
exit /b 1
