"""Where the app keeps its data.

* Normal mode:   %APPDATA%\\ZafInventory on Windows, the usual per-user data
                 folder on macOS / Linux.
* Portable mode: put an empty file called ``portable.txt`` next to
                 ZafInventory.exe and all data is kept in a ``data`` folder beside
                 it instead (handy for running from a USB stick).
* ``ZAF_DATA_DIR`` environment variable overrides both.
"""
import os
import sys
from pathlib import Path

from . import APP_ID


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_portable() -> bool:
    return (app_dir() / "portable.txt").exists()


def data_dir() -> Path:
    override = os.environ.get("ZAF_DATA_DIR")
    if override:
        path = Path(override)
    elif is_portable():
        path = app_dir() / "data"
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_ID
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_ID
    else:
        path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "inventory.db"


def photos_dir() -> Path:
    path = data_dir() / "photos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_backup_dir() -> Path:
    return data_dir() / "backups"
