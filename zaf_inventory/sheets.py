"""Spreadsheet-style ledgers with a running total column."""
import csv
import datetime as dt
import io

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QDoubleSpinBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import fileio, theme
from .db import num
from .widgets import fmt_date, money

# (db column, header, kind)
COLUMNS = [("row_date", "Date", "date"), ("description", "Description", "text"),
           ("category", "Category", "text"), ("reference", "Reference", "text"),
           ("money_in", "Money In", "money"), ("money_out", "Money Out", "money"),
           ("balance", "Running Total", "balance"), ("notes", "Notes", "text")]
BALANCE_COL = 6


def parse_date(text):
    text = text.strip()
    if not text:
        return ""
    if text.lower() in ("today", "t"):
        return dt.date.today().isoformat()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m"):
        try:
            d = dt.datetime.strptime(text, fmt).date()
            if fmt == "%d/%m":
                d = d.replace(year=dt.date.today().year)
            return d.isoformat()
        except ValueError:
            continue
    raise ValueError(f"'{text}' is not a date - use dd/mm/yyyy")


class SheetsTab(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.sheet_id = None
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        split = QSplitter()
        lay.addWidget(split, 1)

        # ---- sheet list
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Sheets")
        title.setObjectName("h2")
        ll.addWidget(title)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._sheet_changed)
        self.list.itemDoubleClicked.connect(lambda *_: self.rename_sheet())
        ll.addWidget(self.list, 1)
        for text, fn in (("New Sheet", self.new_sheet), ("Rename", self.rename_sheet),
                         ("Opening Balance", self.set_opening), ("Delete Sheet", self.delete_sheet)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            if text == "New Sheet":
                b.setProperty("primary", True)
            ll.addWidget(b)
        split.addWidget(left)

        # ---- grid
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Filter rows (running totals still count every row)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search, 1)
        for text, fn in (("Add Row", self.add_row), ("Delete Rows", self.delete_rows),
                         ("Export CSV", self.export_csv)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            if text == "Add Row":
                b.setProperty("primary", True)
            bar.addWidget(b)
        rl.addLayout(bar)
        self.grid = QTableWidget(0, len(COLUMNS))
        self.grid.setHorizontalHeaderLabels([c[1] for c in COLUMNS])
        self.grid.setAlternatingRowColors(True)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                  | QAbstractItemView.AnyKeyPressed)
        self.grid.verticalHeader().setDefaultSectionSize(26)
        hdr = self.grid.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(7, QHeaderView.Stretch)
        for c, w in ((0, 100), (2, 130), (3, 110), (4, 110), (5, 110), (6, 130)):
            self.grid.setColumnWidth(c, w)
        self.grid.itemChanged.connect(self._cell_changed)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._menu)
        rl.addWidget(self.grid, 1)
        self.footer = QLabel()
        self.footer.setObjectName("h2")
        rl.addWidget(self.footer)
        hint = QLabel("Type straight into cells like a spreadsheet. Dates as dd/mm/yyyy (or 'today'). "
                      "Ctrl+C / Ctrl+V copy & paste blocks to/from Excel or Google Sheets. "
                      "Insert adds a row, Delete clears cells.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        rl.addWidget(hint)
        split.addWidget(right)
        split.setSizes([220, 900])

        QShortcut(QKeySequence("Ctrl+C"), self.grid, activated=self.copy_cells)
        QShortcut(QKeySequence("Ctrl+V"), self.grid, activated=self.paste_cells)
        QShortcut(QKeySequence(Qt.Key_Insert), self.grid, activated=self.add_row)
        QShortcut(QKeySequence(Qt.Key_Delete), self.grid, activated=self.clear_cells)
        self.refresh()

    # ------------------------------------------------------------------ sheets
    def refresh(self):
        sheets = self.db.sheets()
        if not sheets:
            self.db.add_sheet("Running Total")
            sheets = self.db.sheets()
        keep = self.sheet_id
        self.list.blockSignals(True)
        self.list.clear()
        for s in sheets:
            item = QListWidgetItem(s["name"])
            item.setData(Qt.UserRole, s["id"])
            self.list.addItem(item)
        self.list.blockSignals(False)
        ids = [s["id"] for s in sheets]
        self.list.setCurrentRow(ids.index(keep) if keep in ids else 0)

    def _sheet_changed(self, item, _prev=None):
        self.sheet_id = item.data(Qt.UserRole) if item else None
        self.load_rows()

    def new_sheet(self):
        name, ok = QInputDialog.getText(self, "New Sheet", "Sheet name (e.g. Cash, Expenses):")
        if ok and name.strip():
            self.sheet_id = self.db.add_sheet(name.strip())
            self.refresh()

    def rename_sheet(self):
        item = self.list.currentItem()
        if not item:
            return
        name, ok = QInputDialog.getText(self, "Rename Sheet", "Name:", text=item.text())
        if ok and name.strip():
            self.db.update_sheet(self.sheet_id, name=name.strip())
            self.refresh()

    def set_opening(self):
        if not self.sheet_id:
            return
        sheet = next(s for s in self.db.sheets() if s["id"] == self.sheet_id)
        value, ok = QInputDialog.getDouble(self, "Opening Balance",
                                           "Balance before the first row:",
                                           num(sheet["opening_balance"]), -1e9, 1e9, 2)
        if ok:
            self.db.update_sheet(self.sheet_id, opening_balance=value)
            self.load_rows()

    def delete_sheet(self):
        item = self.list.currentItem()
        if item and QMessageBox.warning(
                self, "Delete Sheet", f"Delete the sheet '{item.text()}' and all its rows?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.db.delete_sheet(self.sheet_id)
            self.sheet_id = None
            self.refresh()

    # ------------------------------------------------------------------ rows
    def load_rows(self, focus_id=None, focus_col=1):
        self._loading = True
        self.grid.setRowCount(0)
        rows = self.db.sheet_rows(self.sheet_id) if self.sheet_id else []
        self.rows = rows
        c = theme.current
        self.grid.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for col, (key, _, kind) in enumerate(COLUMNS):
                v = row.get(key)
                if kind == "date":
                    text = fmt_date(v) if v else ""
                elif kind in ("money", "balance"):
                    text = money(v) if v not in (None, "") and (kind == "balance" or num(v)) else ""
                else:
                    text = v or ""
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, row["id"])
                if kind in ("money", "balance"):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if kind == "balance":
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    item.setForeground(QColor(c["good"] if num(v) >= 0 else c["bad"]))
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                elif key == "money_in" and num(v):
                    item.setForeground(QColor(c["good"]))
                elif key == "money_out" and num(v):
                    item.setForeground(QColor(c["bad"]))
                self.grid.setItem(r, col, item)
        self._loading = False
        self._apply_filter()
        self._update_footer()
        if focus_id is not None:
            for r, row in enumerate(rows):
                if row["id"] == focus_id:
                    self.grid.setCurrentCell(r, focus_col)
                    self.grid.scrollToItem(self.grid.item(r, focus_col))
                    break

    def _update_footer(self):
        rows = self.rows
        tin = sum(num(r.get("money_in")) for r in rows)
        tout = sum(num(r.get("money_out")) for r in rows)
        bal = rows[-1]["balance"] if rows else 0
        if not rows and self.sheet_id:
            sheet = next((s for s in self.db.sheets() if s["id"] == self.sheet_id), None)
            bal = num(sheet["opening_balance"]) if sheet else 0
        colour = theme.current["good"] if bal >= 0 else theme.current["bad"]
        sep = "&nbsp;&nbsp;&nbsp;·&nbsp;&nbsp;&nbsp;"
        self.footer.setText(f"In {money(tin)}{sep}Out {money(tout)}{sep}Net {money(tin - tout)}{sep}"
                            f"Running Total <span style='color:{colour}'>{money(bal)}</span>")

    def _apply_filter(self):
        words = self.search.text().lower().split()
        for r in range(self.grid.rowCount()):
            text = " ".join((self.grid.item(r, c).text() if self.grid.item(r, c) else "")
                            for c in range(self.grid.columnCount())).lower()
            self.grid.setRowHidden(r, any(w not in text for w in words))

    def _store(self, row_id, col, text):
        key, _, kind = COLUMNS[col]
        if kind == "balance":
            return
        if kind == "date":
            value = parse_date(text)
        elif kind == "money":
            value = round(num(text), 2) if text.strip() else None
        else:
            value = text.strip()
        self.db.update_sheet_cell(row_id, key, value)

    def _cell_changed(self, item):
        if self._loading:
            return
        row_id = item.data(Qt.UserRole)
        try:
            self._store(row_id, item.column(), item.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Sheet", str(exc))
        self.load_rows(focus_id=row_id, focus_col=item.column())

    def add_row(self):
        if not self.sheet_id:
            return
        row_id = self.db.add_sheet_row(self.sheet_id)
        self.load_rows(focus_id=row_id, focus_col=1)
        self.grid.editItem(self.grid.currentItem())

    def _selected_row_ids(self):
        rows = sorted({i.row() for i in self.grid.selectedIndexes()})
        return [self.rows[r]["id"] for r in rows if r < len(self.rows)]

    def delete_rows(self):
        ids = self._selected_row_ids()
        if ids and QMessageBox.question(self, "Delete Rows", f"Delete {len(ids)} row(s)?") \
                == QMessageBox.Yes:
            self.db.delete_sheet_rows(ids)
            self.load_rows()

    def clear_cells(self):
        cells = [i for i in self.grid.selectedIndexes() if i.column() != BALANCE_COL]
        if not cells:
            return
        for idx in cells:
            self._store(self.rows[idx.row()]["id"], idx.column(), "")
        self.load_rows()

    # ------------------------------------------------------------------ clipboard
    def copy_cells(self):
        idx = self.grid.selectedIndexes()
        if not idx:
            return
        rows = sorted({i.row() for i in idx})
        cols = sorted({i.column() for i in idx})
        lines = []
        for r in rows:
            lines.append("\t".join(self.grid.item(r, c).text() if self.grid.item(r, c) else ""
                                   for c in cols))
        QGuiApplication.clipboard().setText("\n".join(lines))

    def paste_cells(self):
        if not self.sheet_id:
            return
        text = QGuiApplication.clipboard().text()
        if not text.strip():
            return
        lines = [l.split("\t") for l in text.rstrip("\n").split("\n")]
        start_row = max(0, self.grid.currentRow())
        start_col = max(0, self.grid.currentColumn())
        row_ids = [r["id"] for r in self.rows]
        errors = []
        for i, values in enumerate(lines):
            r = start_row + i
            if r < len(row_ids):
                row_id = row_ids[r]
            else:
                row_id = self.db.add_sheet_row(self.sheet_id, {"row_date": ""})
                row_ids.append(row_id)
            col = start_col
            for v in values:
                if col >= len(COLUMNS):
                    break
                if col != BALANCE_COL:
                    try:
                        self._store(row_id, col, v)
                    except ValueError as exc:
                        errors.append(str(exc))
                col += 1
        self.load_rows()
        if errors:
            QMessageBox.warning(self, "Paste", "Some cells were skipped:\n" + "\n".join(errors[:8]))

    def _menu(self, pos):
        menu = QMenu(self)
        menu.addAction("Add Row", self.add_row)
        menu.addAction("Copy", self.copy_cells)
        menu.addAction("Paste", self.paste_cells)
        menu.addAction("Clear Cells", self.clear_cells)
        menu.addSeparator()
        menu.addAction("Delete Rows", self.delete_rows)
        menu.exec(self.grid.viewport().mapToGlobal(pos))

    def export_csv(self):
        if not self.sheet_id:
            return
        name = self.list.currentItem().text()
        path, _ = QFileDialog.getSaveFileName(self, "Export Sheet",
                                              f"{name}-{dt.date.today().isoformat()}.csv",
                                              "CSV files (*.csv)")
        if not path:
            return
        fh = io.StringIO(newline="")
        w = csv.writer(fh)
        w.writerow([c[1] for c in COLUMNS])
        for row in self.rows:
            w.writerow([row.get(k) if row.get(k) is not None else "" for k, _, _ in COLUMNS])
        try:
            fileio.write_bytes(path, fh.getvalue().encode("utf-8-sig"))
        except OSError as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))
