"""End-to-end backup / restore check, run by CI on every platform.

Run locally:  python tests/test_backup_restore.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAF_DATA_DIR"] = tempfile.mkdtemp(prefix="zaf-test-")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from zaf_inventory import backup  # noqa: E402
from zaf_inventory.db import Database  # noqa: E402
from zaf_inventory.main_window import MainWindow  # noqa: E402
from zaf_inventory.paths import db_path, photos_dir  # noqa: E402

# answer "Yes" to every confirmation and record any error popups
errors = []
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.critical = staticmethod(lambda parent, title, text, *a, **k: errors.append(text))


def counts(db):
    return {t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("devices", "parts", "sales", "notes", "photos", "sheet_rows")}


app = QApplication([])
db = Database(db_path())

# --- original data, including a photo
dev = db.insert("devices", {"category": "faulty", "brand": "Apple", "model": "iPhone 6",
                            "imei": "354430061347671", "faults": "Cracked screen", "quantity": 1})
db.insert("parts", {"part_type": "Battery", "quantity": 3})
db.add_note("device", dev, "original note")
img = Path(tempfile.mkdtemp()) / "photo.png"
from PySide6.QtGui import QPixmap  # noqa: E402
pix = QPixmap(20, 20)
pix.fill()
pix.save(str(img))
db.add_photo("device", dev, img)
db.record_sale("device", db.insert("devices", {"brand": "Samsung", "model": "S8"}),
               {"sale_price": 50, "quantity": 1, "status": "Completed", "sale_date": ""})
sheet = db.add_sheet("Cash")
db.add_sheet_row(sheet, {"description": "float", "money_in": 100})
original = counts(db)
zip_path = backup.create_backup(db, "manual")

# a copy under a non-standard name, like a hand-made import file
renamed = backup.backup_dir(db) / "My Client Import.zip"
shutil.copy2(zip_path, renamed)
listed = backup.list_backups(db)
assert renamed in listed, f"renamed backup not listed: {listed}"

# --- change everything, then restore through the real window's import action
db.insert("parts", {"part_type": "Screen", "quantity": 9})
db.delete("devices", dev)
assert counts(db) != original

win = MainWindow(db)
import zaf_inventory.main_window as mw  # noqa: E402
mw.choose_backup_file = lambda *a, **k: str(renamed)
win.import_backup()
assert not errors, f"restore reported an error: {errors}"
restored = counts(db)
assert restored == original, f"restored {restored} != original {original}"
assert any(photos_dir().iterdir()), "photos were not restored"
assert db.get("devices", dev)["model"] == "iPhone 6"

# the window shows the restored data
win.inv["faulty"].refresh()
assert win.inv["faulty"].proxy.rowCount() == 1, "faulty tab did not refresh"

# --- restore through the Backup & Restore dialog as well
from zaf_inventory.dialogs import BackupDialog  # noqa: E402
db.insert("parts", {"part_type": "Extra"})
dlg = BackupDialog(db, win)
dlg._restore(zip_path)
assert not errors, f"dialog restore reported an error: {errors}"
assert counts(db) == original

# --- a damaged file must be refused without touching the data
bad = backup.backup_dir(db) / "broken.zip"
bad.write_bytes(b"not a zip")
from zaf_inventory.dialogs import restore_with_prompt  # noqa: E402
assert restore_with_prompt(win, db, bad) is False and errors, "bad zip was not rejected"
assert counts(db) == original

win.close()
print("backup/restore test passed:", original)
