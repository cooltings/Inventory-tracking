"""Job lots (cost split, per-item profit / loss), repaired SKUs and the tablet layout.

Run locally:  python tests/test_joblots_sku_layout.py
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
os.environ["ZAF_COMPACT"] = "1"   # lay out as on the tablet
os.environ["ZAF_TOUCH"] = "1"

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from zaf_inventory.db import Database, sku_for_section  # noqa: E402
from zaf_inventory.paths import db_path  # noqa: E402

QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
app = QApplication([])

# ------------------------------------------------------------------ an older database
old = sqlite3.connect(db_path())
old.executescript("""
    CREATE TABLE devices (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, updated_at TEXT,
        sku TEXT, brand TEXT, model TEXT, quantity INTEGER, status TEXT,
        category TEXT NOT NULL DEFAULT 'working', sold_at TEXT);
    CREATE TABLE parts (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, updated_at TEXT,
        sku TEXT, part_type TEXT, quantity INTEGER, status TEXT);
    CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, updated_at TEXT,
        sku TEXT, title TEXT, sale_price REAL);
    -- phone 1 was repaired before this version (still FD-), phone 2 broke after being working
    INSERT INTO devices (sku, brand, model, quantity, status, category) VALUES
      ('FD-00001', 'Apple', 'iPhone 8', 1, 'In Stock', 'working'),
      ('WP-00002', 'Samsung', 'S9', 1, 'Awaiting Repair', 'faulty'),
      ('MY-OWN-7', 'Nokia', '3310', 1, 'In Stock', 'working'),
      ('WP-00004', 'Google', 'Pixel 6', 1, 'In Stock', 'working');
""")
old.commit()
old.close()
db = Database(db_path())
assert [db.get("devices", i)["sku"] for i in (1, 2, 3, 4)] == \
    ["WP-00001-R", "FD-00002", "MY-OWN-7", "WP-00004"], "one-off SKU fix for existing phones"

# ------------------------------------------------------------------ repaired SKUs
assert sku_for_section("FD-00012", "working") == "WP-00012-R"
assert sku_for_section("FD-T00012", "working") == "WP-T00012-R"
assert sku_for_section("WP-00012-R", "faulty") == "FD-00012"
assert sku_for_section("WP-00005", "faulty") == "FD-00005"
assert sku_for_section("CUSTOM-1", "working") == "CUSTOM-1"
fd = db.insert("devices", {"category": "faulty", "brand": "Apple", "model": "iPhone X",
                           "quantity": 1, "status": "Awaiting Repair"})
assert db.get("devices", fd)["sku"] == f"FD-{fd:05d}"
db.move_device(fd, "working")
assert db.get("devices", fd)["sku"] == f"WP-{fd:05d}-R"
assert "SKU" in db.notes("device", fd)[0]["body"]
db.move_device(fd, "faulty")
assert db.get("devices", fd)["sku"] == f"FD-{fd:05d}"
db.move_device(fd, "working")

# ------------------------------------------------------------------ job lots
a = db.insert("devices", {"category": "working", "brand": "Apple", "model": "iPhone 11",
                          "quantity": 1, "status": "In Stock", "list_price": 200})
b = db.insert("devices", {"category": "faulty", "brand": "Samsung", "model": "S20",
                          "quantity": 1, "status": "Awaiting Repair", "list_price": 100})
c = db.insert("devices", {"category": "faulty", "brand": "Google", "model": "Pixel 5",
                          "quantity": 1, "status": "Awaiting Repair"})
p = db.insert("parts", {"part_type": "Screen", "quantity": 2, "status": "In Stock",
                        "list_price": 25})

from zaf_inventory.joblots import AddToJobLotDialog, JobLotsTab  # noqa: E402
dlg = AddToJobLotDialog(db, [("devices", a, "a"), ("devices", b, "b"), ("devices", c, "c"),
                             ("parts", p, "p")])
assert dlg.lot.currentData() == "__new__", "no lots yet -> create one"
dlg.form.name.setText("Lot of 3 + screens")
dlg.form.total.setValue(330)
dlg._ok()
lot = dlg.lot_id
# even split over 5 units (3 phones + 2 screens) = 66 each
assert [db.get("devices", i)["purchase_price"] for i in (a, b, c)] == [66, 66, 66]
assert db.get("parts", p)["unit_cost"] == 66

# second batch added to the same lot through the dialog (existing lot pre-selected)
extra = db.insert("devices", {"category": "working", "brand": "Apple", "model": "iPhone XR",
                              "quantity": 1, "status": "In Stock", "list_price": 150})
dlg2 = AddToJobLotDialog(db, [("devices", extra, "x")])
assert dlg2.lot.currentData() == lot
dlg2._ok()
assert db.get("devices", a)["purchase_price"] == 55, "330 / 6 units"

# split by list price; the Pixel (no price) counts as the average of the known prices
db.save_joblot({"split": "list"}, lot)
rep = db.joblot_report(lot)
assert abs(rep["totals"]["allocated"] - 330) < 0.05, rep["totals"]
by = {i["id"]: i for i in rep["items"] if i["table"] == "devices"}
assert by[a]["unit_cost"] > by[extra]["unit_cost"] > by[b]["unit_cost"]

# sell the iPhone 11: its sale is costed from the lot and re-costed if the split changes
dev = db.get("devices", a)
db.record_sale("device", a, {"sku": dev["sku"], "title": "iPhone 11", "sale_price": 250,
                             "fees": 30, "postage_cost": 5, "cost_basis": dev["purchase_price"],
                             "quantity": 1, "status": "Completed", "sale_date": "2026-11-20"})
db.save_joblot({"split": "even"}, lot)
sale = next(s for s in db.list_sales() if s["item_id"] == a)
assert sale["cost_basis"] == 55 and sale["profit"] == 250 - 30 - 5 - 55, sale

rep = db.joblot_report(lot)
t = rep["totals"]
assert t["sold_units"] == 1 and t["units"] == 6 and t["net_sales"] == 215
assert t["pl_so_far"] == 215 - 330, t
items = {(i["table"], i["id"]): i for i in rep["items"]}
assert items[("devices", a)]["pl"] == 160 and not items[("devices", a)]["estimated"]
assert items[("devices", c)]["unpriced"] and items[("devices", c)]["pl"] == -55
assert items[("devices", b)]["estimated"]
assert items[("parts", p)]["units"] == 2 and items[("parts", p)]["cost"] == 110

# the stock tabs show the lot and each item's P/L
from zaf_inventory.views import InventoryTab  # noqa: E402
faulty_rows = {r["id"]: r for r in db.list_devices("faulty")}
assert faulty_rows[b]["joblot_name"] == "Lot of 3 + screens" and faulty_rows[b]["lot_pl"] is not None
assert db.list_parts()[0]["lot_pl"] == items[("parts", p)]["pl"]
tab = InventoryTab(db, "faulty")
cols = [c[0] for c in tab.cfg["columns"]]
assert "joblot_name" in cols and "lot_pl" in cols

# manual split leaves costs alone and reports what's not shared out
db.save_joblot({"split": "manual", "total_cost": 400}, lot)
assert db.get("devices", b)["purchase_price"] == 55
tabw = JobLotsTab(db)
tabw.select_lot(lot)
assert "not yet shared out" in tabw.note.text(), tabw.note.text()

# remove an item, delete the lot: items stay in stock
db.save_joblot({"split": "even", "total_cost": 330}, lot)
db.remove_from_joblot("devices", c)
assert not db.get("devices", c)["joblot_uid"]
assert db.get("devices", b)["purchase_price"] == 66, "330 / 5 units left"
db.delete_joblot(lot)
assert db.joblots() == [] and db.get("devices", b)["model"] == "S20"
assert not db.get("devices", b)["joblot_uid"]

# ------------------------------------------------------------------ fits the Galaxy Tab S10 Lite
# landscape at the largest display scaling leaves about 1056 x 660 for the app
from zaf_inventory import dialogs, device_test, sync_ui  # noqa: E402
from zaf_inventory.main_window import MainWindow  # noqa: E402
win = MainWindow(db)
m = win.minimumSizeHint()
assert m.width() <= 700 and m.height() <= 640, f"main window needs {m.width()}x{m.height()}"
for i in range(win.tabs.count()):
    h = win.tabs.widget(i).minimumSizeHint()
    assert h.width() <= 680 and h.height() <= 560, f"{win.tabs.tabText(i)} needs {h.width()}x{h.height()}"
for name, make in {
        "edit item": lambda: dialogs.ItemDialog(db, "devices", record=db.get("devices", a)),
        "test device": lambda: device_test.TestDialog(db, b),
        "mark sold": lambda: dialogs.SaleDialog(db, kind="device", item=db.get("devices", b)),
        "settings": lambda: dialogs.SettingsDialog(db),
        "backups": lambda: dialogs.BackupDialog(db),
        "sync": lambda: sync_ui.SyncDialog(db, win.sync),
        "past sales": lambda: dialogs.AdjustmentDialog(db),
        "job lot": lambda: AddToJobLotDialog(db, [("devices", b, "b")])}.items():
    h = make().minimumSizeHint()
    assert h.width() <= 1000 and h.height() <= 620, f"{name} dialog needs {h.width()}x{h.height()}"
# everything is reachable without the menu bar (hidden on Android) via the Menu button
menu = win.menu_button.menu()
titles = [a.text().replace("&", "") for a in menu.actions()]
assert {"File", "Edit", "Item", "View", "Help", "Go to"} <= set(titles), titles
go = next(a.menu() for a in menu.actions() if a.text() == "Go to")
assert len(go.actions()) == win.tabs.count() == 9
go.actions()[5].trigger()
assert win.tabs.currentWidget() is win.joblots
file_menu = next(a.menu() for a in menu.actions() if a.text().replace("&", "") == "File")
assert any("Backup" in a.text() for a in file_menu.actions())
win.close()
print("job lots + SKU + tablet layout test passed")

from qt_cleanup import finish  # noqa: E402
finish()
