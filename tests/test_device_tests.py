"""Device test checklist + PDF report, including upgrade from pre-1.1 data.

Run locally:  python tests/test_device_tests.py
"""
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAF_DATA_DIR"] = tempfile.mkdtemp(prefix="zaf-test-")
work = Path(tempfile.mkdtemp(prefix="zaf-work-"))

from PySide6.QtGui import QDesktopServices  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox  # noqa: E402

from zaf_inventory import backup, report  # noqa: E402
from zaf_inventory.checklist import ALL_KEYS, TOTAL_STANDARD, not_applicable_keys  # noqa: E402
from zaf_inventory.db import Database  # noqa: E402
from zaf_inventory.device_test import TestDialog  # noqa: E402
from zaf_inventory.paths import db_path  # noqa: E402
from zaf_inventory.views import InventoryTab  # noqa: E402

QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QDesktopServices.openUrl = staticmethod(lambda *a, **k: True)
app = QApplication([])

# --------------------------------------------------------------------------
# 1. A database exactly as v1.0.x wrote it: no test tables, no RAM / model no. columns
old_path = work / "old.db"
old = sqlite3.connect(old_path)
old.executescript("""
    CREATE TABLE devices (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, updated_at TEXT,
        sku TEXT, brand TEXT, model TEXT, imei TEXT, serial TEXT, storage TEXT, colour TEXT,
        grade TEXT, battery_health INTEGER, faults TEXT, quantity INTEGER, status TEXT,
        category TEXT NOT NULL DEFAULT 'working', sold_at TEXT);
    CREATE TABLE parts (id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT, part_type TEXT, quantity INTEGER);
    CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT, title TEXT, sale_price REAL);
    INSERT INTO devices (sku, brand, model, imei, serial, storage, colour, grade, battery_health,
                         quantity, status, category)
      VALUES ('WP-00001', 'Apple', 'iPhone 12', '356938035643809', 'F2LXK0ABQ1N2', '128GB', 'Blue',
              'B - Good', 87, 1, 'In Stock', 'working'),
             ('WP-00002', 'Samsung', 'Galaxy S21', '352792449647656', 'RF8J8219F5M', '256GB', 'Grey',
              'A - Excellent', 91, 1, 'In Stock', 'working');
""")
old.commit()
old.close()

# restoring an old backup zip into the app must upgrade it in place
old_zip = work / "old-backup.zip"
with zipfile.ZipFile(old_zip, "w") as zf:
    zf.write(old_path, "inventory.db")
db = Database(db_path())
backup.restore_backup(db, old_zip)
cols = db.columns("devices")
assert {"ram", "model_number", "battery_cycles", "os_version", "cosmetic_notes"} <= cols, cols
rows = db.list_devices("working")
assert [r["sku"] for r in rows] == ["WP-00001", "WP-00002"]
assert rows[0]["imei"] == "356938035643809" and rows[0]["battery_health"] == 87
assert all(r["test_summary"] == "Not tested" for r in rows), rows[0]
iphone, galaxy = rows[0]["id"], rows[1]["id"]

# --------------------------------------------------------------------------
# 2. Storage logic
db.set_test(iphone, "power_on", "Pass")
db.set_test(iphone, "net_5g", "Fail", "No signal")
db.set_test(iphone, "net_5g", note="No 5G on EE or Vodafone")     # note-only update keeps result
t = db.device_tests(iphone)
assert t["net_5g"] == {"result": "Fail", "note": "No 5G on EE or Vodafone"}, t
db.set_test(iphone, "power_on", "")                                # cleared -> row removed
assert "power_on" not in db.device_tests(iphone)
db.set_test(iphone, "custom:Taptic keyboard", "", "")              # custom tests persist while blank
assert "custom:Taptic keyboard" in db.device_tests(iphone)
assert db.list_devices("working")[0]["test_summary"] == "1 FAIL · 1/" + str(TOTAL_STANDARD + 1)
db.clear_tests(iphone)
assert set(db.device_tests(iphone)) == {"custom:Taptic keyboard"}
assert db.device_tests(iphone)["custom:Taptic keyboard"]["result"] == ""
assert len(ALL_KEYS) == len(set(ALL_KEYS)) == TOTAL_STANDARD >= 90
assert "sw_frp" in not_applicable_keys({"brand": "Apple"})
assert "sw_icloud" in not_applicable_keys({"brand": "Samsung", "model": "Galaxy S21"})
assert "sw_icloud" not in not_applicable_keys({"brand": "", "model": "iPhone 8"})

# --------------------------------------------------------------------------
# 3. The test dialog, driven the way a user would
dlg = TestDialog(db, iphone)
assert dlg.table.rowCount() > TOTAL_STANDARD
dlg.auto_na()                                                       # iPhone: Android-only items -> N/A
assert dlg.results["sw_frp"]["result"] == "N/A" and dlg.results["sw_frp"]["note"] == "Android only"
assert dlg.results.get("sw_icloud", {}).get("result", "") == ""

row = dlg.row_of["power_on"]
dlg.table.cellWidget(row, 1).buttons["Pass"].click()                # click Pass
assert dlg.results["power_on"]["result"] == "Pass"
dlg.table.cellWidget(row, 1).buttons["Pass"].click()                # click again -> clears
assert dlg.results["power_on"]["result"] == ""
dlg.table.key_result.emit(row, "Pass")                              # keyboard 'P'
assert dlg.results["power_on"]["result"] == "Pass"
assert dlg.table.cellWidget(row, 1).result == "Pass"
dlg.table.key_result.emit(dlg.row_of["net_5g"], "Fail")             # keyboard 'F'
dlg.table.item(dlg.row_of["net_5g"], 2).setText("No 5G signal on EE")
assert dlg.results["net_5g"] == {"result": "Fail", "note": "No 5G signal on EE"}

QInputDialog.getText = staticmethod(lambda *a, **k: ("Taptic feedback on keyboard", True))
dlg.add_custom()
assert "custom:Taptic feedback on keyboard" in dlg.row_of
dlg._on_key_result(dlg.row_of["custom:Taptic feedback on keyboard"], "Pass")

dlg._bulk("Pass")                                                   # everything left -> Pass
assert dlg._untested_keys() == []
dlg.filter.setCurrentText("Fail")                                   # filter shows only the fail
visible = [r for r, i in enumerate(dlg.rowinfo) if i["kind"] == "test" and not dlg.table.isRowHidden(r)]
assert visible == [dlg.row_of["net_5g"]], visible
dlg.filter.setCurrentText("All tests")

dlg.spec.widgets["ram"].setCurrentText("4GB")
dlg.spec.widgets["model_number"].setText("A2403")
dlg.spec.widgets["battery_cycles"].setValue(412)
dlg.spec.widgets["os_version"].setText("iOS 17.5")
dlg.spec.widgets["cosmetic_notes"].setPlainText("Light scratches on screen")
dlg.tested_by.setText("Anthony")
dlg.overall.setPlainText("Clean phone, 5G needs attention.")
dlg.accept()                                                        # saves details + logs activity

saved = db.get("devices", iphone)
assert (saved["ram"], saved["model_number"], saved["battery_cycles"], saved["os_version"]) == \
    ("4GB", "A2403", 412, "iOS 17.5")
assert db.test_meta(iphone)["tested_by"] == "Anthony"
assert db.setting("tester_name") == "Anthony"
assert any(a["action"] == "Tested" for a in db.activity())
assert db.list_devices("working")[0]["test_summary"].startswith("1 FAIL")

# reopening a device that was never tested must not create any test rows or meta
TestDialog(db, galaxy).accept()
assert db.device_tests(galaxy) == {} and db.test_meta(galaxy) == {}

# --------------------------------------------------------------------------
# 4. PDF report
db.set_setting("report_business", "Zaf Phones")
pdf = work / "report.pdf"
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(pdf), ""))
tab = InventoryTab(db, "working")
tab.select_id(iphone)
tab.test_report()
assert pdf.exists() and pdf.read_bytes()[:5] == b"%PDF-", "PDF was not written"
assert db.setting("report_dir") == str(work)
assert report.default_filename(db.get("devices", iphone)).startswith("TestReport-WP-00001-3569")

try:
    from PySide6.QtCore import QSize
    from PySide6.QtPdf import QPdfDocument
except ImportError:
    print("QtPdf not available - skipped reading the PDF back")
else:
    doc = QPdfDocument()
    doc.load(str(pdf))
    assert doc.pageCount() >= 3, doc.pageCount()
    text = " ".join(doc.getAllText(p).text() for p in range(doc.pageCount())) \
        if hasattr(doc, "getAllText") else ""
    out_dir = os.environ.get("ZAF_TEST_OUT")  # CI keeps these so a failure can be inspected
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(pdf, Path(out_dir) / "report.pdf")
        (Path(out_dir) / "extracted-text.txt").write_text(text, encoding="utf-8")
        for page in range(min(doc.pageCount(), 3)):
            doc.render(page, QSize(900, 1273)).save(str(Path(out_dir) / f"page{page + 1}.png"))
    if hasattr(doc, "getAllText"):
        for needle in ("Device Test Report", "Zaf Phones", "356938035643809", "F2LXK0ABQ1N2", "A2403",
                       "128GB", "4GB", "Blue", "87%", "412 cycles", "iOS 17.5", "B - Good",
                       "Light scratches on screen", "1 TEST FAILED", "No 5G signal on EE",
                       "Taptic feedback on keyboard", "Clean phone, 5G needs attention.", "Anthony"):
            assert needle in text, f"{needle!r} missing from the PDF; extracted {len(text)} chars: {text[:300]!r}"
    assert not doc.render(0, QSize(300, 400)).isNull()

# deleting a device removes its test data
db.delete("devices", iphone)
assert db.device_tests(iphone) == {} and db.test_meta(iphone) == {}

# the test data survives a backup / restore round trip
dev = db.insert("devices", {"brand": "Google", "model": "Pixel 7", "category": "working"})
db.set_test(dev, "cam_flash", "Fail", "Torch dead")
bk = backup.create_backup(db, "manual")
db.clear_tests(dev)
backup.restore_backup(db, bk)
assert db.device_tests(dev)["cam_flash"] == {"result": "Fail", "note": "Torch dead"}

print("device test + report test passed:", TOTAL_STANDARD, "standard tests")
