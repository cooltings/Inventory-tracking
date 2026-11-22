"""Job lots to sell: cost, fees and estimated profit for the lot and each item, then the sale.

Run locally:  python tests/test_sell_lots.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAF_DATA_DIR"] = tempfile.mkdtemp(prefix="zaf-test-")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from zaf_inventory.db import Database  # noqa: E402
from zaf_inventory.paths import db_path  # noqa: E402

QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
app = QApplication([])
db = Database(db_path())
db.set_setting("fee_percent", "10")
db.set_setting("fee_fixed", "0.30")

# three broken phones (cost = purchase + repairs) and two of five screens
a = db.insert("devices", {"category": "faulty", "brand": "Apple", "model": "iPhone 8",
                          "quantity": 1, "status": "Awaiting Repair", "purchase_price": 20,
                          "repair_cost": 5, "list_price": 40})
b = db.insert("devices", {"category": "faulty", "brand": "Samsung", "model": "S9",
                          "quantity": 1, "status": "Awaiting Repair", "purchase_price": 15,
                          "list_price": 20})
c = db.insert("devices", {"category": "faulty", "brand": "Google", "model": "Pixel 4",
                          "quantity": 1, "status": "Awaiting Repair", "purchase_price": 10})
p = db.insert("parts", {"part_type": "Screen", "quantity": 5, "unit_cost": 4, "status": "In Stock"})

# ------------------------------------------------------------------ the dialog from the stock tab
from zaf_inventory.sell_lots import MarkLotSoldDialog, SellAsJobLotDialog, SellLotsTab  # noqa
dlg = SellAsJobLotDialog(db, [("devices", a, "a"), ("devices", b, "b"), ("devices", c, "c")])
assert dlg.lot.currentData() == "__new__"
dlg.form.name.setText("3x faulty phones")
dlg.form.price.setValue(100)
dlg.form.pp.setValue(5)
dlg.form.post.setValue(4)
assert "Estimated" in dlg.estimate.text() and "£" in dlg.estimate.text(), dlg.estimate.text()
assert db.sell_lots() == [], "the live estimate must not save anything"
assert not db.get("devices", a)["sell_lot_uid"]
dlg._ok()
lot = dlg.lot_id
db.add_to_sell_lot(lot, [("parts", p, 2)])

rep = db.sell_lot_report(lot)
t = rep["totals"]
# cost 25 + 15 + 10 + 2*4 = 58; fees 10% of 105 + 0.30 = 10.80; profit 105 - 10.80 - 4 - 58
assert t["cost"] == 58 and t["fees"] == 10.8 and t["profit"] == 32.2, t
assert t["count"] == 4 and t["units"] == 5
assert abs(t["break_even"] - (58 + 4 + 0.30) / 0.9 + 5) < 0.01, t
items = {(i["table"], i["id"]): i for i in rep["items"]}
# shares follow list prices; unpriced items count as the average (30)
assert items[("devices", a)]["price"] > items[("devices", c)]["price"] > items[("devices", b)]["price"]
assert round(sum(i["price"] for i in rep["items"]), 2) == 100
assert round(sum(i["fees"] for i in rep["items"]), 2) == 10.8
assert round(sum(i["profit"] for i in rep["items"]), 2) == 32.2
assert t["individual"] is None, "not every item has a list price"

# changing the list price changes the estimate; the stock tabs show the lot
db.save_sell_lot({"list_price": 50}, lot)
assert db.sell_lot_report(lot)["totals"]["profit"] < 0
db.save_sell_lot({"list_price": 100, "status": "Listed"}, lot)
assert db.get("devices", a)["status"] == "Listed" and db.get("devices", a)["listed_at"]
assert {r["id"]: r["sell_lot_name"] for r in db.list_devices("faulty")}[b] == "3x faulty phones"
assert db.list_parts()[0]["sell_lot_name"] == "3x faulty phones"

# the tab shows it; removing an item and setting a part quantity re-estimates
tab = SellLotsTab(db)
assert tab.lot_id == lot and tab.table.rowCount() == 4
tab.form.price.setValue(120)       # live re-estimate before saving
assert "Estimated" in tab.summary.text()
tab.form.price.setValue(100)
db.remove_from_sell_lot("devices", c)
tab.refresh()
assert tab.table.rowCount() == 3

# ------------------------------------------------------------------ mark the lot sold
sold = MarkLotSoldDialog(db, lot)
sold.price.setValue(90)
assert abs(sold.fees.value() - 9.8) < 0.001, "10% of 95 + 0.30"
sold.fees.setValue(9.5)            # eBay charged a bit less
sold.price.setValue(90)            # typing over the fees keeps them
assert abs(sold.fees.value() - 9.5) < 0.001
sold.order.setText("12-34567-89012")
sold._ok()
sales = [s for s in db.list_sales() if s.get("sell_lot_uid")]
assert len(sales) == 3, sales
assert round(sum(s["sale_price"] for s in sales), 2) == 90
assert round(sum(s["fees"] for s in sales), 2) == 9.5
# 90 + 5 - 9.5 - 4 - (25 + 15 + 8)
assert round(sum(s["profit"] for s in sales), 2) == 33.5
assert all("job lot: 3x faulty phones" in s["title"] for s in sales)
assert db.get("devices", a)["status"] == "Sold" and db.get("devices", b)["status"] == "Sold"
assert db.get("devices", c)["status"] == "Awaiting Repair", "removed item stays in stock"
assert db.get("parts", p)["quantity"] == 3 and not db.get("parts", p)["sell_lot_uid"]
rep = db.sell_lot_report(lot)
assert rep["sold"] and rep["totals"]["profit"] == 33.5
assert db.sell_lot(lot)["status"] == "Sold"
assert db.sell_lot_sold(lot, {"sale_price": 1}) == [], "can't be sold twice"
tab.refresh()
assert not tab.sold_btn.isEnabled() and tab.table.rowCount() == 3

# selling one by one compared, when every item has a list price
lot2 = db.save_sell_lot({"name": "pair", "list_price": 50})
d1 = db.insert("devices", {"category": "faulty", "brand": "A", "model": "1", "quantity": 1,
                           "purchase_price": 10, "list_price": 30})
d2 = db.insert("devices", {"category": "faulty", "brand": "B", "model": "2", "quantity": 1,
                           "purchase_price": 10, "list_price": 30})
db.add_to_sell_lot(lot2, [("devices", d1), ("devices", d2)])
t2 = db.sell_lot_report(lot2)["totals"]
assert t2["individual"] == round(2 * (30 - 3 - 0.30 - 10), 2), t2

# deleting an unsold lot leaves the phones in stock
db.delete_sell_lot(lot2)
assert db.get("devices", d1)["model"] == "1" and not db.get("devices", d1)["sell_lot_uid"]

# ------------------------------------------------------------------ the window
from zaf_inventory.main_window import MainWindow  # noqa: E402
win = MainWindow(db)
win.show_sell_lot(lot)
assert win.tabs.currentWidget() is win.lots and win.lots.currentWidget() is win.sell_lots
assert win.a_sell_lot.shortcut().toString() == "Ctrl+Shift+J"
win.close()
print("selling job lots test passed")

from qt_cleanup import finish  # noqa: E402
finish()
