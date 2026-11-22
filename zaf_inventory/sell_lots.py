"""Job lots to sell: pick phones / parts, price them as one listing and see the profit."""
from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QDialog,
                               QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import theme
from .joblots import _money_box
from .widgets import money

LOT_STATUSES = ("Draft", "Listed")
NEW_LOT = "__new__"


def _pl(value):
    c = theme.current
    word = "Profit" if value >= 0 else "Loss"
    return f"<span style='color:{c['good' if value >= 0 else 'bad']}'><b>{word} {money(value)}</b></span>"


def summary_html(t, sold=False):
    """The money lines shown under a lot: price, fees, costs and the (estimated) profit."""
    if not t["count"]:
        return "No items in this lot yet. Select phones or parts and choose <b>Sell as Job Lot</b>."
    head = "Sold for" if sold else "Listed at"
    lines = [f"{t['count']} item(s), {t['units']} unit(s) · cost <b>{money(t['cost'])}</b>",
             f"{head} <b>{money(t['price'])}</b>"
             + (f" + {money(t['postage_charged'])} P&amp;P" if t["postage_charged"] else "")
             + f" · fees {money(t['fees'])}"
             + (f" · postage {money(t['postage_cost'])}" if t["postage_cost"] else "")
             + (f" · other {money(t['other_costs'])}" if t["other_costs"] else ""),
             ("Profit: " if sold else "Estimated: ") + _pl(t["profit"])
             + (f" ({t['margin']:.0f}% margin)" if t["margin"] is not None else "")]
    if t.get("break_even") is not None:
        lines.append(f"Break-even price: <b>{money(t['break_even'])}</b>")
    if t.get("individual") is not None:
        diff = t["profit"] - t["individual"]
        lines.append(f"Selling them one by one at their list prices: {money(t['individual'])} "
                     f"({'lot is ' + money(abs(diff)) + (' better' if diff >= 0 else ' worse')})")
    return "<br>".join(lines)


class SellLotForm(QWidget):
    changed = Signal()

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        f = QFormLayout(self)
        f.setContentsMargins(0, 0, 0, 0)
        self.name = QLineEdit(placeholderText="e.g. Job lot 5x faulty iPhones")
        self.status = QComboBox()
        self.status.addItems(LOT_STATUSES)
        self.platform = QComboBox(editable=True)
        self.platform.addItems(db.get_list("platforms"))
        self.price = _money_box()
        self.pp = _money_box()
        self.post = _money_box()
        self.other = _money_box()
        f.addRow("Lot name", self.name)
        f.addRow("List price", self.price)
        f.addRow("P&&P charged", self.pp)
        f.addRow("Postage cost", self.post)
        f.addRow("Other costs", self.other)
        f.addRow("Status", self.status)
        f.addRow("Platform", self.platform)
        for w in (self.price, self.pp, self.post, self.other):
            w.valueChanged.connect(self.changed)

    def set_lot(self, lot):
        self.blockSignals(True)
        self.name.setText(lot.get("name") or "")
        self.status.setCurrentText(lot.get("status") if lot.get("status") in LOT_STATUSES
                                   else "Draft")
        self.platform.setCurrentText(lot.get("platform") or self.db.setting("default_platform")
                                     or "")
        self.price.setValue(float(lot.get("list_price") or 0))
        self.pp.setValue(float(lot.get("postage_charged") or 0))
        self.post.setValue(float(lot.get("postage_cost") or 0))
        self.other.setValue(float(lot.get("other_costs") or 0))
        self.blockSignals(False)

    def values(self):
        return {"name": self.name.text().strip() or "Job lot",
                "status": self.status.currentText(),
                "platform": self.platform.currentText().strip(),
                "list_price": round(self.price.value(), 2),
                "postage_charged": round(self.pp.value(), 2),
                "postage_cost": round(self.post.value(), 2),
                "other_costs": round(self.other.value(), 2)}


class SellAsJobLotDialog(QDialog):
    """Put the selected items into a new or existing lot to sell, with a live estimate."""

    def __init__(self, db, items, parent=None):
        super().__init__(parent)
        self.db, self.items = db, items   # [(table, id, label)]
        self.lot_id = None
        self.setWindowTitle("Sell as Job Lot")
        lay = QVBoxLayout(self)
        names = ", ".join(lbl for _, _, lbl in items[:4]) + (" ..." if len(items) > 4 else "")
        head = QLabel(f"<b>{len(items)} item(s):</b> {names}")
        head.setWordWrap(True)
        lay.addWidget(head)
        row = QHBoxLayout()
        row.addWidget(QLabel("Lot"))
        self.lot = QComboBox()
        self.lot.addItem("New job lot to sell...", NEW_LOT)
        for lot in db.sell_lots(include_sold=False):
            self.lot.addItem(f"{lot['name']}  ({lot.get('status') or 'Draft'})", lot["id"])
        row.addWidget(self.lot, 1)
        lay.addLayout(row)
        self.form = SellLotForm(db)
        lay.addWidget(self.form)
        self.estimate = QLabel()
        self.estimate.setWordWrap(True)
        self.estimate.setTextFormat(Qt.RichText)
        lay.addWidget(self.estimate)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Add to Lot")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.lot.currentIndexChanged.connect(self._lot_changed)
        self.form.changed.connect(self._estimate)
        self._lot_changed()

    def _lot_changed(self, *_):
        lot_id = self.lot.currentData()
        if lot_id == NEW_LOT:
            self.form.set_lot({"status": "Draft"})
            self.form.name.setFocus()
        else:
            self.form.set_lot(self.db.sell_lot(lot_id))
        self._estimate()

    def _estimate(self):
        """Work the figures out on a throwaway lot so nothing is saved until OK."""
        db = self.db
        try:
            db.conn.execute("SAVEPOINT preview")
            lot_id = self.lot.currentData()
            vals = self.form.values()
            if lot_id == NEW_LOT:
                lot_id = db.insert("sell_lots", vals, commit=False)
            else:
                db.update("sell_lots", lot_id, vals, commit=False)
            for t, i, _ in self.items:
                item = db.get(t, i)
                db.conn.execute(f"UPDATE {t} SET sell_lot_uid=?, sell_lot_qty=? WHERE id=?",
                                (db.sell_lot(lot_id)["uid"],
                                 max(1, int(float(item.get("quantity") or 1))), i))
            rep = db.sell_lot_report(lot_id)
        finally:
            db.conn.execute("ROLLBACK TO preview")
            db.conn.execute("RELEASE preview")
        self.estimate.setText(summary_html(rep["totals"]))

    def _ok(self):
        lot_id = self.lot.currentData()
        vals = self.form.values()
        if lot_id == NEW_LOT:
            lot_id = self.db.save_sell_lot(vals)
        else:
            self.db.save_sell_lot(vals, lot_id)
        self.db.add_to_sell_lot(lot_id, [(t, i) for t, i, _ in self.items])
        self.lot_id = lot_id
        self.accept()


class MarkLotSoldDialog(QDialog):
    def __init__(self, db, lot_id, parent=None):
        super().__init__(parent)
        self.db, self.lot_id = db, lot_id
        lot = db.sell_lot(lot_id)
        self.setWindowTitle(f"Mark Sold: {lot['name']}")
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.price = _money_box()
        self.price.setValue(float(lot.get("list_price") or 0))
        self.pp = _money_box()
        self.pp.setValue(float(lot.get("postage_charged") or 0))
        self.post = _money_box()
        self.post.setValue(float(lot.get("postage_cost") or 0))
        self.other = _money_box()
        self.other.setValue(float(lot.get("other_costs") or 0))
        self.fees = _money_box()
        self.auto_fees = True
        self.date = QDateEdit(QDate.currentDate(), calendarPopup=True)
        self.date.setDisplayFormat("dd/MM/yyyy")
        self.platform = QComboBox(editable=True)
        self.platform.addItems(db.get_list("platforms"))
        self.platform.setCurrentText(lot.get("platform") or db.setting("default_platform") or "")
        self.order = QLineEdit()
        self.buyer = QLineEdit()
        f.addRow("Sold for", self.price)
        f.addRow("P&&P charged", self.pp)
        f.addRow("Fees", self.fees)
        f.addRow("Postage cost", self.post)
        f.addRow("Other costs", self.other)
        f.addRow("Sold on", self.date)
        f.addRow("Platform", self.platform)
        f.addRow("Order no.", self.order)
        f.addRow("Buyer", self.buyer)
        lay.addLayout(f)
        hint = QLabel("Fees are worked out from your fee settings; type over them if eBay "
                      "charged something different. Each item gets its own sale in Sold, "
                      "with the money shared out.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setTextFormat(Qt.RichText)
        lay.addWidget(self.preview)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Mark Lot Sold")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for w in (self.price, self.pp, self.post, self.other):
            w.valueChanged.connect(self._update)
        self.fees.valueChanged.connect(self._fees_edited)
        self._update()

    def _fees_edited(self, *_):
        if not self._setting_fees:
            self.auto_fees = False
        self._update(fees_only=True)

    def _update(self, *_, fees_only=False):
        if self.auto_fees and not fees_only:
            pct = float(self.db.setting("fee_percent") or 0)
            fixed = float(self.db.setting("fee_fixed") or 0)
            gross = self.price.value() + self.pp.value()
            self._setting_fees = True
            self.fees.setValue(gross * pct / 100 + (fixed if gross > 0 else 0))
            self._setting_fees = False
        rep = self.db.sell_lot_report(self.lot_id, **self._money())
        self.preview.setText(summary_html(rep["totals"]).replace("Estimated", "Profit"))

    _setting_fees = False

    def _money(self):
        return {"price": self.price.value(), "postage_charged": self.pp.value(),
                "postage_cost": self.post.value(), "other_costs": self.other.value(),
                "fees": round(self.fees.value(), 2)}

    def sale(self):
        m = self._money()
        return {"sale_price": m["price"], "postage_charged": m["postage_charged"],
                "postage_cost": m["postage_cost"], "other_costs": m["other_costs"],
                "fees": m["fees"], "sale_date": self.date.date().toString("yyyy-MM-dd"),
                "platform": self.platform.currentText().strip(),
                "order_number": self.order.text().strip(), "buyer": self.buyer.text().strip()}

    def _ok(self):
        self.db.sell_lot_sold(self.lot_id, self.sale())
        self.accept()


class SellLotsTab(QWidget):
    open_item = Signal(str, int)
    changed = Signal()

    COLS = ["SKU", "Item", "From", "Qty", "Cost", "Share of Price", "Fees", "Est. Profit"]

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.lot_id = None
        self.items = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        split = QSplitter()
        lay.addWidget(split, 1)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Lots to Sell")
        title.setObjectName("h2")
        ll.addWidget(title)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._lot_selected)
        ll.addWidget(self.list, 1)
        delete = QPushButton("Delete Lot")
        delete.clicked.connect(self.delete_lot)
        ll.addWidget(delete)
        hint = QLabel("Select phones or parts on their tab, then Sell as Job Lot "
                      "(right-click or Ctrl+Shift+J).")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        ll.addWidget(hint)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.form = SellLotForm(db)
        self.form.setMinimumWidth(240)
        self.form.changed.connect(self._live)
        top.addWidget(self.form, 3)
        side = QVBoxLayout()
        self.save_btn = QPushButton("Save Changes")
        self.save_btn.clicked.connect(self.save_lot)
        self.sold_btn = QPushButton("Mark Lot Sold...")
        self.sold_btn.setProperty("primary", True)
        self.sold_btn.clicked.connect(self.mark_sold)
        side.addWidget(self.save_btn)
        side.addWidget(self.sold_btn)
        side.addStretch(1)
        top.addLayout(side, 1)
        rl.addLayout(top)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.RichText)
        rl.addWidget(self.summary)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self._open)
        rl.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.remove_btn = QPushButton("Remove From Lot")
        self.remove_btn.clicked.connect(self.remove_items)
        self.qty_btn = QPushButton("Set Qty...")
        self.qty_btn.clicked.connect(self.set_qty)
        open_btn = QPushButton("Open Item")
        open_btn.clicked.connect(self._open)
        for b in (self.remove_btn, self.qty_btn, open_btn):
            row.addWidget(b)
        row.addStretch(1)
        rl.addLayout(row)
        note = QLabel("The lot price, fees and postage are shared across the items by their own "
                      "list price (else by cost) to give each one's profit.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        rl.addWidget(note)
        split.addWidget(right)
        split.setSizes([240, 900])
        split.setChildrenCollapsible(False)
        left.setMinimumWidth(150)
        self.refresh()

    def refresh(self):
        keep = self.lot_id
        self.list.blockSignals(True)
        self.list.clear()
        for lot in self.db.sell_lots():
            t = self.db.sell_lot_report(lot["id"])["totals"]
            status = lot.get("status") or "Draft"
            word = "profit" if status == "Sold" else "est."
            item = QListWidgetItem(f"{lot['name']}\n{status} · {t['count']} item(s) · "
                                   f"{word} {'+' if t['profit'] >= 0 else ''}{money(t['profit'])}")
            item.setData(Qt.UserRole, lot["id"])
            item.setForeground(QColor(theme.current["good" if t["profit"] >= 0 else "bad"]))
            self.list.addItem(item)
        self.list.blockSignals(False)
        ids = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        if ids:
            self.list.setCurrentRow(ids.index(keep) if keep in ids else 0)
        else:
            self.lot_id = None
            self._show(None)

    def select_lot(self, lot_id):
        self.lot_id = lot_id
        self.refresh()

    def _lot_selected(self, item, _prev=None):
        self.lot_id = item.data(Qt.UserRole) if item else None
        self._show(self.lot_id)

    def _show(self, lot_id):
        self.table.setRowCount(0)
        self.items = []
        for w in (self.form, self.save_btn, self.sold_btn, self.remove_btn, self.qty_btn):
            w.setEnabled(lot_id is not None)
        if lot_id is None:
            self.summary.setText("No lots to sell yet. Select broken phones (or anything else) "
                                 "and choose <b>Sell as Job Lot</b>.")
            return
        lot = self.db.sell_lot(lot_id)
        self.form.set_lot(lot)
        sold = lot.get("status") == "Sold"
        for w in (self.form, self.save_btn, self.sold_btn, self.remove_btn, self.qty_btn):
            w.setEnabled(not sold)
        self._fill(self.db.sell_lot_report(lot_id))

    def _live(self):
        """Re-estimate as the price / postage boxes change, before saving."""
        if self.lot_id is None or not self.form.isEnabled():
            return
        v = self.form.values()
        self._fill(self.db.sell_lot_report(self.lot_id, price=v["list_price"],
                                           postage_charged=v["postage_charged"],
                                           postage_cost=v["postage_cost"],
                                           other_costs=v["other_costs"]))

    def _fill(self, rep):
        self.summary.setText(summary_html(rep["totals"], rep["sold"]))
        self.items = rep["items"]
        self.table.setHorizontalHeaderItem(7, QTableWidgetItem(
            "Profit" if rep["sold"] else "Est. Profit"))
        c = theme.current
        self.table.setRowCount(0)
        for i in self.items:
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = [i["sku"], i["title"], i["where"], str(i["qty"]), money(i["cost"]),
                    money(i["price"] + i["postage_charged"]), money(i["fees"]), money(i["profit"])]
            for col, v in enumerate(vals):
                cell = QTableWidgetItem(v or "")
                if col >= 3:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if col == 7:
                    cell.setForeground(QColor(c["good" if i["profit"] >= 0 else "bad"]))
                self.table.setItem(r, col, cell)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

    # ---------------------------------------------------------------- actions
    def save_lot(self):
        if self.lot_id is None:
            return
        self.db.save_sell_lot(self.form.values(), self.lot_id)
        self.refresh()
        self.changed.emit()

    def mark_sold(self):
        if self.lot_id is None:
            return
        if not self.items:
            QMessageBox.information(self, "Mark Lot Sold", "This lot has no items in it.")
            return
        self.db.save_sell_lot(self.form.values(), self.lot_id)
        dlg = MarkLotSoldDialog(self.db, self.lot_id, self)
        if dlg.exec():
            self.refresh()
            self.changed.emit()

    def delete_lot(self):
        if self.lot_id is None:
            return
        lot = self.db.sell_lot(self.lot_id)
        extra = ("\n\nIts sales stay in Sold." if lot.get("status") == "Sold"
                 else "\n\nThe phones and parts stay in your stock.")
        if QMessageBox.question(self, "Delete Lot", f"Delete the lot '{lot['name']}'?{extra}") \
                != QMessageBox.Yes:
            return
        self.db.delete_sell_lot(self.lot_id)
        self.lot_id = None
        self.refresh()
        self.changed.emit()

    def _selected_items(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.items[r] for r in rows if r < len(self.items)]

    def remove_items(self):
        items = self._selected_items()
        if not items:
            return
        for i in items:
            self.db.remove_from_sell_lot(i["table"], i["id"])
        self.refresh()
        self.changed.emit()

    def set_qty(self):
        items = [i for i in self._selected_items() if i["table"] == "parts"]
        if not items:
            QMessageBox.information(self, "Set Qty", "Select a part in the lot to change how "
                                    "many of it go in. Phones go in whole.")
            return
        i = items[0]
        stock = int(float(self.db.get("parts", i["id"]).get("quantity") or 1))
        qty, ok = QInputDialog.getInt(self, "Set Qty", f"How many of {i['title']} go in the lot? "
                                      f"(in stock: {stock})", i["qty"], 1, max(1, stock))
        if ok:
            self.db.update("parts", i["id"], {"sell_lot_qty": qty})
            self.refresh()

    def _open(self, *_):
        items = self._selected_items()
        if not items:
            return
        i = items[0]
        if i["where"] == "Sold":
            where = "sold" if i["table"] == "devices" else "parts"
        else:
            where = {"Working": "working", "Faulty": "faulty", "Parts": "parts"}[i["where"]]
        self.open_item.emit(where, i["id"])
