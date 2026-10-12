"""Reusable form widgets: field-driven form, fault picker, notes and photos panels."""
import datetime as dt
from pathlib import Path

from PySide6.QtCore import QDate, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter, QDateEdit,
                               QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from .db import num, split_faults
from .paths import photos_dir

UI = {"currency": "£"}
BLANK_DATE = QDate(2000, 1, 1)
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.heic)"


def money(value, blank=""):
    if value in (None, ""):
        return blank
    v = num(value)
    sign = "-" if v < 0 else ""
    return f"{sign}{UI['currency']}{abs(v):,.2f}"


def fmt_ts(ts):
    try:
        return dt.datetime.fromisoformat(ts).strftime("%d %b %Y %H:%M")
    except (TypeError, ValueError):
        return ts or ""


def fmt_date(d):
    try:
        return dt.date.fromisoformat(str(d)[:10]).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return d or ""


# ============================================================================ faults
class FaultPicker(QWidget):
    """Tick-list of known faults with a filter box and the ability to add new ones."""

    def __init__(self, faults, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.filter = QLineEdit(placeholderText="Filter faults...")
        self.filter.textChanged.connect(self._apply_filter)
        self.custom = QLineEdit(placeholderText="Add a new fault...")
        self.custom.returnPressed.connect(self._add_custom)
        add = QPushButton("Add")
        add.clicked.connect(self._add_custom)
        top.addWidget(self.filter, 1)
        top.addWidget(self.custom, 1)
        top.addWidget(add)
        lay.addLayout(top)
        self.list = QListWidget()
        self.list.setFlow(QListWidget.LeftToRight)
        self.list.setWrapping(True)
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setUniformItemSizes(True)
        self.list.setGridSize(QSize(235, 26))
        self.list.setMinimumHeight(150)
        lay.addWidget(self.list)
        for f in faults:
            self._add_item(f, False)

    def _add_item(self, text, checked):
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.text().lower() == text.lower():
                if checked:
                    item.setCheckState(Qt.Checked)
                return
        item = QListWidgetItem(text)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        item.setToolTip(text)
        self.list.addItem(item)

    def _add_custom(self):
        text = self.custom.text().strip()
        if text:
            self._add_item(text, True)
            self.custom.clear()

    def _apply_filter(self, text):
        text = text.lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    def value(self):
        return "; ".join(self.list.item(i).text() for i in range(self.list.count())
                         if self.list.item(i).checkState() == Qt.Checked)

    def set_value(self, text):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.Unchecked)
        for f in split_faults(text):
            self._add_item(f, True)


# ============================================================================ form
class FormPanel(QWidget):
    """Builds an edit form from a list of Field definitions."""

    def __init__(self, db, table, fields, parent=None, columns=2):
        super().__init__(parent)
        self.db, self.table, self.fields = db, table, fields
        self.widgets = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        outer.addLayout(grid)
        wide = []
        idx = 0
        for f in fields:
            w = self._make(f)
            self.widgets[f.key] = w
            if f.tip:
                w.setToolTip(f.tip)
            if f.kind in ("multiline", "faults"):
                wide.append(f)
                continue
            row, col = divmod(idx, columns)
            label = QLabel(f.label + (" *" if f.required else ""))
            grid.addWidget(label, row, col * 2, Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(w, row, col * 2 + 1)
            idx += 1
        for c in range(columns):
            grid.setColumnStretch(c * 2 + 1, 1)
        for f in wide:
            outer.addWidget(QLabel(f"<b>{f.label}</b>"))
            outer.addWidget(self.widgets[f.key], 1 if f.kind == "faults" else 0)

    def _suggestions(self, f):
        items = list(f.options)
        if f.suggest:
            items += self.db.get_list(f.suggest)
        items += [str(v) for v in self.db.distinct(self.table, f.key)]
        seen, out = set(), []
        for it in items:
            if it and it.lower() not in seen:
                seen.add(it.lower())
                out.append(it)
        return out

    def _make(self, f):
        if f.kind == "readonly":
            w = QLineEdit(readOnly=True, placeholderText="(automatic)")
        elif f.kind == "multiline":
            w = QPlainTextEdit()
            w.setFixedHeight(90)
        elif f.kind == "combo":
            w = QComboBox(editable=True)
            items = self._suggestions(f)
            w.addItems([""] + items)
            comp = QCompleter(items, w)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setFilterMode(Qt.MatchContains)
            w.setCompleter(comp)
            w.setInsertPolicy(QComboBox.NoInsert)
        elif f.kind == "choice":
            w = QComboBox()
            w.addItems([""] + list(f.options))
        elif f.kind == "money":
            w = QDoubleSpinBox()
            w.setRange(-1_000_000, 10_000_000)
            w.setDecimals(2)
            w.setPrefix(UI["currency"])
            w.setGroupSeparatorShown(True)
        elif f.kind == "int":
            w = QSpinBox()
            w.setRange(0, 1_000_000)
        elif f.kind == "percent":
            w = QSpinBox()
            w.setRange(0, 100)
            w.setSuffix(" %")
            w.setSpecialValueText("—")
        elif f.kind == "date":
            w = QDateEdit(calendarPopup=True)
            w.setDisplayFormat("dd/MM/yyyy")
            w.setMinimumDate(BLANK_DATE)
            w.setSpecialValueText(" ")
            w.setDate(BLANK_DATE)
        elif f.kind == "faults":
            w = FaultPicker(self.db.all_faults())
        else:
            w = QLineEdit()
        if isinstance(w, (QSpinBox, QDoubleSpinBox)):
            w.setAlignment(Qt.AlignRight)
        return w

    def value_of(self, key):
        f = next(x for x in self.fields if x.key == key)
        w = self.widgets[key]
        if f.kind in ("readonly", "text"):
            return w.text().strip()
        if f.kind == "multiline":
            return w.toPlainText().strip()
        if f.kind in ("combo", "choice"):
            return w.currentText().strip()
        if f.kind == "money":
            return round(w.value(), 2)
        if f.kind == "int":
            return w.value()
        if f.kind == "percent":
            return w.value() or None
        if f.kind == "date":
            return "" if w.date() == BLANK_DATE else w.date().toString("yyyy-MM-dd")
        if f.kind == "faults":
            return w.value()
        return None

    def values(self):
        return {f.key: self.value_of(f.key) for f in self.fields if f.kind != "readonly"}

    def set_values(self, data):
        for f in self.fields:
            if f.key not in data:
                continue
            v = data.get(f.key)
            w = self.widgets[f.key]
            if f.kind in ("readonly", "text"):
                w.setText("" if v is None else str(v))
            elif f.kind == "multiline":
                w.setPlainText(v or "")
            elif f.kind in ("combo", "choice"):
                text = "" if v is None else str(v)
                if w.findText(text) < 0:
                    w.addItem(text)
                w.setCurrentIndex(w.findText(text))
                if f.kind == "combo":
                    w.setEditText(text)
            elif f.kind == "money":
                w.setValue(num(v))
            elif f.kind in ("int", "percent"):
                w.setValue(int(num(v)))
            elif f.kind == "date":
                d = QDate.fromString(str(v or "")[:10], "yyyy-MM-dd")
                w.setDate(d if d.isValid() else BLANK_DATE)
            elif f.kind == "faults":
                w.set_value(v)

    def missing_required(self):
        return [f.label for f in self.fields if f.required and not self.value_of(f.key)]


# ============================================================================ notes
class NotesWidget(QWidget):
    """Timestamped note history. Works before the item is saved (notes are queued)."""

    def __init__(self, db, entity, entity_id=None, parent=None):
        super().__init__(parent)
        self.db, self.entity, self.entity_id = db, entity, entity_id
        self.pending = []
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setAlternatingRowColors(True)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda *_: self.edit_selected())
        lay.addWidget(self.list, 1)
        self.input = QPlainTextEdit(placeholderText="Type a note (e.g. tested, battery swapped, "
                                                    "buyer asked about...)   Ctrl+Enter to add")
        self.input.setFixedHeight(80)
        lay.addWidget(self.input)
        row = QHBoxLayout()
        add = QPushButton("Add Note")
        add.setProperty("primary", True)
        add.clicked.connect(self.add)
        edit = QPushButton("Edit")
        edit.clicked.connect(self.edit_selected)
        delete = QPushButton("Delete")
        delete.clicked.connect(self.delete_selected)
        row.addWidget(add)
        row.addStretch(1)
        row.addWidget(edit)
        row.addWidget(delete)
        lay.addLayout(row)
        QShortcut(QKeySequence("Ctrl+Return"), self.input, activated=self.add)
        self.reload()

    def reload(self):
        self.list.clear()
        notes = [{"id": None, "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                  "body": b + "   (unsaved)"} for b in reversed(self.pending)]
        if self.entity_id:
            notes += self.db.notes(self.entity, self.entity_id)
        for n in notes:
            item = QListWidgetItem(f"{fmt_ts(n['created_at'])}\n{n['body']}")
            item.setData(Qt.UserRole, n["id"])
            self.list.addItem(item)

    def add(self):
        body = self.input.toPlainText().strip()
        if not body:
            return
        if self.entity_id:
            self.db.add_note(self.entity, self.entity_id, body)
        else:
            self.pending.append(body)
        self.input.clear()
        self.reload()

    def edit_selected(self):
        item = self.list.currentItem()
        if not item or item.data(Qt.UserRole) is None:
            return
        body = item.text().split("\n", 1)[1] if "\n" in item.text() else item.text()
        text, ok = QInputDialog.getMultiLineText(self, "Edit Note", "Note:", body)
        if ok and text.strip():
            self.db.update_note(item.data(Qt.UserRole), text)
            self.reload()

    def delete_selected(self):
        items = [i for i in self.list.selectedItems() if i.data(Qt.UserRole) is not None]
        if not items:
            return
        if QMessageBox.question(self, "Delete Notes", f"Delete {len(items)} note(s)?") \
                == QMessageBox.Yes:
            for i in items:
                self.db.delete_note(i.data(Qt.UserRole))
            self.reload()

    def flush(self, entity_id):
        self.entity_id = entity_id
        for body in self.pending:
            self.db.add_note(self.entity, entity_id, body)
        self.pending = []


# ============================================================================ photos
class PhotosWidget(QWidget):
    """Thumbnail grid of item photos. Drag & drop image files onto it to add."""

    def __init__(self, db, entity, entity_id=None, parent=None):
        super().__init__(parent)
        self.db, self.entity, self.entity_id = db, entity, entity_id
        self.pending = []
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(150, 150))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setSpacing(8)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.itemDoubleClicked.connect(self._open)
        lay.addWidget(self.list, 1)
        hint = QLabel("Tip: drag photos straight onto this panel. Double-click to open full size.")
        hint.setObjectName("muted")
        lay.addWidget(hint)
        row = QHBoxLayout()
        add = QPushButton("Add Photos...")
        add.clicked.connect(self.browse)
        rem = QPushButton("Remove")
        rem.clicked.connect(self.remove_selected)
        row.addWidget(add)
        row.addStretch(1)
        row.addWidget(rem)
        lay.addLayout(row)
        self.reload()

    def reload(self):
        self.list.clear()
        entries = [(None, p) for p in self.pending]
        if self.entity_id:
            entries += [(ph["id"], str(photos_dir() / ph["filename"]))
                        for ph in self.db.photos(self.entity, self.entity_id)]
        for pid, path in entries:
            pix = QPixmap(path)
            icon = QIcon(pix.scaled(150, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation)) \
                if not pix.isNull() else QIcon()
            item = QListWidgetItem(icon, Path(path).name[:22])
            item.setData(Qt.UserRole, pid)
            item.setData(Qt.UserRole + 1, path)
            self.list.addItem(item)

    def add_files(self, paths):
        for p in paths:
            if self.entity_id:
                self.db.add_photo(self.entity, self.entity_id, p)
            else:
                self.pending.append(p)
        self.reload()

    def browse(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add Photos", "", IMAGE_FILTER)
        if files:
            self.add_files(files)

    def remove_selected(self):
        items = self.list.selectedItems()
        if not items or QMessageBox.question(self, "Remove Photos",
                                             f"Remove {len(items)} photo(s)?") != QMessageBox.Yes:
            return
        for it in items:
            if it.data(Qt.UserRole) is None:
                self.pending.remove(it.data(Qt.UserRole + 1))
            else:
                self.db.delete_photo(it.data(Qt.UserRole))
        self.reload()

    def _open(self, item):
        QDesktopServices.openUrl(QUrl.fromLocalFile(item.data(Qt.UserRole + 1)))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".heic"}
        files = [u.toLocalFile() for u in event.mimeData().urls()
                 if Path(u.toLocalFile()).suffix.lower() in exts]
        if files:
            self.add_files(files)

    def flush(self, entity_id):
        self.entity_id = entity_id
        for p in self.pending:
            self.db.add_photo(self.entity, entity_id, p)
        self.pending = []
