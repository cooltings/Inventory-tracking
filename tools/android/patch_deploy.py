"""Adjust PySide6's Android deploy tool for this app (run in CI before building).

pyside6-android-deploy hard-codes the python-for-android requirements to
"python3,shiboken6,PySide6" and only adds permissions Qt modules ask for. The app
needs Python's sqlite3 module (its database) and network access (Wi-Fi sync), so
both are added here.
"""
import sys
from pathlib import Path

import PySide6

PERMISSIONS = ["android.permission.INTERNET", "android.permission.ACCESS_NETWORK_STATE",
               "android.permission.ACCESS_WIFI_STATE",
               "android.permission.CHANGE_WIFI_MULTICAST_STATE"]

target = Path(PySide6.__file__).parent / "scripts" / "deploy_lib" / "android" / "buildozer.py"
src = target.read_text()

replacements = [
    ('self.set_value("app", "requirements", "python3,shiboken6,PySide6")',
     'self.set_value("app", "requirements", "python3,sqlite3,shiboken6,PySide6")'),
    ('permissions = ", ".join(permissions)',
     f'permissions = ", ".join(sorted(set(permissions) | set({PERMISSIONS!r})))'),
]
for old, new in replacements:
    if new in src:
        continue
    if old not in src:
        sys.exit(f"patch_deploy: expected text not found in {target}:\n  {old}")
    src = src.replace(old, new, 1)
target.write_text(src)
print(f"patched {target}")
