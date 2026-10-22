"""Inventory tabs (working, faulty, parts, sold), activity log and the table model."""
import csv
import datetime as dt
import io

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QSettings, QSortFilterProxyModel,
                            Qt, Signal)
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu,
                               QMessageBox,
                               QPushButton, QTableView, QVBoxLayout, QWidget)

from . import APP_ID, fileio, theme
from .db import device_title, num, part_title, split_faults
from .device_test import TestDialog, export_report
from .dialogs import (ItemDialog, NotesDialog, ReturnDialog, SaleDialog, UsePartDialog,
                      restore_with_prompt)
from .fields import (DEVICE_FIELDS, DEVICE_STATUSES, EXCLUDED_SALE_STATUSES, NUMERIC_KINDS,
                     PART_FIELDS, PART_STATUSES, SALE_FIELDS, WRITTEN_OFF)
from .widgets import fmt_date, fmt_ts, money

NUMERIC = NUMERIC_KINDS | {"pct1", "notes", "days"}


def _columns(fields, extras, drop=()):
    cols = [(f.key, f.label, f.kind) for f in fields if f.kind != "multiline" and f.key not in drop]
    if any(e[0] == "total_cost" for e in extras):  # device tabs: test result sits beside Status
        at = next(i for i, c in enumerate(cols) if c[0] == "status") + 1
        cols.insert(at, ("test_summary", "Tests", "text"))
    return cols + extras


TAB_CONFIG = {
    "working": {
        "title": "Working Phones", "table": "devices", "entity": "device",
        "columns": _columns(DEVICE_FIELDS, [("total_cost", "Total Cost", "money"),
                                            ("est_profit", "Est. Profit", "money"),
                                            ("age_days", "Days In Stock", "days"),
                                            ("photo_count", "Photos", "int"),
                                            ("note_count", "Notes", "notes")]),
        "hidden": {"accessories", "ebay_item_id", "source", "repair_cost", "photo_count",
                   "purchase_date", "model_number", "ram", "battery_cycles", "os_version"},
        "filters": [("brand", "Brands"), ("model", "Models"), ("storage", "Storage"),
                    ("grade", "Grades"), ("network", "Networks"), ("faults", "Faults"),
                    ("status", "Statuses"), ("location", "Locations")],
    },
    "faulty": {
        "title": "Faulty / Repairs", "table": "devices", "entity": "device",
        "columns": _columns(DEVICE_FIELDS, [("total_cost", "Total Cost", "money"),
                                            ("est_profit", "Est. Profit", "money"),
                                            ("age_days", "Days In Stock", "days"),
                                            ("photo_count", "Photos", "int"),
                                            ("note_count", "Notes", "notes")]),
        "hidden": {"accessories", "ebay_item_id", "source", "battery_health", "photo_count",
                   "purchase_date", "est_profit", "model_number", "ram", "battery_cycles",
                   "os_version", "test_summary"},
        "filters": [("brand", "Brands"), ("model", "Models"), ("faults", "Faults"),
                    ("status", "Statuses"), ("grade", "Grades"), ("network", "Networks"),
                    ("location", "Locations")],
    },
    "parts": {
        "title": "Parts", "table": "parts", "entity": "part",
        "columns": _columns(PART_FIELDS, [("stock_value", "Stock Value", "money"),
                                          ("photo_count", "Photos", "int"),
                                          ("note_count", "Notes", "notes")]),
        "hidden": {"part_number", "min_qty", "source", "purchase_date", "ebay_item_id",
                   "photo_count"},
        "filters": [("part_type", "Part Types"), ("brand", "Brands"),
                    ("condition", "Conditions"), ("status", "Statuses"),
                    ("location", "Locations")],
    },
    "sales": {
        "title": "Sold", "table": "sales", "entity": "sale",
        "columns": _columns(SALE_FIELDS, [("margin", "Margin", "pct1"),
                                          ("running_profit", "Running Profit", "money"),
                                          ("note_count", "Notes", "notes")]),
        "hidden": {"brand", "model", "postage_charged", "other_costs", "tracking", "serial"},
        "filters": [("platform", "Platforms"), ("brand", "Brands"), ("status", "Statuses"),
                    ("item_kind", "Types")],
    },
}

PERIODS = ["All time", "Today", "This week", "This month", "Last month", "Last 30 days",
           "Last 90 days", "This year", "Last year"]


def period_range(name):
    today = dt.date.today()
    if name == "Today":
        return today, today
    if name == "This week":
        return today - dt.timedelta(days=today.weekday()), today
    if name == "This month":
        return today.replace(day=1), today
    if name == "Last month":
        end = today.replace(day=1) - dt.timedelta(days=1)
        return end.replace(day=1), end
    if name == "Last 30 days":
        return today - dt.timedelta(days=30), today
    if name == "Last 90 days":
        return today - dt.timedelta(days=90), today
    if name == "This year":
        return today.replace(month=1, day=1), today
    if name == "Last year":
        return dt.date(today.year - 1, 1, 1), dt.date(today.year - 1, 12, 31)
    return None, None


# ============================================================================ model
class RowsModel(QAbstractTableModel):
    def __init__(self, columns, parent=None):
        super().__init__(parent)
        self.columns = columns
        self.rows = []
        self.aged_days = 60

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        for r in rows:
            r["_hay"] = " ".join(str(v) for k, v in r.items()
                                 if v not in (None, "") and not k.startswith("_")).lower()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.columns[section][1]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        key, _, kind = self.columns[index.column()]
        v = row.get(key)
        if role == Qt.DisplayRole:
            if v in (None, ""):
                return ""
            if kind == "money":
                return money(v)
            if kind == "date":
                return fmt_date(v)
            if kind == "pct1":
                return f"{num(v):.1f}%"
            if kind == "percent":
                return f"{v}%"
            if kind == "notes":
                return f"✎ {v}" if v else ""
            if kind == "item_kind" or key == "item_kind":
                return str(v).title()
            return str(v)
        if role == Qt.UserRole:  # sort key
            if kind in NUMERIC:
                return num(v, float("-inf")) if v not in (None, "") else float("-inf")
            return str(v or "").lower()
        if role == Qt.TextAlignmentRole:
            if kind in NUMERIC:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            if key == "note_count" and row.get("latest_note"):
                return "Latest note:\n" + row["latest_note"]
            if key in ("faults", "compatible", "title") and v:
                return str(v).replace("; ", "\n")
            return None
        if role == Qt.ForegroundRole:
            c = theme.current
            if row.get("excluded") or row.get("written_off"):
                return QColor(c["muted"])
            if key in ("profit", "est_profit", "running_profit", "margin") and v not in (None, ""):
                return QColor(c["good"] if num(v) >= 0 else c["bad"])
            if key == "faults" and v:
                return QColor(c["warn"])
            if key == "test_summary":
                if row.get("test_fail"):
                    return QColor(c["bad"])
                return QColor(c["good"] if v == "All passed" else c["muted"])
            return None
        if role == Qt.BackgroundRole:
            c = theme.current
            if row.get("low_stock") and key in ("quantity", "sku", "part_type"):
                return QColor(c["warn_bg"])
            if key == "age_days" and v is not None and num(v) >= self.aged_days:
                return QColor(c["warn_bg"])
            if key == "status" and v in EXCLUDED_SALE_STATUSES:
                return QColor(c["bad_bg"])
            return None
        if role == Qt.FontRole and (row.get("excluded") or row.get("written_off")):
            f = QFont()
            f.setStrikeOut(True)
            return f
        return None


class FilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.words = []
        self.eq = {}
        self.extra = None
        self.setSortRole(Qt.UserRole)

    def set_filters(self, text, eq, extra=None):
        self.words = text.lower().split()
        self.eq = {k: v for k, v in eq.items() if v}
        self.extra = extra
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, parent):
        row = self.sourceModel().rows[source_row]
        hay = row["_hay"]
        if any(w not in hay for w in self.words):
            return False
        for key, value in self.eq.items():
            if key == "faults":
                if value not in split_faults(row.get("faults")):
                    return False
            elif str(row.get(key) or "") != value:
                return False
        if self.extra and not self.extra(row):
            return False
        return True


# ============================================================================ tab
class InventoryTab(QWidget):
    changed = Signal()  # data in this tab changed, others may need a refresh
    status_message = Signal(str)

    def __init__(self, db, kind, parent=None):
        super().__init__(parent)
        self.db, self.kind = db, kind
        self.cfg = TAB_CONFIG[kind]
        self.table_name = self.cfg["table"]
        self.entity = self.cfg["entity"]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        # ---- filter bar
        bar = QHBoxLayout()
        self.search = QLineEdit(placeholderText=f"Search {self.cfg['title'].lower()}... "
                                                "(any words: brand, model, IMEI, fault, note)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filters)
        bar.addWidget(self.search, 3)
        self.combos = {}
        if kind == "sales":
            self.period = QComboBox()
            self.period.addItems(PERIODS)
            self.period.currentIndexChanged.connect(self.apply_filters)
            bar.addWidget(self.period)
        for key, label in self.cfg["filters"]:
            cb = QComboBox()
            cb.setMinimumWidth(110)
            cb.setMaxVisibleItems(25)
            cb.setProperty("all_label", f"All {label}")
            cb.addItem(f"All {label}")
            cb.currentIndexChanged.connect(self.apply_filters)
            self.combos[key] = cb
            bar.addWidget(cb, 1)
        self.low_only = None
        if kind == "parts":
            self.low_only = QCheckBox("Low stock only")
            self.low_only.toggled.connect(self.apply_filters)
            bar.addWidget(self.low_only)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear_filters)
        bar.addWidget(clear)
        lay.addLayout(bar)

        # ---- table
        self.model = RowsModel(self.cfg["columns"], self)
        self.proxy = FilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setDefaultSectionSize(28)
        self.view.setWordWrap(False)
        self.view.doubleClicked.connect(lambda *_: self.edit())
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        hdr = self.view.horizontalHeader()
        hdr.setSectionsMovable(True)
        hdr.setStretchLastSection(False)
        hdr.setContextMenuPolicy(Qt.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._header_menu)
        hdr.sectionMoved.connect(self._save_header)
        hdr.sectionResized.connect(self._save_header)
        hdr.sortIndicatorChanged.connect(self._save_header)
        lay.addWidget(self.view, 1)

        self.summary = QLabel()
        self.summary.setObjectName("muted")
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.summary)

        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: (self.search.setFocus(),
                                                                   self.search.selectAll()))
        QShortcut(QKeySequence(Qt.Key_Delete), self.view, activated=self.delete)
        QShortcut(QKeySequence(Qt.Key_Return), self.view, activated=self.edit)
        QShortcut(QKeySequence("Ctrl+C"), self.view, activated=self.copy_rows)

        self._header_restored = False
        self.refresh()

    # ------------------------------------------------------------------ data
    def load_rows(self):
        if self.kind in ("working", "faulty"):
            return self.db.list_devices(self.kind)
        if self.kind == "parts":
            return self.db.list_parts()
        return self.db.list_sales()

    def refresh(self):
        selected = {r["id"] for r in self.selected()}
        self.model.aged_days = int(num(self.db.setting("aged_days"), 60))
        self.model.set_rows(self.load_rows())
        self._fill_combos()
        self.apply_filters()
        if not self._header_restored:
            self._restore_header()
        if selected:
            sel = self.view.selectionModel()
            for r in range(self.proxy.rowCount()):
                src = self.proxy.mapToSource(self.proxy.index(r, 0)).row()
                if self.model.rows[src]["id"] in selected:
                    sel.select(self.proxy.index(r, 0),
                               sel.SelectionFlag.Select | sel.SelectionFlag.Rows)

    def _fill_combos(self):
        for key, cb in self.combos.items():
            current = cb.currentText()
            values = set()
            for r in self.model.rows:
                if key == "faults":
                    values.update(split_faults(r.get("faults")))
                elif r.get(key) not in (None, ""):
                    values.add(str(r.get(key)))
            cb.blockSignals(True)
            cb.clear()
            cb.addItem(cb.property("all_label"))
            cb.addItems(sorted(values, key=str.lower))
            idx = cb.findText(current)
            cb.setCurrentIndex(max(0, idx))
            cb.blockSignals(False)

    def apply_filters(self):
        eq = {k: (cb.currentText() if cb.currentIndex() > 0 else "")
              for k, cb in self.combos.items()}
        extra = None
        if self.kind == "sales":
            start, end = period_range(self.period.currentText())
            if start:
                s, e = start.isoformat(), end.isoformat()
                extra = lambda r: s <= str(r.get("sale_date") or "")[:10] <= e
        elif self.low_only is not None and self.low_only.isChecked():
            extra = lambda r: r.get("low_stock")
        self.proxy.set_filters(self.search.text(), eq, extra)
        self._update_summary()

    def clear_filters(self):
        self.search.clear()
        for cb in self.combos.values():
            cb.setCurrentIndex(0)
        if self.kind == "sales":
            self.period.setCurrentIndex(0)
        if self.low_only:
            self.low_only.setChecked(False)

    def visible_rows(self):
        return [self.model.rows[self.proxy.mapToSource(self.proxy.index(r, 0)).row()]
                for r in range(self.proxy.rowCount())]

    def _update_summary(self):
        rows = self.visible_rows()
        total = len(self.model.rows)
        shown = f"{len(rows)} of {total} shown" if len(rows) != total else f"{total} lines"
        written_off = [r for r in rows if r.get("written_off")]
        rows = [r for r in rows if not r.get("written_off")]
        if self.kind in ("working", "faulty"):
            units = sum(int(num(r.get("quantity"), 1)) for r in rows)
            cost = sum(r["total_cost"] * int(num(r.get("quantity"), 1)) for r in rows)
            listv = sum(num(r.get("list_price")) * int(num(r.get("quantity"), 1)) for r in rows)
            text = (f"{shown}   ·   {units} units   ·   Cost {money(cost)}   ·   "
                    f"List value {money(listv)}")
            if listv:
                text += f"   ·   Potential profit {money(listv - cost)}"
        elif self.kind == "parts":
            units = sum(int(num(r.get("quantity"))) for r in rows)
            value = sum(r["stock_value"] for r in rows)
            low = sum(1 for r in rows if r.get("low_stock"))
            text = f"{shown}   ·   {units} units   ·   Stock value {money(value)}"
            if low:
                text += f"   ·   {low} low stock"
        else:  # sales
            valid = [r for r in rows if not r.get("excluded")]
            revenue = sum(num(r.get("sale_price")) + num(r.get("postage_charged")) for r in valid)
            fees = sum(num(r.get("fees")) for r in valid)
            profit = sum(num(r.get("profit")) for r in valid)
            margin = f"{profit / revenue * 100:.1f}%" if revenue else "-"
            text = (f"{shown}   ·   {len(valid)} sales   ·   Revenue {money(revenue)}   ·   "
                    f"Fees {money(fees)}   ·   Profit {money(profit)}   ·   Margin {margin}")
        if self.kind != "sales" and written_off:
            if self.kind == "parts":
                lost = sum(r["stock_value"] for r in written_off)
            else:
                lost = sum(r["total_cost"] * int(num(r.get("quantity"), 1)) for r in written_off)
            text += f"   ·   {len(written_off)} written off ({money(lost)}, not counted)"
        self.summary.setText(text)

    # ------------------------------------------------------------------ header state
    def _settings(self):
        return QSettings(APP_ID, APP_ID)

    def _header_key(self):
        return f"header/{self.kind}/{len(self.cfg['columns'])}"

    def _restore_header(self):
        self._header_restored = True
        hdr = self.view.horizontalHeader()
        state = self._settings().value(self._header_key())
        if state is not None and hdr.restoreState(state):
            return
        for i, (key, _, _) in enumerate(self.cfg["columns"]):
            hdr.setSectionHidden(i, key in self.cfg["hidden"])
        self.view.resizeColumnsToContents()
        for i, (key, _, kind) in enumerate(self.cfg["columns"]):
            width = self.view.columnWidth(i)
            limit = 260 if key in ("faults", "title", "compatible") else 180
            self.view.setColumnWidth(i, max(70, min(width + 16, limit)))
        if self.kind == "sales":
            self.view.sortByColumn(self._col("sale_date"), Qt.DescendingOrder)
        else:
            self.view.sortByColumn(0, Qt.DescendingOrder)

    def _save_header(self, *args):
        if self._header_restored:
            self._settings().setValue(self._header_key(),
                                      self.view.horizontalHeader().saveState())

    def _col(self, key):
        return next(i for i, c in enumerate(self.cfg["columns"]) if c[0] == key)

    def _header_menu(self, pos):
        hdr = self.view.horizontalHeader()
        menu = QMenu(self)
        menu.addSection("Show columns")
        for i, (key, label, _) in enumerate(self.cfg["columns"]):
            act = QAction(label, menu, checkable=True)
            act.setChecked(not hdr.isSectionHidden(i))
            act.toggled.connect(lambda on, i=i: (hdr.setSectionHidden(i, not on),
                                                 self._save_header()))
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction("Fit columns to contents", lambda: (self.view.resizeColumnsToContents(),
                                                           self._save_header()))
        menu.addAction("Reset to default layout", self._reset_header)
        menu.exec(hdr.mapToGlobal(pos))

    def _reset_header(self):
        self._settings().remove(self._header_key())
        hdr = self.view.horizontalHeader()
        for i in range(hdr.count()):
            hdr.moveSection(hdr.visualIndex(i), i)
        self._header_restored = False
        self._restore_header()

    # ------------------------------------------------------------------ selection helpers
    def selected(self):
        rows = self.view.selectionModel().selectedRows() if self.view.selectionModel() else []
        return [self.model.rows[self.proxy.mapToSource(i).row()] for i in rows]

    def current(self):
        sel = self.selected()
        if not sel:
            idx = self.view.currentIndex()
            if idx.isValid():
                return self.model.rows[self.proxy.mapToSource(idx).row()]
            self.status_message.emit("Select a row first.")
            return None
        return sel[0]

    def select_id(self, row_id):
        self.clear_filters()
        for r in range(self.proxy.rowCount()):
            src = self.proxy.mapToSource(self.proxy.index(r, 0)).row()
            if self.model.rows[src]["id"] == row_id:
                self.view.selectRow(r)
                self.view.scrollTo(self.proxy.index(r, 0), QAbstractItemView.PositionAtCenter)
                return True
        return False

    def title_of(self, r):
        if self.kind == "sales":
            return r.get("title") or ""
        return device_title(r) if self.table_name == "devices" else part_title(r)

    def _done(self, msg=None):
        self.refresh()
        self.changed.emit()
        if msg:
            self.status_message.emit(msg)

    # ------------------------------------------------------------------ actions
    def add(self):
        if self.kind == "sales":
            QMessageBox.information(self, "Record a Sale",
                                    "To record a sale, select an item on the Working Phones, "
                                    "Faulty or Parts tab and choose 'Mark as Sold' (Ctrl+S).")
            return
        dlg = ItemDialog(self.db, self.table_name, category=self.kind, parent=self)
        dlg.exec()
        if getattr(dlg, "last_added", None):
            self._done("Item added.")
            self.select_id(dlg.last_added)

    def edit(self):
        r = self.current()
        if not r:
            return
        if self.kind == "sales":
            dlg = SaleDialog(self.db, sale=self.db.get("sales", r["id"]), parent=self)
        else:
            dlg = ItemDialog(self.db, self.table_name, record=self.db.get(self.table_name, r["id"]),
                             parent=self)
        dlg.exec()
        self._done()

    def duplicate(self):
        r = self.current()
        if not r or self.kind == "sales":
            return
        rec = self.db.get(self.table_name, r["id"])
        for k in ("id", "sku", "imei", "serial", "created_at", "updated_at", "sold_at",
                  "ebay_item_id"):
            rec.pop(k, None)
        new_id = self.db.insert(self.table_name, rec)
        self.db.log("Duplicated", self.entity, new_id, f"from {r.get('sku')}")
        self._done(f"Duplicated {r.get('sku')}.")
        self.select_id(new_id)

    def delete(self):
        rows = self.selected()
        if not rows:
            return
        what = "\n".join(f"  {r.get('sku')}  {self.title_of(r)}" for r in rows[:10])
        if len(rows) > 10:
            what += f"\n  ...and {len(rows) - 10} more"
        extra = ""
        if self.kind == "sales":
            extra = ("\n\nNote: deleting a sale does NOT put the item back in stock. "
                     "Use 'Return / Refund' for that.")
        if QMessageBox.warning(self, "Delete", f"Permanently delete {len(rows)} item(s)?\n\n"
                               f"{what}{extra}", QMessageBox.Yes | QMessageBox.No,
                               QMessageBox.No) != QMessageBox.Yes:
            return
        for r in rows:
            self.db.delete(self.table_name, r["id"])
            self.db.log("Deleted", self.entity, r["id"], f"{r.get('sku')} {self.title_of(r)}")
        self._done(f"Deleted {len(rows)} item(s).")

    def notes(self):
        r = self.current()
        if r:
            NotesDialog(self.db, self.entity, r["id"],
                        f"{r.get('sku')}  {self.title_of(r)}", self).exec()
            self.refresh()

    def sell(self):
        if self.kind == "sales":
            return self.edit()
        r = self.current()
        if not r:
            return
        if self.kind == "parts" and int(num(r.get("quantity"))) <= 0:
            QMessageBox.information(self, "Out of Stock", "This part has no stock left.")
            return
        rec = self.db.get(self.table_name, r["id"])
        dlg = SaleDialog(self.db, kind=self.entity, item=rec, parent=self)
        if dlg.exec():
            self._done(f"Sold {r.get('sku')}. See the Sold tab.")

    def return_sale(self):
        r = self.current()
        if not r or self.kind != "sales":
            return
        dlg = ReturnDialog(r, self)
        if dlg.exec():
            self.db.return_sale(r["id"], dlg.status.currentText(), dlg.restock.isChecked())
            self._done(f"Sale marked {dlg.status.currentText()}.")

    def use_part(self):
        r = self.current()
        if not r or self.table_name != "devices":
            return
        dlg = UsePartDialog(self.db, self.db.get("devices", r["id"]), self)
        if dlg.exec():
            self._done(dlg.result_text)

    def move(self):
        rows = self.selected()
        if not rows or self.table_name != "devices":
            return
        target = "faulty" if self.kind == "working" else "working"
        for r in rows:
            self.db.move_device(r["id"], target)
        label = "Faulty / Repairs" if target == "faulty" else "Working Phones"
        self._done(f"Moved {len(rows)} device(s) to {label}.")

    def set_status(self, status):
        rows = self.selected()
        for r in rows:
            self.db.update(self.table_name, r["id"], {"status": status})
            self.db.log("Status", self.entity, r["id"], f"{r.get('sku')} -> {status}")
        self._done(f"{len(rows)} item(s) set to {status}.")

    def test_device(self):
        r = self.current()
        if not r or self.table_name != "devices":
            return
        TestDialog(self.db, r["id"], self).exec()
        self._done()

    def test_report(self):
        r = self.current()
        if not r or self.table_name != "devices":
            return
        path = export_report(self, self.db, r["id"])
        if path:
            self.status_message.emit(f"Test report saved to {path}")

    def write_off(self):
        rows = [r for r in self.selected() if r.get("status") != WRITTEN_OFF]
        if not rows or self.kind == "sales":
            return
        names = ", ".join(r.get("sku") or "" for r in rows[:6]) + (" ..." if len(rows) > 6 else "")
        reason, ok = QInputDialog.getText(
            self, "Write Off", f"Write off {len(rows)} item(s): {names}\n\n"
            "They stay on record but no longer count as stock.\nReason (optional):")
        if not ok:
            return
        for r in rows:
            self.db.write_off(self.table_name, r["id"], reason.strip())
        self._done(f"Wrote off {len(rows)} item(s).")

    def copy_title(self):
        r = self.current()
        if r:
            QGuiApplication.clipboard().setText(self.title_of(r)[:80])
            self.status_message.emit("Listing title copied.")

    def copy_value(self, key):
        r = self.current()
        if r and r.get(key):
            QGuiApplication.clipboard().setText(str(r[key]))
            self.status_message.emit(f"Copied {r[key]}")

    def copy_rows(self):
        """Copy selected rows (visible columns) as tab separated text for Excel / Sheets."""
        rows = self.selected()
        if not rows:
            return
        hdr = self.view.horizontalHeader()
        cols = [hdr.logicalIndex(v) for v in range(hdr.count())]
        cols = [c for c in cols if not hdr.isSectionHidden(c)]
        lines = ["\t".join(self.cfg["columns"][c][1] for c in cols)]
        for r in rows:
            src = self.model.rows.index(r)
            lines.append("\t".join(str(self.model.data(self.model.index(src, c)) or "")
                                   for c in cols))
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.status_message.emit(f"Copied {len(rows)} row(s).")

    # ------------------------------------------------------------------ csv
    def export_csv(self):
        default = f"{self.cfg['title'].replace(' / ', '-').replace(' ', '-')}-" \
                  f"{dt.date.today().isoformat()}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", default, "CSV files (*.csv)")
        if not path:
            return
        rows = self.visible_rows()
        cols = [c for c in self.cfg["columns"] if c[2] != "notes"]
        fh = io.StringIO(newline="")
        w = csv.writer(fh)
        w.writerow([c[1] for c in cols] + ["Notes"])
        for r in rows:
            notes = " | ".join(n["body"] for n in reversed(self.db.notes(self.entity, r["id"])))
            w.writerow([r.get(c[0]) if r.get(c[0]) is not None else "" for c in cols] + [notes])
        try:
            fileio.write_bytes(path, fh.getvalue().encode("utf-8-sig"))
        except OSError as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))
            return
        self.status_message.emit(f"Exported {len(rows)} rows to {fileio.display_name(path)}")

    def import_csv(self):
        if self.kind == "sales":
            QMessageBox.information(self, "Import", "Import is available for Working Phones, "
                                                    "Faulty and Parts.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import", "",
                                              "CSV or backup (*.csv *.zip);;CSV files (*.csv);;"
                                              "Zaf Inventory backup (*.zip)")
        if not path:
            return
        if fileio.display_name(path).lower().endswith(".zip"):
            if restore_with_prompt(self, self.db, path):
                self._done("Backup restored.")
            return
        fields = DEVICE_FIELDS if self.table_name == "devices" else PART_FIELDS
        lookup = {}
        for f in fields:
            lookup[f.key.lower()] = f
            lookup[f.label.lower()] = f
        added, skipped = 0, 0
        try:
            with io.StringIO(fileio.read_text(path), newline="") as fh:
                reader = csv.DictReader(fh)
                mapping = {h: lookup.get((h or "").strip().lower()) for h in reader.fieldnames or []}
                matched = [h for h, f in mapping.items() if f]
                if not matched:
                    raise ValueError("No column headings matched. Use headings like Brand, Model, "
                                     "IMEI, Qty (export a CSV first to see the format).")
                for line in reader:
                    data = {}
                    for h, f in mapping.items():
                        if not f or f.kind == "readonly":
                            continue
                        v = (line.get(h) or "").strip()
                        if f.kind in ("money",):
                            v = num(v) if v else None
                        elif f.kind in ("int", "percent"):
                            v = int(num(v)) if v else None
                        elif f.kind == "date" and v:
                            v = _parse_date(v)
                        data[f.key] = v
                    if not any(data.values()):
                        skipped += 1
                        continue
                    if self.table_name == "devices":
                        data["category"] = self.kind
                        data.setdefault("quantity", 1)
                        data["quantity"] = data["quantity"] or 1
                        data["status"] = data.get("status") or (
                            "In Stock" if self.kind == "working" else "Awaiting Repair")
                    else:
                        data["status"] = data.get("status") or "In Stock"
                    notes = (line.get("Notes") or line.get("notes") or "").strip()
                    new_id = self.db.insert(self.table_name, data, commit=False)
                    if notes:
                        self.db.add_note(self.entity, new_id, notes, commit=False)
                    added += 1
            self.db.conn.commit()
        except Exception as exc:
            self.db.conn.rollback()
            QMessageBox.critical(self, "Import Failed", str(exc))
            return
        self.db.log("Imported", self.entity, None, f"{added} rows from {path}")
        self._done()
        QMessageBox.information(self, "Import Complete",
                                f"Imported {added} item(s)." +
                                (f" Skipped {skipped} empty row(s)." if skipped else ""))

    # ------------------------------------------------------------------ context menu
    def _context_menu(self, pos):
        r = self.current()
        menu = QMenu(self)
        if self.kind == "sales":
            menu.addAction("Edit Sale...", self.edit)
            menu.addAction("Notes...", self.notes)
            menu.addAction("Return / Refund / Cancel...", self.return_sale)
            menu.addSeparator()
            menu.addAction("Copy Order Number", lambda: self.copy_value("order_number"))
            menu.addAction("Copy IMEI", lambda: self.copy_value("imei"))
            menu.addAction("Copy Selected Rows", self.copy_rows)
            menu.addSeparator()
            menu.addAction("Delete Sale", self.delete)
        elif r:
            menu.addAction("Edit...", self.edit)
            menu.addAction("Notes && Photos...", self.notes)
            if self.table_name == "devices":
                menu.addAction("Test Device...", self.test_device)
                menu.addAction("Test Report (PDF)...", self.test_report)
            menu.addAction("Mark as Sold...", self.sell)
            if self.table_name == "devices":
                menu.addAction("Use Part From Stock...", self.use_part)
                menu.addAction("Move to Faulty / Repairs" if self.kind == "working"
                               else "Move to Working Phones (repaired)", self.move)
            menu.addAction("Write Off...", self.write_off)
            status_menu = menu.addMenu("Set Status")
            for s in (DEVICE_STATUSES if self.table_name == "devices" else PART_STATUSES):
                status_menu.addAction(s, lambda s=s: self.set_status(s))
            menu.addSeparator()
            menu.addAction("Copy Listing Title", self.copy_title)
            menu.addAction("Copy IMEI", lambda: self.copy_value("imei"))
            menu.addAction("Copy Serial No.", lambda: self.copy_value("serial"))
            menu.addAction("Copy SKU", lambda: self.copy_value("sku"))
            menu.addAction("Copy Selected Rows", self.copy_rows)
            menu.addSeparator()
            menu.addAction("Duplicate", self.duplicate)
            menu.addAction("Delete", self.delete)
        else:
            menu.addAction("Add New...", self.add)
        menu.exec(self.view.viewport().mapToGlobal(pos))


def _parse_date(text):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return text


# ============================================================================ activity
class ActivityTab(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.search = QLineEdit(placeholderText="Filter activity...")
        self.search.setClearButtonEnabled(True)
        lay.addWidget(self.search)
        cols = [("ts", "When", "ts"), ("action", "Action", "text"), ("entity", "Type", "text"),
                ("details", "Details", "text")]
        self.model = _ActivityModel(cols, self)
        self.proxy = FilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.search.textChanged.connect(lambda t: self.proxy.set_filters(t, {}))
        view = QTableView()
        view.setModel(self.proxy)
        view.setSortingEnabled(True)
        view.setAlternatingRowColors(True)
        view.setSelectionBehavior(QAbstractItemView.SelectRows)
        view.verticalHeader().hide()
        view.horizontalHeader().setStretchLastSection(True)
        view.sortByColumn(0, Qt.DescendingOrder)
        lay.addWidget(view, 1)
        self.view = view

    def refresh(self):
        self.model.set_rows(self.db.activity())
        self.view.resizeColumnToContents(0)
        self.view.resizeColumnToContents(1)


class _ActivityModel(RowsModel):
    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and self.columns[index.column()][2] == "ts":
            return fmt_ts(self.rows[index.row()].get("ts"))
        if role == Qt.DisplayRole and self.columns[index.column()][0] == "entity":
            return str(self.rows[index.row()].get("entity") or "").title()
        return super().data(index, role)
