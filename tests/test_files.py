"""Saving and opening picked files (CSV, PDF, photos, backups) through zaf_inventory.fileio.

Run locally:  python tests/test_files.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAF_DATA_DIR"] = tempfile.mkdtemp(prefix="zaf-test-")
out = Path(tempfile.mkdtemp(prefix="zaf-files-"))

from PySide6.QtGui import QDesktopServices, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from zaf_inventory import fileio  # noqa: E402
from zaf_inventory.db import Database  # noqa: E402
from zaf_inventory.paths import db_path  # noqa: E402

opened = []
QDesktopServices.openUrl = staticmethod(lambda url: opened.append(url.toString()) or True)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Yes)
errors = []
QMessageBox.critical = staticmethod(lambda p, t, m, *a, **k: errors.append(m))
app = QApplication([])


def pick(path):
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(path), ""))
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(path), ""))
    QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([str(path)], ""))


# names of Android content:// links
assert fileio.display_name("content://com.android.providers.downloads.documents/document/"
                           "raw%3A%2Fstorage%2Femulated%2F0%2FDownload%2Freport.pdf") == "report.pdf"
assert fileio.display_name("content://media/external/file/primary%3ADocuments%2Fstock.csv") \
    == "stock.csv"
assert fileio.is_uri("content://x/y") and not fileio.is_uri(out / "a.csv")
assert fileio.local_copy(out / "a.csv") == str(out / "a.csv")

db = Database(db_path())
dev = db.insert("devices", {"category": "working", "brand": "Apple", "model": "iPhone 11",
                            "imei": "356938035643809", "quantity": 1, "status": "In Stock"})
db.add_note("device", dev, "Café note: £ sign & accents")

from zaf_inventory.views import InventoryTab  # noqa: E402
tab = InventoryTab(db, "working")

# CSV export, then import it back into the faulty tab
pick(out / "stock.csv")
tab.export_csv()
text = (out / "stock.csv").read_bytes()
assert text.startswith(b"\xef\xbb\xbf") and "iPhone 11".encode() in text, "UTF-8 BOM CSV for Excel"
assert "Café note: £ sign".encode() in text
faulty = InventoryTab(db, "faulty")
faulty.import_csv()
assert not errors, errors
assert [d["model"] for d in db.list_devices("faulty")] == ["iPhone 11"]

# sheet export
from zaf_inventory.sheets import SheetsTab  # noqa: E402
sheets = SheetsTab(db)
db.add_sheet_row(sheets.sheet_id, {"description": "Float", "money_in": 25})
sheets.load_rows()
pick(out / "sheet.csv")
sheets.export_csv()
assert b"Float" in (out / "sheet.csv").read_bytes()

# PDF test report
pick(out / "report.pdf")
from zaf_inventory.device_test import export_report  # noqa: E402
assert export_report(None, db, dev) == str(out / "report.pdf")
assert (out / "report.pdf").read_bytes()[:5] == b"%PDF-"
assert opened and opened[-1].endswith("report.pdf"), opened

# photos keep their original name as the caption
img = out / "front.jpg"
pix = QPixmap(10, 10)
pix.fill()
pix.save(str(img), "JPG")
from zaf_inventory.widgets import PhotosWidget  # noqa: E402
pw = PhotosWidget(db, "device", dev)
pick(img)
pw.browse()
photo = db.photos("device", dev)[0]
assert photo["caption"] == "front.jpg" and photo["filename"].endswith(".jpg"), photo

# backup: save a copy anywhere, then restore from the picked file
from zaf_inventory.dialogs import BackupDialog, choose_backup_file, restore_with_prompt  # noqa: E402
bd = BackupDialog(db)
pick(out / "my-backup.zip")
bd._backup_to()
assert (out / "my-backup.zip").stat().st_size > 1000 and not errors, errors
db.delete("devices", dev)
assert choose_backup_file(None, db) == str(out / "my-backup.zip")
assert restore_with_prompt(None, db, str(out / "my-backup.zip"))
assert db.get("devices", dev)["model"] == "iPhone 11"
print("file handling test passed")
