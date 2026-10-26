import datetime as dt
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
                               QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from . import backup, fileio
from .paths import is_android
from .db import calc_profit, device_title, imei_valid, num, part_title, today_iso
from .fields import (DEVICE_FIELDS, EXCLUDED_SALE_STATUSES, PART_FIELDS, SALE_STATUSES)
from .widgets import UI, FormPanel, NotesWidget, PhotosWidget, fmt_ts, money

KEEP_ON_ADD_ANOTHER = {"brand", "model", "storage", "source", "purchase_date", "location",
                       "network", "purchase_price", "part_type", "compatible", "condition",
                       "unit_cost", "status", "grade"}


def _button_row(*buttons):
    row = QHBoxLayout()
    row.addStretch(1)
    for b in buttons:
        row.addWidget(b)
    return row


# ============================================================================ item
class ItemDialog(QDialog):
    """Add / edit a device (working or faulty) or a part."""

    def __init__(self, db, table, record=None, category="working", parent=None):
        super().__init__(parent)
        self.db, self.table = db, table
        self.record = record
        self.category = (record or {}).get("category", category)
        self.entity = "device" if table == "devices" else "part"
        self.saved_id = record["id"] if record else None
        fields = DEVICE_FIELDS if table == "devices" else PART_FIELDS
        self.resize(980, 760)

        lay = QVBoxLayout(self)
        self.header = QLabel()
        self.header.setObjectName("h2")
        lay.addWidget(self.header)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        self.form = FormPanel(db, table, fields)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.addWidget(self.form)
        scroll.setWidget(holder)
        self.tabs.addTab(scroll, "Details")
        self.notes = NotesWidget(db, self.entity, self.saved_id)
        self.tabs.addTab(self.notes, "Notes")
        self.photos = PhotosWidget(db, self.entity, self.saved_id)
        self.tabs.addTab(self.photos, "Photos")

        if table == "devices":
            self.form.widgets["imei"].editingFinished.connect(self._check_imei_live)
        self.imei_hint = QLabel()
        lay.addWidget(self.imei_hint)

        copy = QPushButton("Copy Listing Title")
        copy.setToolTip("Copy an eBay style title for this item to the clipboard")
        copy.clicked.connect(self._copy_title)
        save = QPushButton("Save")
        save.setProperty("primary", True)
        save.setDefault(True)
        save.clicked.connect(lambda: self._save(close=True))
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(copy)
        row.addStretch(1)
        if not record:
            again = QPushButton("Save && Add Another")
            again.clicked.connect(lambda: self._save(close=False))
            row.addWidget(again)
        row.addWidget(cancel)
        row.addWidget(save)
        lay.addLayout(row)

        if record:
            self.form.set_values(record)
        else:
            self.form.set_values(self._defaults())
        self._update_header()

    def _defaults(self):
        if self.table == "devices":
            return {"status": "In Stock" if self.category == "working" else "Awaiting Repair",
                    "quantity": 1, "purchase_date": today_iso(),
                    "grade": "For Parts / Not Working" if self.category == "faulty" else ""}
        return {"status": "In Stock", "quantity": 1, "purchase_date": today_iso()}

    def _kind_label(self):
        if self.table == "parts":
            return "Part"
        return "Working Phone" if self.category == "working" else "Faulty Device"

    def _update_header(self):
        if self.saved_id:
            rec = self.db.get(self.table, self.saved_id)
            title = device_title(rec) if self.table == "devices" else part_title(rec)
            self.header.setText(f"{rec.get('sku')}  ·  {title}")
            self.setWindowTitle(f"Edit {self._kind_label()} - {rec.get('sku')}")
        else:
            self.header.setText(f"New {self._kind_label()}")
            self.setWindowTitle(f"Add {self._kind_label()}")

    def _current_title(self):
        data = {**self.form.values(), "category": self.category}
        return device_title(data) if self.table == "devices" else part_title(data)

    def _copy_title(self):
        QGuiApplication.clipboard().setText(self._current_title()[:80])
        self.imei_hint.setText("Listing title copied to clipboard.")

    def _check_imei_live(self):
        imei = self.form.value_of("imei")
        msgs = []
        if imei and not imei_valid(imei):
            msgs.append("IMEI does not pass the checksum - double check it.")
        dupes = self.db.find_imei(imei, exclude_id=self.saved_id) if imei else []
        if dupes:
            msgs.append("Same IMEI already on " + ", ".join(d["sku"] for d in dupes))
        colour = "#f87171" if msgs else ""
        self.imei_hint.setStyleSheet(f"color: {colour}")
        self.imei_hint.setText("  ".join(msgs))
        return msgs

    def _save(self, close):
        missing = self.form.missing_required()
        if missing:
            QMessageBox.warning(self, "Missing Information",
                                "Please fill in: " + ", ".join(missing))
            return
        if self.table == "devices":
            msgs = self._check_imei_live()
            if msgs and QMessageBox.question(
                    self, "Check IMEI", "\n".join(msgs) + "\n\nSave anyway?") != QMessageBox.Yes:
                return
        data = self.form.values()
        if self.saved_id:
            self.db.update(self.table, self.saved_id, data)
            self.db.log("Edited", self.entity, self.saved_id, self._current_title())
        else:
            if self.table == "devices":
                data["category"] = self.category
            new_id = self.db.insert(self.table, data)
            self.notes.flush(new_id)
            self.photos.flush(new_id)
            sku = self.db.get(self.table, new_id)["sku"]
            self.db.log("Added", self.entity, new_id, f"{sku} {self._current_title()}")
            self.last_added = new_id
            if not close:
                keep = {k: v for k, v in data.items() if k in KEEP_ON_ADD_ANOTHER}
                self.form.set_values({f.key: "" if f.kind not in ("money", "int", "percent")
                                      else 0 for f in self.form.fields})
                self.form.set_values({**self._defaults(), **keep})
                self.notes.entity_id = None
                self.photos.entity_id = None
                self.notes.reload()
                self.photos.reload()
                self.imei_hint.setText(f"Saved {sku}. Ready for the next one.")
                self.added_any = True
                self.form.widgets["imei" if self.table == "devices" else "quantity"].setFocus()
                return
        self.accept()


# ============================================================================ notes / photos
class NotesDialog(QDialog):
    def __init__(self, db, entity, entity_id, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Notes - {title}")
        self.resize(700, 600)
        lay = QVBoxLayout(self)
        head = QLabel(title)
        head.setObjectName("h2")
        lay.addWidget(head)
        tabs = QTabWidget()
        tabs.addTab(NotesWidget(db, entity, entity_id), "Notes")
        tabs.addTab(PhotosWidget(db, entity, entity_id), "Photos")
        lay.addWidget(tabs, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay.addLayout(_button_row(close))


# ============================================================================ sale
class SaleDialog(QDialog):
    """Mark an item as sold (kind + item) or edit an existing sale (sale)."""

    def __init__(self, db, kind=None, item=None, sale=None, parent=None):
        super().__init__(parent)
        self.db, self.kind, self.item, self.sale = db, kind, item, sale
        self.setWindowTitle("Edit Sale" if sale else "Mark as Sold")
        self.resize(720, 640)
        cur = UI["currency"]
        if sale:
            self.kind = sale.get("item_kind")
            title, sku, imei = sale.get("title"), sale.get("sku"), sale.get("imei")
            available = 999_999
        else:
            title = device_title(item) if kind == "device" else part_title(item)
            sku, imei = item.get("sku"), item.get("imei")
            available = max(1, int(num(item.get("quantity"), 1)))
        self.unit_cost = 0.0
        if item:
            self.unit_cost = (num(item.get("purchase_price")) + num(item.get("repair_cost"))) \
                if kind == "device" else num(item.get("unit_cost"))

        lay = QVBoxLayout(self)
        head = QLabel(f"{sku}  ·  {title}" + (f"   IMEI {imei}" if imei else ""))
        head.setObjectName("h2")
        head.setWordWrap(True)
        lay.addWidget(head)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)
        page = QWidget()
        tabs.addTab(page, "Sale")
        grid = QGridLayout(page)

        def money_box():
            w = QDoubleSpinBox()
            w.setRange(-1_000_000, 10_000_000)
            w.setDecimals(2)
            w.setPrefix(cur)
            w.setAlignment(Qt.AlignRight)
            w.valueChanged.connect(self._recalc)
            return w

        self.title = QLineEdit(title or "")
        self.title.setCursorPosition(0)
        self.date = QDateEdit(QDate.currentDate(), calendarPopup=True)
        self.date.setDisplayFormat("dd/MM/yyyy")
        self.platform = QComboBox(editable=True)
        self.platform.addItems(db.get_list("platforms"))
        self.platform.setCurrentText(db.setting("default_platform"))
        self.order = QLineEdit()
        self.buyer = QLineEdit()
        self.qty = QSpinBox()
        self.qty.setRange(1, available)
        self.qty.setSuffix(f"  of {available}" if not sale and available > 1 else "")
        self.qty.valueChanged.connect(self._qty_changed)
        self.price = money_box()
        self.pp_charged = money_box()
        self.fees = money_box()
        self.auto_fees = QCheckBox(f"Auto ({db.setting('fee_percent')}% + "
                                   f"{cur}{num(db.setting('fee_fixed')):.2f})")
        self.auto_fees.setChecked(not sale)
        self.auto_fees.toggled.connect(self._recalc)
        self.postage = money_box()
        self.other = money_box()
        self.cost = money_box()
        self.cost.setValue(self.unit_cost)
        self.status = QComboBox()
        self.status.addItems(SALE_STATUSES)
        self.status.setCurrentText("Paid")
        self.tracking = QLineEdit()

        rows = [("Item title", self.title), ("Sold on", self.date), ("Platform", self.platform),
                ("Order number", self.order), ("Buyer", self.buyer), ("Quantity", self.qty),
                ("Status", self.status), ("Tracking", self.tracking)]
        money_rows = [("Sale price", self.price), ("P&P charged to buyer", self.pp_charged),
                      ("Selling fees", self.fees), ("", self.auto_fees),
                      ("Postage cost", self.postage), ("Other costs", self.other),
                      ("Item cost (total)", self.cost)]
        for i, (lbl, w) in enumerate(rows):
            grid.addWidget(QLabel(lbl), i, 0, Qt.AlignRight)
            grid.addWidget(w, i, 1)
        for i, (lbl, w) in enumerate(money_rows):
            grid.addWidget(QLabel(lbl), i, 2, Qt.AlignRight)
            grid.addWidget(w, i, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.profit_label = QLabel()
        self.profit_label.setObjectName("h2")
        grid.addWidget(self.profit_label, max(len(rows), len(money_rows)) + 1, 0, 1, 4,
                       Qt.AlignCenter)
        grid.setRowStretch(max(len(rows), len(money_rows)) + 2, 1)

        self.notes = NotesWidget(db, "sale", sale["id"] if sale else None)
        tabs.addTab(self.notes, "Notes")

        if sale:
            self.title.setText(sale.get("title") or "")
            self.title.setCursorPosition(0)
            d = QDate.fromString(str(sale.get("sale_date") or "")[:10], "yyyy-MM-dd")
            if d.isValid():
                self.date.setDate(d)
            self.platform.setCurrentText(sale.get("platform") or "")
            self.order.setText(sale.get("order_number") or "")
            self.buyer.setText(sale.get("buyer") or "")
            self.qty.setValue(int(num(sale.get("quantity"), 1)) or 1)
            self.price.setValue(num(sale.get("sale_price")))
            self.pp_charged.setValue(num(sale.get("postage_charged")))
            self.fees.setValue(num(sale.get("fees")))
            self.postage.setValue(num(sale.get("postage_cost")))
            self.other.setValue(num(sale.get("other_costs")))
            self.cost.setValue(num(sale.get("cost_basis")))
            self.status.setCurrentText(sale.get("status") or "Paid")
            self.tracking.setText(sale.get("tracking") or "")
        elif item and item.get("list_price"):
            self.price.setValue(num(item.get("list_price")))

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setProperty("primary", True)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._recalc()
        self.price.setFocus()
        self.price.selectAll()

    def _qty_changed(self, q):
        if not self.sale:
            self.cost.setValue(round(self.unit_cost * q, 2))
        self._recalc()

    def _recalc(self):
        if not hasattr(self, "profit_label"):
            return  # still building the widgets
        self.fees.setEnabled(not self.auto_fees.isChecked())
        if self.auto_fees.isChecked():
            gross = self.price.value() + self.pp_charged.value()
            fee = gross * num(self.db.setting("fee_percent")) / 100 + \
                (num(self.db.setting("fee_fixed")) if gross > 0 else 0)
            self.fees.blockSignals(True)
            self.fees.setValue(round(fee, 2))
            self.fees.blockSignals(False)
        data = self._data()
        profit = calc_profit(data)
        gross = data["sale_price"] + data["postage_charged"]
        margin = f"   ({profit / gross * 100:.1f}% margin)" if gross else ""
        colour = "#4ade80" if profit >= 0 else "#f87171"
        self.profit_label.setText(f"Profit: <span style='color:{colour}'>{money(profit)}</span>"
                                  f"{margin}")

    def _data(self):
        return {"title": self.title.text().strip(),
                "sale_date": self.date.date().toString("yyyy-MM-dd"),
                "platform": self.platform.currentText().strip(),
                "order_number": self.order.text().strip(), "buyer": self.buyer.text().strip(),
                "quantity": self.qty.value(), "sale_price": round(self.price.value(), 2),
                "postage_charged": round(self.pp_charged.value(), 2),
                "fees": round(self.fees.value(), 2), "postage_cost": round(self.postage.value(), 2),
                "other_costs": round(self.other.value(), 2),
                "cost_basis": round(self.cost.value(), 2),
                "status": self.status.currentText(), "tracking": self.tracking.text().strip()}

    def _save(self):
        data = self._data()
        if data["sale_price"] <= 0 and QMessageBox.question(
                self, "No Sale Price", "Sale price is zero. Save anyway?") != QMessageBox.Yes:
            return
        if self.sale:
            self.db.update_sale(self.sale["id"], data)
            self.db.log("Edited", "sale", self.sale["id"], data["title"])
        else:
            it = self.item
            data.update({"sku": it.get("sku"), "brand": it.get("brand"),
                         "model": it.get("model") if self.kind == "device" else it.get("compatible"),
                         "imei": it.get("imei"), "serial": it.get("serial")})
            sale_id = self.db.record_sale(self.kind, it["id"], data)
            self.notes.flush(sale_id)
        self.accept()


class AdjustmentDialog(QDialog):
    """Past sales (or any correction) entered as one lump instead of phone by phone."""

    def __init__(self, db, sale=None, parent=None):
        super().__init__(parent)
        self.db, self.sale = db, sale
        self.setWindowTitle("Edit Past Sales" if sale else "Add Past Sales / Profit Adjustment")
        self.resize(560, 420)
        cur = UI["currency"]
        lay = QVBoxLayout(self)
        intro = QLabel("Add sales you made before using the app (or any correction) as one total. "
                       "It counts in every profit and revenue figure, the running profit and, "
                       "if you give it a date, that month in the chart.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.title = QLineEdit("Sales before Zaf Inventory")
        self.dated = QCheckBox("Put it in a month")
        self.date = QDateEdit(QDate.currentDate(), calendarPopup=True)
        self.date.setDisplayFormat("dd/MM/yyyy")
        self.dated.toggled.connect(self.date.setEnabled)
        self.date.setEnabled(False)
        drow = QHBoxLayout()
        drow.addWidget(self.dated)
        drow.addWidget(self.date, 1)
        self.qty = QSpinBox()
        self.qty.setRange(0, 1_000_000)
        self.qty.setSuffix(" phones / items")
        self.revenue = QDoubleSpinBox()
        self.profit = QDoubleSpinBox()
        for w in (self.revenue, self.profit):
            w.setRange(-10_000_000, 10_000_000)
            w.setDecimals(2)
            w.setPrefix(cur)
            w.setAlignment(Qt.AlignRight)
        form.addRow("Description", self.title)
        form.addRow("Date", drow)
        form.addRow("Number sold", self.qty)
        form.addRow("Total revenue (optional)", self.revenue)
        form.addRow("Total profit", self.profit)
        lay.addLayout(form)
        hint = QLabel("Undated entries count in the all-time totals only. A loss can be entered "
                      "as a negative profit.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch(1)
        if sale:
            self.title.setText(sale.get("title") or "")
            d = QDate.fromString(str(sale.get("sale_date") or "")[:10], "yyyy-MM-dd")
            if d.isValid():
                self.dated.setChecked(True)
                self.date.setDate(d)
            self.qty.setValue(int(num(sale.get("quantity"))))
            self.revenue.setValue(num(sale.get("sale_price")))
            self.profit.setValue(num(sale.get("profit")))
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _save(self):
        if not self.profit.value() and not self.revenue.value():
            QMessageBox.information(self, "Past Sales", "Enter the total profit (and revenue if "
                                                        "you know it).")
            return
        self.saved_id = self.db.save_adjustment({
            "title": self.title.text(), "quantity": self.qty.value(),
            "sale_date": self.date.date().toString("yyyy-MM-dd") if self.dated.isChecked() else "",
            "revenue": self.revenue.value(), "profit": self.profit.value()},
            sale_id=self.sale["id"] if self.sale else None)
        self.accept()


class ReturnDialog(QDialog):
    def __init__(self, sale, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Return / Refund / Cancel")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>{sale.get('sku')}</b>  {sale.get('title')}"))
        form = QFormLayout()
        self.status = QComboBox()
        self.status.addItems(EXCLUDED_SALE_STATUSES)
        self.restock = QCheckBox("Put the item back into stock")
        self.restock.setChecked(True)
        form.addRow("New status", self.status)
        form.addRow("", self.restock)
        lay.addLayout(form)
        info = QLabel("The sale stays in your records but is left out of revenue and profit.")
        info.setObjectName("muted")
        info.setWordWrap(True)
        lay.addWidget(info)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


# ============================================================================ use part
class UsePartDialog(QDialog):
    def __init__(self, db, device, parent=None):
        super().__init__(parent)
        self.db, self.device = db, device
        self.setWindowTitle(f"Use Part on {device.get('sku')}")
        self.resize(900, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>{device.get('sku')}</b>  {device_title(device)}"
                             + (f"<br>Faults: {device.get('faults')}" if device.get("faults") else "")))
        self.search = QLineEdit(placeholderText="Search parts (type, model, SKU)...")
        self.search.setText(device.get("model") or "")
        self.search.textChanged.connect(self._fill)
        lay.addWidget(self.search)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["SKU", "Type", "Brand", "Compatible", "Condition",
                                              "Qty", "Unit Cost"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.doubleClicked.connect(self._ok)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Quantity to use:"))
        self.qty = QSpinBox()
        self.qty.setRange(1, 1000)
        row.addWidget(self.qty)
        row.addStretch(1)
        ok = QPushButton("Fit Part")
        ok.setProperty("primary", True)
        ok.clicked.connect(self._ok)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self.parts = [p for p in db.list_parts()
                      if int(num(p.get("quantity"))) > 0 and not p["written_off"]]
        self._fill()
        if self.table.rowCount() == 0:
            self.search.clear()

    def _fill(self):
        words = self.search.text().lower().split()
        self.table.setRowCount(0)
        for p in self.parts:
            hay = " ".join(str(p.get(k) or "") for k in
                           ("sku", "part_type", "brand", "compatible", "part_number",
                            "condition", "description")).lower()
            if any(w not in hay for w in words):
                continue
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = [p["sku"], p["part_type"], p["brand"], p["compatible"], p["condition"],
                    str(p["quantity"]), money(p["unit_cost"])]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v or "")
                item.setData(Qt.UserRole, p["id"])
                self.table.setItem(r, c, item)
        if self.table.rowCount():
            self.table.selectRow(0)

    def _ok(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Use Part", "Select a part first.")
            return
        part_id = self.table.item(row, 0).data(Qt.UserRole)
        part = self.db.get("parts", part_id)
        qty = self.qty.value()
        if qty > int(num(part.get("quantity"))):
            QMessageBox.warning(self, "Use Part", f"Only {part.get('quantity')} in stock.")
            return
        cost = self.db.use_part(part_id, self.device["id"], qty)
        self.result_text = f"Fitted {qty} x {part.get('sku')} (+{money(cost)} repair cost)"
        self.accept()


# ============================================================================ settings
class SettingsDialog(QDialog):
    LISTS = [("brands", "Brands"), ("faults", "Fault List"), ("part_types", "Part Types"),
             ("sources", "Bought From"), ("platforms", "Sale Platforms"),
             ("locations", "Storage Locations")]

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Settings")
        self.resize(720, 560)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        gen = QWidget()
        f = QFormLayout(gen)
        self.currency = QLineEdit(db.setting("currency"))
        self.currency.setMaxLength(4)
        self.theme = QComboBox()
        self.theme.addItems(["dark", "light"])
        self.theme.setCurrentText(db.setting("theme"))
        self.aged = QSpinBox()
        self.aged.setRange(1, 3650)
        self.aged.setValue(int(num(db.setting("aged_days"), 60)))
        self.aged.setSuffix(" days")
        self.fee_pct = QDoubleSpinBox()
        self.fee_pct.setRange(0, 100)
        self.fee_pct.setDecimals(2)
        self.fee_pct.setSuffix(" %")
        self.fee_pct.setValue(num(db.setting("fee_percent")))
        self.fee_fixed = QDoubleSpinBox()
        self.fee_fixed.setRange(0, 1000)
        self.fee_fixed.setDecimals(2)
        self.fee_fixed.setValue(num(db.setting("fee_fixed")))
        self.platform = QComboBox(editable=True)
        self.platform.addItems(db.get_list("platforms"))
        self.platform.setCurrentText(db.setting("default_platform"))
        self.business = QLineEdit(db.setting("report_business"))
        self.business.setPlaceholderText("Shown at the top of PDF test reports")
        self.tester = QLineEdit(db.setting("tester_name"))
        self.tester.setPlaceholderText("Pre-filled as 'Tested by' on test reports")
        f.addRow("Currency symbol", self.currency)
        f.addRow("Business name on reports", self.business)
        f.addRow("Default tester name", self.tester)
        f.addRow("Theme", self.theme)
        f.addRow("Flag stock as aged after", self.aged)
        f.addRow("Selling fee (percentage)", self.fee_pct)
        f.addRow("Selling fee (fixed per order)", self.fee_fixed)
        f.addRow("Default sale platform", self.platform)
        note = QLabel("Fees are used to pre-fill the 'Selling fees' box when marking an item "
                      "sold. You can always override them per sale.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        f.addRow(note)
        tabs.addTab(gen, "General")

        bk = QWidget()
        f = QFormLayout(bk)
        self.bdir = QLineEdit(str(backup.backup_dir(db)))
        self.bdir2 = QLineEdit(db.setting("backup_dir_secondary"))
        self.bdir2.setPlaceholderText("Optional - e.g. a OneDrive / Google Drive / USB folder")
        f.addRow("Backup folder", self._with_browse(self.bdir))
        f.addRow("Extra copy to", self._with_browse(self.bdir2))
        self.auto = QCheckBox("Automatically back up")
        self.auto.setChecked(db.setting("auto_backup") == "1")
        self.hours = QSpinBox()
        self.hours.setRange(1, 24 * 30)
        self.hours.setSuffix(" hours")
        self.hours.setValue(int(num(db.setting("auto_backup_hours"), 24)))
        self.on_exit = QCheckBox("Back up when closing the app")
        self.on_exit.setChecked(db.setting("backup_on_exit") == "1")
        self.keep = QSpinBox()
        self.keep.setRange(1, 1000)
        self.keep.setValue(int(num(db.setting("backup_keep"), 30)))
        f.addRow("", self.auto)
        f.addRow("Every", self.hours)
        f.addRow("", self.on_exit)
        f.addRow("Keep this many automatic backups", self.keep)
        hint = QLabel("Manual backups are never deleted automatically. Tip: point 'Extra copy to' "
                      "at a cloud-synced folder so you have an off-site copy.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        f.addRow(hint)
        tabs.addTab(bk, "Backups")

        lists = QWidget()
        g = QGridLayout(lists)
        self.list_edits = {}
        for i, (key, label) in enumerate(self.LISTS):
            box = QPlainTextEdit("\n".join(db.get_list(key)))
            self.list_edits[key] = box
            g.addWidget(QLabel(f"<b>{label}</b> (one per line)"), (i // 3) * 2, i % 3)
            g.addWidget(box, (i // 3) * 2 + 1, i % 3)
        tabs.addTab(lists, "Lists")

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _with_browse(self, edit):
        if is_android():  # Android folders are picked per file, not typed in
            edit.setReadOnly(True)
            return edit
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit, 1)
        b = QPushButton("Browse...")
        b.clicked.connect(lambda: edit.setText(
            QFileDialog.getExistingDirectory(self, "Choose Folder", edit.text()) or edit.text()))
        h.addWidget(b)
        return w

    def _save(self):
        db = self.db
        db.set_setting("currency", self.currency.text().strip() or "£")
        db.set_setting("report_business", self.business.text().strip())
        db.set_setting("tester_name", self.tester.text().strip())
        db.set_setting("theme", self.theme.currentText())
        db.set_setting("aged_days", self.aged.value())
        db.set_setting("fee_percent", self.fee_pct.value())
        db.set_setting("fee_fixed", self.fee_fixed.value())
        db.set_setting("default_platform", self.platform.currentText())
        db.set_setting("backup_dir", self.bdir.text().strip())
        db.set_setting("backup_dir_secondary", self.bdir2.text().strip())
        db.set_setting("auto_backup", "1" if self.auto.isChecked() else "0")
        db.set_setting("auto_backup_hours", self.hours.value())
        db.set_setting("backup_on_exit", "1" if self.on_exit.isChecked() else "0")
        db.set_setting("backup_keep", self.keep.value())
        for key, box in self.list_edits.items():
            db.set_list(key, [l.strip() for l in box.toPlainText().splitlines()])
        self.accept()


# ============================================================================ backups
def restore_with_prompt(parent, db, path) -> bool:
    """Confirm, restore a backup zip and report the result. True if data was replaced."""
    name = fileio.display_name(path)
    if QMessageBox.warning(
            parent, "Restore Backup",
            f"Replace ALL current data with the backup:\n{name}\n\n"
            "A safety backup of your current data is taken first, so this can be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
        return False
    try:
        path = fileio.local_copy(path, ".zip")
        safety = backup.restore_backup(db, path)
    except Exception as exc:
        import traceback
        from .paths import data_dir
        try:
            with open(data_dir() / "error.log", "a", encoding="utf-8") as fh:
                fh.write(f"Restore of {path} failed:\n{traceback.format_exc()}\n")
        except OSError:
            pass
        QMessageBox.critical(parent, "Restore Failed",
                             f"Could not restore {Path(path).name}:\n\n{exc}\n\n"
                             "Your current data has not been changed. Details are in "
                             "error.log in the data folder.")
        return False
    QMessageBox.information(parent, "Restore Complete",
                            f"Data restored from {name}.\n\n"
                            f"Your previous data was saved as:\n{safety.name}")
    return True


def choose_backup_file(parent, db):
    path, _ = QFileDialog.getOpenFileName(parent, "Import Backup", str(Path.home() / "Downloads"),
                                          "Zaf Inventory backup (*.zip);;All files (*)")
    return path


class BackupDialog(QDialog):
    restored = Signal()

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Backup && Restore")
        self.resize(860, 520)
        lay = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Created", "Type", "Size", "Contents", "File"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        actions = [("Back Up Now", self._backup), ("Save a Copy As...", self._backup_to),
                   ("Verify", self._verify), ("Restore Selected", self._restore_selected),
                   ("Restore From File...", self._restore_file), ("Open Folder", self._open),
                   ("Delete", self._delete)]
        for text, fn in actions:
            if text == "Open Folder" and is_android():
                continue  # app storage can't be browsed on Android
            b = QPushButton(text)
            b.clicked.connect(fn)
            if text == "Back Up Now":
                b.setProperty("primary", True)
            row.addWidget(b)
        lay.addLayout(row)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay.addLayout(_button_row(close))
        self.reload()

    def reload(self):
        last = self.db.setting("last_backup")
        self.info.setText(f"Backup folder: <b>{backup.backup_dir(self.db)}</b><br>"
                          f"Last backup: <b>{fmt_ts(last) if last else 'never'}</b>")
        self.files = backup.list_backups(self.db)
        self.table.setRowCount(len(self.files))
        for r, p in enumerate(self.files):
            try:
                m = backup.read_manifest(p)
            except Exception:
                m = {}
            counts = m.get("counts", {})
            contents = (f"{counts.get('devices', '?')} devices, {counts.get('parts', '?')} parts, "
                        f"{counts.get('sales', '?')} sales, {counts.get('photos', '?')} photos") \
                if counts else "?"
            vals = [fmt_ts(m.get("created")) or dt.datetime.fromtimestamp(
                p.stat().st_mtime).strftime("%d %b %Y %H:%M"),
                m.get("reason", ""), f"{p.stat().st_size / 1024 / 1024:.2f} MB", contents, p.name]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(v))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)

    def _selected(self):
        r = self.table.currentRow()
        return self.files[r] if 0 <= r < len(self.files) else None

    def _backup(self):
        try:
            p = backup.create_backup(self.db, "manual")
        except Exception as exc:
            QMessageBox.critical(self, "Backup Failed", str(exc))
            return
        self.reload()
        QMessageBox.information(self, "Backup Complete", f"Saved to:\n{p}")

    def _backup_to(self):
        """Back up, then save a copy anywhere (USB stick, cloud folder, Android Downloads...)."""
        try:
            made = backup.create_backup(self.db, "manual")
        except Exception as exc:
            QMessageBox.critical(self, "Backup Failed", str(exc))
            return
        self.reload()
        path, _ = QFileDialog.getSaveFileName(self, "Save a Copy of the Backup", made.name,
                                              "Zaf Inventory backup (*.zip)")
        if not path:
            return
        try:
            fileio.write_bytes(path, made.read_bytes())
            QMessageBox.information(self, "Backup Complete",
                                    f"Saved a copy as:\n{fileio.display_name(path)}")
        except OSError as exc:
            QMessageBox.critical(self, "Backup Failed", str(exc))

    def _verify(self):
        p = self._selected()
        if not p:
            return
        try:
            m = backup.verify_backup(p)
            QMessageBox.information(self, "Backup OK",
                                    f"{p.name}\n\nThe backup is complete and the database "
                                    f"passed its integrity check.\nCreated: {m.get('created', '?')}")
        except ValueError as exc:
            QMessageBox.critical(self, "Backup Problem", str(exc))

    def _restore(self, path):
        if restore_with_prompt(self, self.db, path):
            self.reload()
            self.restored.emit()

    def _restore_selected(self):
        p = self._selected()
        if p:
            self._restore(p)

    def _restore_file(self):
        f = choose_backup_file(self, self.db)
        if f:
            self._restore(f)

    def _open(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(backup.backup_dir(self.db))))

    def _delete(self):
        p = self._selected()
        if p and QMessageBox.question(self, "Delete Backup", f"Delete {p.name}?") \
                == QMessageBox.Yes:
            p.unlink(missing_ok=True)
            self.reload()


# ============================================================================ global search
class GlobalSearchDialog(QDialog):
    chosen = Signal(str, int)
    WHERE = {"working": "Working Phones", "faulty": "Faulty / Repairs", "parts": "Parts",
             "sales": "Sold", "sold": "Sold (device)"}

    def __init__(self, db, parent=None, text=""):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Search Everything")
        self.resize(900, 520)
        lay = QVBoxLayout(self)
        self.box = QLineEdit(text, placeholderText="IMEI, serial, SKU, model, fault, buyer, "
                                                   "order number, words in notes...")
        self.box.textChanged.connect(self._run)
        lay.addWidget(self.box)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Where", "SKU", "Item", "Detail", "Status"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.doubleClicked.connect(self._go)
        lay.addWidget(self.table, 1)
        hint = QLabel("Double-click a result to jump to it.")
        hint.setObjectName("muted")
        lay.addWidget(hint)
        self._run()

    def _run(self):
        text = self.box.text().strip()
        self.results = self.db.global_search(text) if len(text) >= 2 else []
        self.table.setRowCount(len(self.results))
        for r, res in enumerate(self.results):
            vals = [self.WHERE.get(res["where"], res["where"]), res["sku"], res["title"],
                    res["detail"], res["status"]]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(str(v or "")))
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)

    def _go(self):
        r = self.table.currentRow()
        if 0 <= r < len(self.results):
            res = self.results[r]
            self.chosen.emit(res["where"], res["id"])
            self.accept()
