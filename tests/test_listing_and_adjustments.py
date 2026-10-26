"""Days to sell (from listing) and past-sales profit adjustments.

Run locally:  python tests/test_listing_and_adjustments.py
"""
import datetime as dt
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAF_DATA_DIR"] = tempfile.mkdtemp(prefix="zaf-test-")

from PySide6.QtCore import QDate  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from zaf_inventory.db import Database  # noqa: E402
from zaf_inventory.paths import db_path  # noqa: E402

QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
app = QApplication([])
today = dt.date.today().isoformat()

# ------------------------------------------------------------------ data from an older version
old = sqlite3.connect(db_path())
old.executescript("""
    CREATE TABLE devices (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, updated_at TEXT,
        sku TEXT, brand TEXT, model TEXT, quantity INTEGER, status TEXT, purchase_date TEXT,
        purchase_price REAL, category TEXT NOT NULL DEFAULT 'working', sold_at TEXT);
    CREATE TABLE parts (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, updated_at TEXT,
        sku TEXT, part_type TEXT,
        quantity INTEGER, status TEXT);
    CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, updated_at TEXT,
        sku TEXT, title TEXT,
        item_kind TEXT, item_id INTEGER, sale_date TEXT, sale_price REAL, profit REAL,
        quantity INTEGER, status TEXT);
    CREATE TABLE activity (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, action TEXT,
        entity TEXT, entity_id INTEGER, details TEXT);
    INSERT INTO devices (sku, brand, model, quantity, status, purchase_date, purchase_price,
                         created_at)
      VALUES ('WP-00001', 'Apple', 'iPhone 8', 1, 'Sold', '2026-08-20', 80, '2026-08-20T10:00:00'),
             ('WP-00002', 'Samsung', 'S9', 1, 'Sold', '2026-08-01', 60, '2026-08-01T10:00:00'),
             ('WP-00003', 'Google', 'Pixel 6', 1, 'Listed', '2026-09-20', 90, '2026-09-20T10:00:00');
    -- phone 1 was listed via Set Status on 1 Sept (and set back and forth later)
    INSERT INTO activity (ts, action, entity, entity_id, details) VALUES
      ('2026-09-01T09:30:00', 'Status', 'device', 1, 'WP-00001 -> Listed'),
      ('2026-09-05T09:30:00', 'Status', 'device', 1, 'WP-00001 -> Reserved'),
      ('2026-09-06T09:30:00', 'Status', 'device', 1, 'WP-00001 -> Listed'),
      ('2026-09-21T12:00:00', 'Status', 'device', 3, 'WP-00003 -> Listed'),
      ('2026-09-02T09:30:00', 'Status', 'device', 2, 'WP-00002 -> In Repair');
    INSERT INTO sales (sku, title, item_kind, item_id, sale_date, sale_price, profit, quantity, status)
      VALUES ('WP-00001', 'iPhone 8', 'device', 1, '2026-09-11', 120, 40, 1, 'Completed'),
             ('WP-00002', 'S9', 'device', 2, '2026-09-11', 100, 40, 1, 'Completed');
""")
old.commit()
old.close()

db = Database(db_path())   # upgrade in place
d1, d2, d3 = (db.get("devices", i) for i in (1, 2, 3))
assert d1["listed_at"] == "2026-09-01", "first listing in the activity log"
assert not d2["listed_at"], "never listed - no date made up"
assert d3["listed_at"] == "2026-09-21"
s = db.stats()
assert s["days_listed_n"] == 1 and s["days_fallback_n"] == 1, s
assert abs(s["avg_days_to_sell"] - (10 + 41) / 2) < 0.01, s["avg_days_to_sell"]   # 10 listed, 41 added
sales = {r["sku"]: r for r in db.list_sales()}
assert (sales["WP-00001"]["days_to_sell"], sales["WP-00001"]["days_basis"]) == (10, "listed")
assert (sales["WP-00002"]["days_to_sell"], sales["WP-00002"]["days_basis"]) == (41, "added")
# the back-fill only runs once (it must not overwrite later edits)
db.update("devices", 1, {"listed_at": "2026-09-03"})
db.close()
db = Database(db_path())
assert db.get("devices", 1)["listed_at"] == "2026-09-03"

# ------------------------------------------------------------------ recording new listings
new = db.insert("devices", {"category": "working", "brand": "Apple", "model": "iPhone 12",
                            "quantity": 1, "status": "In Stock"})
assert not db.get("devices", new)["listed_at"]
db.update("devices", new, {"status": "Listed"})
assert db.get("devices", new)["listed_at"] == today
db.update("devices", new, {"status": "Reserved"})
db.conn.execute("UPDATE devices SET listed_at='2026-10-01' WHERE id=?", (new,))
db.update("devices", new, {"status": "Listed"})
assert db.get("devices", new)["listed_at"] == "2026-10-01", "keeps the first listing date"
listed_now = db.insert("devices", {"category": "working", "brand": "Nokia", "model": "3310",
                                   "quantity": 1, "status": "Listed"})
assert db.get("devices", listed_now)["listed_at"] == today
part = db.insert("parts", {"part_type": "Screen", "quantity": 1, "status": "In Stock"})
db.update("parts", part, {"status": "Listed"})
assert db.get("parts", part)["listed_at"] == today

# via the app: Set Status menu and the edit form
from zaf_inventory.views import InventoryTab  # noqa: E402
tab = InventoryTab(db, "working")
other = db.insert("devices", {"category": "working", "brand": "Sony", "model": "Xperia 5",
                              "quantity": 1, "status": "In Stock"})
tab.refresh()
tab.select_id(other)
tab.set_status("Listed")
assert db.get("devices", other)["listed_at"] == today
from zaf_inventory.dialogs import ItemDialog  # noqa: E402
form_dev = db.insert("devices", {"category": "working", "brand": "LG", "model": "G8",
                                 "quantity": 1, "status": "In Stock"})
dlg = ItemDialog(db, "devices", record=db.get("devices", form_dev))
dlg.form.widgets["status"].setCurrentText("Listed")
dlg._save(close=True)
assert db.get("devices", form_dev)["listed_at"] == today, "edit form leaves the date blank -> filled"
row = next(r for r in db.list_devices("working") if r["id"] == new)
assert row["days_listed"] == (dt.date.today() - dt.date(2026, 10, 1)).days

# ------------------------------------------------------------------ past sales as one lump
before = db.stats()
from zaf_inventory.dialogs import AdjustmentDialog  # noqa: E402
adj = AdjustmentDialog(db)
adj.title.setText("Sales before Zaf Inventory")
adj.qty.setValue(140)
adj.revenue.setValue(9000)
adj.profit.setValue(3200)
adj._save()                                               # undated -> all-time only
after = db.stats()
assert round(after["all"]["profit"] - before["all"]["profit"], 2) == 3200
assert after["all"]["n"] - before["all"]["n"] == 140, "counts as 140 phones sold"
assert round(after["all"]["revenue"] - before["all"]["revenue"], 2) == 9000
assert after["month"] == before["month"], "undated adjustment stays out of this month"

dated = AdjustmentDialog(db)
dated.title.setText("Correction")
dated.dated.setChecked(True)
dated.date.setDate(QDate.currentDate())
dated.profit.setValue(-50)                                # losses allowed
dated._save()
s = db.stats()
assert round(s["month"]["profit"] - before["month"]["profit"], 2) == -50
assert round(s["all"]["profit"] - before["all"]["profit"], 2) == 3150

lump = next(r for r in db.list_sales() if r["title"] == "Sales before Zaf Inventory")
assert lump["item_kind"] == "adjustment" and lump["profit"] == 3200 and lump["quantity"] == 140
edit = AdjustmentDialog(db, sale=db.get("sales", lump["id"]))
assert edit.qty.value() == 140 and edit.profit.value() == 3200 and not edit.dated.isChecked()
edit.profit.setValue(3300)
edit._save()
assert db.get("sales", lump["id"])["profit"] == 3300
assert round(db.stats()["all"]["profit"] - before["all"]["profit"], 2) == 3250

sold = InventoryTab(db, "sales")
assert "Profit" in sold.summary.text()
sold.select_id(lump["id"])
sold.return_sale()                                        # blocked with a message, no change
assert db.get("sales", lump["id"])["status"] == "Completed"

from zaf_inventory.dashboard import Dashboard  # noqa: E402
dash = Dashboard(db)
dash.refresh()
assert "listed to sold" in dash.cards["speed"].sub.text(), dash.cards["speed"].sub.text()
assert all(b["brand"] != "(none)" or b["n"] < 140 for b in db.stats()["brands"]), \
    "past-sales totals must not show up as a brand"
print("listing + adjustments test passed")
