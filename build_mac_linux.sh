#!/usr/bin/env bash
# Builds a standalone app for macOS (dist/ZafInventory.app) or Linux (dist/ZafInventory/).
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt pyinstaller
python tools/make_icon.py || true
pyinstaller --noconfirm --clean --windowed --name ZafInventory \
    --exclude-module tkinter --exclude-module PySide6.QtWebEngineCore \
    --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQml main.py
echo "Done - see the dist/ folder."
