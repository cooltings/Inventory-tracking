"""Written Off status: stock totals, UI behaviour and compatibility with older data.

Run locally:  python tests/test_written_off.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAF_DATA_DIR"] = tempfile.mkdtemp(prefix="zaf-test-")

from PySide6.QtWidgets import QApplication, QInputDialog  # noqa: E402

from zaf_inventory import backup  # noqa: E402
from zaf_inventory.db import Database  # noqa: E402
from zaf_inventory.fields import DEVICE_STATUSES, PART_STATUSES, WRITTEN_OFF  # noqa: E402
from zaf_inventory.paths import db_path  # noqa: E402
from zaf_inventory.views import InventoryTab  # noqa: E402

# --- data as an older version (1.0.x) left it: blank / NULL / old statuses only
old = sqlite3.connect(db_path())
old.executescript("""
    CREATE TABLE devices (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, updated_at TEXT,
        sku TEXT, brand TEXT, model TEXT, quantity INTEGER, status TEXT, purchase_price REAL,
        repair_cost REAL, list_price REAL, category TEXT NOT NULL DEFAULT 'working', sold_at TEXT);
    CREATE TABLE parts (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, updated_at TEXT,
        sku TEXT, part_type TEXT, quantity INTEGER, min_qty INTEGER, unit_cost REAL, status TEXT);
    INSERT INTO devices (sku, brand, model, quantity, status, purchase_price, category)
        VALUES ('WP-00001', 'Apple', 'iPhone 8', 1, 'In Stock', 100, 'working'),
               ('WP-00002', 'Apple', 'iPhone X', 1, NULL, 50, 'working'),
               ('FD-00003', 'Samsung', 'S8', 2, 'Awaiting Repair', 20, 'faulty'),
               ('FD-00004', 'Samsung', 'S9', 1, '', 10, 'faulty');
    INSERT INTO parts (sku, part_type, quantity, min_qty, unit_cost, status)
        VALUES ('PT-00001', 'Battery', 4, 0, 5, 'In Stock'), ('PT-00002', 'Screen', 1, 2, 30, NULL);
""")
old.commit()
old.close()

app = QApplication([])
db = Database(db_path())  # migrates the old file in place
s = db.stats()
assert s["working"]["n"] == 2 and s["working"]["cost"] == 150, s["working"]
assert s["faulty"]["n"] == 3 and s["faulty"]["cost"] == 50, s["faulty"]
assert s["parts"]["qty"] == 5 and s["parts"]["cost"] == 50, s["parts"]
assert len(s["low_stock"]) == 1
assert s["written_off"] == {"devices": 0, "parts": 0, "cost": 0}, s["written_off"]
assert WRITTEN_OFF in DEVICE_STATUSES and WRITTEN_OFF in PART_STATUSES

# --- write off one device line (qty 2) and the low-stock part
db.write_off("devices", 3, "water damaged beyond repair")
db.write_off("parts", 2)
s = db.stats()
assert s["faulty"]["n"] == 1 and s["faulty"]["cost"] == 10, s["faulty"]
assert s["parts"]["qty"] == 4 and s["parts"]["cost"] == 20, s["parts"]
assert s["low_stock"] == [], "written-off part still flagged low stock"
assert s["written_off"] == {"devices": 2, "parts": 1, "cost": 70.0}, s["written_off"]
assert db.get("devices", 3)["status"] == WRITTEN_OFF
assert "water damaged" in db.notes("device", 3)[0]["body"]
assert next(r for r in db.list_devices("faulty") if r["id"] == 3)["written_off"]

# --- still visible in its tab, but left out of the tab totals
tab = InventoryTab(db, "faulty")
assert tab.proxy.rowCount() == 2, "written-off device should still be listed"
assert "1 written off" in tab.summary.text() and "Cost £10.00" in tab.summary.text(), \
    tab.summary.text()

# --- the Write Off action from the UI
QInputDialog.getText = staticmethod(lambda *a, **k: ("cracked board", True))
working = InventoryTab(db, "working")
working.select_id(1)
working.write_off()
assert db.get("devices", 1)["status"] == WRITTEN_OFF
assert db.stats()["working"]["n"] == 1

# --- written-off items can be put back into stock
db.update("devices", 1, {"status": "In Stock"})
assert db.stats()["working"]["n"] == 2

# --- survives a backup / restore round trip
zip_path = backup.create_backup(db, "manual")
db.update("devices", 3, {"status": "In Stock"})
backup.restore_backup(db, zip_path)
assert db.get("devices", 3)["status"] == WRITTEN_OFF

print("written-off test passed")
