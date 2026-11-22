"""Job lots: phones / parts bought together for one price, with profit or loss per item."""
from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import theme
from .db import num, today_iso
from .widgets import UI, money

SPLITS = [("even", "Split the price evenly"),
          ("list", "Split by list price (pricier items carry more cost)"),
          ("manual", "I'll set each item's cost myself")]
NEW_LOT = "__new__"


def _money_box():
    w = QDoubleSpinBox()
    w.setRange(0, 10_000_000)
    w.setDecimals(2)
    w.setPrefix(UI["currency"])
    w.setAlignment(Qt.AlignRight)
    return w


def _pl_html(value, suffix=""):
    c = theme.current
    colour = c["good"] if value >= 0 else c["bad"]
    word = "Profit" if value >= 0 else "Loss"
    return f"<span style='color:{colour}'><b>{word} {money(value)}</b></span>{suffix}"


class LotForm(QWidget):
    """Name / date / price / split fields, shared by the dialog and the tab."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        f = QFormLayout(self)
        f.setContentsMargins(0, 0, 0, 0)
        self.name = QLineEdit(placeholderText="e.g. eBay job lot 12 Nov - 6 iPhones")
        self.date = QDateEdit(QDate.currentDate(), calendarPopup=True)
        self.date.setDisplayFormat("dd/MM/yyyy")
        self.source = QComboBox(editable=True)
        self.source.addItems([""] + db.get_list("sources"))
        self.total = _money_box()
        self.split = QComboBox()
        for key, label in SPLITS:
            self.split.addItem(label, key)
        f.addRow("Name", self.name)
        f.addRow("Bought on", self.date)
        f.addRow("Bought from", self.source)
        f.addRow("Total paid", self.total)
        f.addRow("Cost per item", self.split)

    def set_lot(self, lot):
        self.name.setText(lot.get("name") or "")
        d = QDate.fromString(str(lot.get("purchase_date") or "")[:10], "yyyy-MM-dd")
        self.date.setDate(d if d.isValid() else QDate.currentDate())
        self.source.setCurrentText(lot.get("source") or "")
        self.total.setValue(num(lot.get("total_cost")))
        self.split.setCurrentIndex(max(0, self.split.findData(lot.get("split") or "even")))

    def values(self):
        return {"name": self.name.text().strip() or "Job lot",
                "purchase_date": self.date.date().toString("yyyy-MM-dd"),
                "source": self.source.currentText().strip(),
                "total_cost": round(self.total.value(), 2),
                "split": self.split.currentData()}


class AddToJobLotDialog(QDialog):
    """Pick an existing lot or create one, for the selected items."""

    def __init__(self, db, items, parent=None):
        super().__init__(parent)
        self.db, self.items = db, items   # items: [(table, id, label)]
        self.setWindowTitle("Add to Job Lot")
        lay = QVBoxLayout(self)
        names = ", ".join(lbl for _, _, lbl in items[:4]) + (" ..." if len(items) > 4 else "")
        head = QLabel(f"<b>{len(items)} item(s):</b> {names}")
        head.setWordWrap(True)
        lay.addWidget(head)
        row = QHBoxLayout()
        row.addWidget(QLabel("Job lot"))
        self.lot = QComboBox()
        self.lot.addItem("New job lot...", NEW_LOT)
        for lot in db.joblots():
            self.lot.addItem(f"{lot['name']}  ({money(lot['total_cost'])})", lot["id"])
        row.addWidget(self.lot, 1)
        lay.addLayout(row)
        self.form = LotForm(db)
        lay.addWidget(self.form)
        self.lot.currentIndexChanged.connect(self._lot_changed)
        hint = QLabel("The total paid is shared across everything in the lot, and every item "
                      "then shows its own profit or loss.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Add to Lot")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if self.lot.count() > 1:   # most recent lot first, ready to add to
            self.lot.setCurrentIndex(1)
        self._lot_changed()

    def _lot_changed(self, *_):
        lot_id = self.lot.currentData()
        self.form.setEnabled(lot_id == NEW_LOT)
        if lot_id != NEW_LOT:
            self.form.set_lot(self.db.joblot(lot_id))
        else:
            self.form.set_lot({"purchase_date": today_iso(), "split": "even"})
            self.form.name.setFocus()

    def _ok(self):
        lot_id = self.lot.currentData()
        if lot_id == NEW_LOT:
            if not self.form.total.value() and QMessageBox.question(
                    self, "Add to Job Lot", "The total paid is £0. Carry on?") != QMessageBox.Yes:
                return
            lot_id = self.db.save_joblot(self.form.values())
        self.db.add_to_joblot(lot_id, [(t, i) for t, i, _ in self.items])
        self.lot_id = lot_id
        self.accept()


class JobLotsTab(QWidget):
    open_item = Signal(str, int)   # where ("working" / "faulty" / "parts" / "sold"), id
    changed = Signal()

    COLS = ["SKU", "Item", "Where", "Units", "Cost / Unit", "Total Cost", "Sold", "Net Sales",
            "Profit / Loss"]

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.lot_id = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        split = QSplitter()
        lay.addWidget(split, 1)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Job Lots")
        title.setObjectName("h2")
        ll.addWidget(title)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._lot_selected)
        ll.addWidget(self.list, 1)
        new = QPushButton("New Job Lot")
        new.setProperty("primary", True)
        new.clicked.connect(self.new_lot)
        delete = QPushButton("Delete Job Lot")
        delete.clicked.connect(self.delete_lot)
        ll.addWidget(new)
        ll.addWidget(delete)
        hint = QLabel("Add phones or parts from their tabs: right-click → Add to Job Lot.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        ll.addWidget(hint)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.form = LotForm(db)
        top.addWidget(self.form, 3)
        self.form.setMinimumWidth(240)
        side = QVBoxLayout()
        save = QPushButton("Save Changes")
        save.clicked.connect(self.save_lot)
        side.addWidget(save)
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
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.doubleClicked.connect(self._open)
        rl.addWidget(self.table, 1)
        row = QHBoxLayout()
        remove = QPushButton("Remove From Lot")
        remove.clicked.connect(self.remove_items)
        open_btn = QPushButton("Open Item")
        open_btn.clicked.connect(self._open)
        row.addWidget(remove)
        row.addWidget(open_btn)
        row.addStretch(1)
        self.note = QLabel()
        self.note.setObjectName("muted")
        row.addWidget(self.note)
        rl.addLayout(row)
        split.addWidget(right)
        split.setSizes([260, 900])
        split.setChildrenCollapsible(False)
        left.setMinimumWidth(160)
        self.refresh()

    # ---------------------------------------------------------------- lots list
    def refresh(self):
        keep = self.lot_id
        self.list.blockSignals(True)
        self.list.clear()
        for lot in self.db.joblots():
            tot = self.db.joblot_report(lot["id"])["totals"]
            pl = tot["pl_so_far"]
            item = QListWidgetItem(f"{lot['name']}\n{tot['sold_units']}/{tot['units']} sold  ·  "
                                   f"{'+' if pl >= 0 else ''}{money(pl)}")
            item.setData(Qt.UserRole, lot["id"])
            item.setForeground(QColor(theme.current["good" if pl >= 0 else "bad"]))
            self.list.addItem(item)
        self.list.blockSignals(False)
        ids = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        if ids:
            self.list.setCurrentRow(ids.index(keep) if keep in ids else 0)
        else:
            self.lot_id = None
            self._show(None)

    def _lot_selected(self, item, _prev=None):
        self.lot_id = item.data(Qt.UserRole) if item else None
        self._show(self.lot_id)

    def select_lot(self, lot_id):
        self.lot_id = lot_id
        self.refresh()

    def _show(self, lot_id):
        self.form.setEnabled(lot_id is not None)
        self.table.setRowCount(0)
        if lot_id is None:
            self.summary.setText("No job lots yet. Select phones or parts and choose "
                                 "<b>Add to Job Lot</b>.")
            self.note.clear()
            return
        rep = self.db.joblot_report(lot_id)
        self.form.set_lot(rep["lot"])
        t = rep["totals"]
        lines = [f"Paid <b>{money(t['paid'])}</b>"
                 + (f" + repairs {money(t['repairs'])}" if t["repairs"] else "")
                 + f"  ·  {t['sold_units']} of {t['units']} sold for <b>{money(t['net_sales'])}</b> "
                 f"after fees &amp; postage",
                 "So far: " + _pl_html(t["pl_so_far"])]
        if t["remaining_value"]:
            lines.append("If the rest sell at their list price: "
                         + _pl_html(t["pl_potential"], " <span style='color:#888'>(estimate)</span>"))
        self.summary.setText("<br>".join(lines))
        self.items = rep["items"]
        c = theme.current
        italic = QFont()
        italic.setItalic(True)
        for i in self.items:
            r = self.table.rowCount()
            self.table.insertRow(r)
            pl_text = money(i["pl"]) + (" (est.)" if i["estimated"] else "")
            if i["unpriced"]:
                pl_text += " (unsold, no list price)"
            vals = [i["sku"], i["title"], i["where"], str(i["units"]), money(i["unit_cost"]),
                    money(i["cost"]), f"{i['sold_units']}/{i['units']}", money(i["net_sales"]),
                    pl_text]
            for col, v in enumerate(vals):
                cell = QTableWidgetItem(v or "")
                if col >= 3:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if col == 8:
                    cell.setForeground(QColor(c["good"] if i["pl"] >= 0 else c["bad"]))
                    if i["estimated"] or i["unpriced"]:
                        cell.setFont(italic)
                self.table.setItem(r, col, cell)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        diff = round(t["paid"] - t["allocated"], 2)
        if rep["lot"].get("split") == "manual" and abs(diff) >= 0.01:
            self.note.setText(f"Item costs add up to {money(t['allocated'])}: "
                              f"{money(abs(diff))} {'not yet shared out' if diff > 0 else 'more than paid'}")
        else:
            self.note.setText("Estimates use each item's list price minus your selling fees.")

    # ---------------------------------------------------------------- actions
    def new_lot(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("New Job Lot")
        lay = QVBoxLayout(dlg)
        form = LotForm(self.db)
        form.set_lot({"purchase_date": today_iso(), "split": "even"})
        lay.addWidget(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec():
            self.select_lot(self.db.save_joblot(form.values()))
            self.changed.emit()

    def save_lot(self):
        if self.lot_id is None:
            return
        self.db.save_joblot(self.form.values(), self.lot_id)
        self.refresh()
        self.changed.emit()

    def delete_lot(self):
        if self.lot_id is None:
            return
        lot = self.db.joblot(self.lot_id)
        if QMessageBox.question(self, "Delete Job Lot",
                                f"Delete the job lot '{lot['name']}'?\n\nThe phones and parts "
                                "stay in your stock with their current costs.") != QMessageBox.Yes:
            return
        self.db.delete_joblot(self.lot_id)
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
        if QMessageBox.question(self, "Remove From Lot",
                                f"Remove {len(items)} item(s) from this lot? They stay in stock; "
                                "the lot price is shared across what's left.") != QMessageBox.Yes:
            return
        for i in items:
            self.db.remove_from_joblot(i["table"], i["id"])
        self.refresh()
        self.changed.emit()

    def _open(self, *_):
        items = self._selected_items()
        if not items:
            return
        i = items[0]
        where = {"Working": "working", "Faulty": "faulty", "Parts": "parts", "Sold": "sold",
                 "Written off": "faulty"}.get(i["where"], "working")
        if i["table"] == "devices" and i["where"] == "Written off":
            where = self.db.get("devices", i["id"]).get("category") or "working"
        self.open_item.emit(where, i["id"])
