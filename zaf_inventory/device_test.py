"""Device testing screen: checklist, device details and PDF report."""
import datetime as dt
import tempfile
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QDialog, QFileDialog,
                               QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from . import fileio, report, theme
from .checklist import (APPLIES, CUSTOM_PREFIX, FAIL, NA, NOTE_FOR, PASS, RESULTS, is_custom,
                        not_applicable_keys, summarise, summary_text)
from .db import device_title, imei_valid
from .fields import DEVICE_FIELDS
from .widgets import FormPanel

SPEC_KEYS = ["brand", "model", "model_number", "colour", "storage", "ram", "imei", "serial",
             "network", "os_version", "battery_health", "battery_cycles", "grade",
             "cosmetic_notes"]
RESULT_COLOURS = {PASS: "#16a34a", FAIL: "#dc2626", NA: "#6b7280"}
FILTERS = ["All tests", "Not tested", PASS, FAIL, NA]


class ResultButtons(QWidget):
    """Pass / Fail / N/A toggle. Clicking the active button clears the result."""
    changed = Signal(str)

    def __init__(self, result="", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 1, 4, 1)
        lay.setSpacing(4)
        self.result = ""
        self.buttons = {}
        for name in RESULTS:
            b = QPushButton(name)
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)  # keep the keyboard on the table
            b.setFixedHeight(24)
            c = RESULT_COLOURS[name]
            b.setStyleSheet(
                "QPushButton { padding: 1px 8px; border-radius: 4px; min-width: 32px; }"
                f"QPushButton:checked {{ background: {c}; color: white; border-color: {c};"
                " font-weight: 600; }")
            b.clicked.connect(lambda checked, n=name: self._clicked(n, checked))
            lay.addWidget(b)
            self.buttons[name] = b
        self.set_result(result)

    def _clicked(self, name, checked):
        self.set_result(name if checked else "")
        self.changed.emit(self.result)

    def set_result(self, result):
        self.result = result
        for name, b in self.buttons.items():
            b.setChecked(name == result)


class CheckTable(QTableWidget):
    key_result = Signal(int, str)  # row, result

    KEYS = {Qt.Key_P: PASS, Qt.Key_F: FAIL, Qt.Key_N: NA, Qt.Key_Delete: "", Qt.Key_Backspace: ""}

    def keyPressEvent(self, event):
        blocked = Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier
        if (self.state() != QAbstractItemView.EditingState and self.currentRow() >= 0
                and not (event.modifiers() & blocked) and event.key() in self.KEYS):
            self.key_result.emit(self.currentRow(), self.KEYS[event.key()])
            return
        super().keyPressEvent(event)


def export_report(parent, db, device_id):
    """Ask where to save, write the PDF report and open it. Returns the path or None."""
    device = db.get("devices", device_id)
    folder = Path(db.setting("report_dir") or Path.home() / "Documents")
    if not folder.is_dir():
        folder = Path.home()
    path, _ = QFileDialog.getSaveFileName(parent, "Save Test Report",
                                          str(folder / report.default_filename(device)),
                                          "PDF files (*.pdf)")
    if not path:
        return None
    if not fileio.is_uri(path) and not path.lower().endswith(".pdf"):
        path += ".pdf"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = report.write_pdf(db, device_id, Path(tmp) / "report.pdf")
            fileio.write_bytes(path, pdf.read_bytes())
    except Exception as exc:  # e.g. file open in a PDF viewer, folder not writable
        QMessageBox.critical(parent, "Report Failed", f"Could not create the PDF:\n\n{exc}")
        return None
    if not fileio.is_uri(path):
        db.set_setting("report_dir", str(Path(path).parent))
    db.log("Report", "device", device_id,
           f"{device.get('sku')} test report {fileio.display_name(path)}")
    fileio.open_external(path)
    return path


class TestDialog(QDialog):
    def __init__(self, db, device_id, parent=None):
        super().__init__(parent)
        self.db, self.device_id = db, device_id
        self.device = db.get("devices", device_id)
        self.results = db.device_tests(device_id)
        self.meta = db.test_meta(device_id)
        self.dirty = False
        self._loading = False
        self.rowinfo = []
        self.row_of = {}
        self.setWindowTitle(f"Test Device - {self.device.get('sku')}")
        self.resize(1120, 840)

        lay = QVBoxLayout(self)
        imei = self.device.get("imei")
        head = QLabel(f"{self.device.get('sku')}  ·  {device_title(self.device)}"
                      + (f"   IMEI {imei}" if imei else ""))
        head.setObjectName("h2")
        head.setWordWrap(True)
        lay.addWidget(head)

        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self._build_checklist_tab()
        self._build_details_tab()

        # ---- footer
        foot = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        self.progress.setFixedWidth(180)
        self.stats = QLabel()
        foot.addWidget(self.progress)
        foot.addWidget(self.stats, 1)
        self.pdf_btn = QPushButton("Generate PDF Report...")
        self.pdf_btn.setProperty("primary", True)
        self.pdf_btn.clicked.connect(self.generate_report)
        done = QPushButton("Done")
        done.clicked.connect(self.accept)
        foot.addWidget(self.pdf_btn)
        foot.addWidget(done)
        lay.addLayout(foot)

        self._build_rows()
        self._refresh_stats()

    # ------------------------------------------------------------------ checklist tab
    def _build_checklist_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        bar = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Search tests...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.filter = QComboBox()
        self.filter.addItems(FILTERS)
        self.filter.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.filter)
        for text, fn, tip in (
                ("Auto N/A", self.auto_na, "Mark features this phone can't have as N/A "
                                           "(e.g. iCloud lock on Android, FRP on iPhone)"),
                ("Pass All Untested", lambda: self._bulk(PASS), "Mark every untested item as Pass"),
                ("N/A All Untested", lambda: self._bulk(NA), "Mark every untested item as N/A"),
                ("Add Custom Test...", self.add_custom, "Add your own test to this phone"),
                ("Clear All", self.clear_all, "Reset every result and note")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
        lay.addLayout(bar)

        self.table = CheckTable(0, 3)
        self.table.setHorizontalHeaderLabels(["Test", "Result", "Notes (double-click to edit)"])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setShowGrid(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 190)
        self.table.itemChanged.connect(self._on_note_changed)
        self.table.key_result.connect(self._on_key_result)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.table, 1)
        hint = QLabel("Click Pass / Fail / N/A, or select a row and press <b>P</b> / <b>F</b> / <b>N</b> "
                      "(<b>Delete</b> clears it). Failing a test jumps to its note. "
                      "Everything saves as you go.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.tabs.addTab(page, "Checklist")

    def _build_rows(self):
        current_key = None
        row = self.table.currentRow()
        if 0 <= row < len(self.rowinfo):
            current_key = self.rowinfo[row].get("key")
        self._loading = True
        self.table.setRowCount(0)
        self.rowinfo, self.row_of = [], {}
        alt = QColor(theme.current["alt"])
        for name, items in report.sections_for(self.results):
            r = self.table.rowCount()
            self.table.insertRow(r)
            head = QTableWidgetItem(name.upper())
            font = QFont(head.font())
            font.setBold(True)
            head.setFont(font)
            head.setBackground(alt)
            head.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(r, 0, head)
            self.table.setSpan(r, 0, 1, 3)
            self.rowinfo.append({"kind": "header", "section": name})
            for key, label in items:
                r = self.table.rowCount()
                self.table.insertRow(r)
                entry = self.results.get(key, {})
                li = QTableWidgetItem(label)
                li.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.table.setItem(r, 0, li)
                buttons = ResultButtons(entry.get("result", ""))
                buttons.changed.connect(lambda res, k=key: self._on_result(k, res))
                self.table.setCellWidget(r, 1, buttons)
                ni = QTableWidgetItem(entry.get("note", ""))
                ni.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                self.table.setItem(r, 2, ni)
                self.rowinfo.append({"kind": "test", "key": key, "section": name, "label": label})
                self.row_of[key] = r
        self._loading = False
        self._apply_filter()
        if current_key in self.row_of:
            self.table.setCurrentCell(self.row_of[current_key], 0)

    # ------------------------------------------------------------------ results
    def _entry(self, key):
        return self.results.setdefault(key, {"result": "", "note": ""})

    def _store(self, key, result=None, note=None):
        """Write one test to the cache, the database and the visible row."""
        entry = self._entry(key)
        if result is not None:
            entry["result"] = result
            widget = self.table.cellWidget(self.row_of[key], 1)
            if widget is not None and widget.result != result:
                widget.set_result(result)
        if note is not None:
            entry["note"] = note.strip()
            item = self.table.item(self.row_of[key], 2)
            if item is not None and item.text() != note:
                self._loading = True
                item.setText(note)
                self._loading = False
        self.db.set_test(self.device_id, key, result, None if note is None else note.strip())
        self.dirty = True

    def _on_result(self, key, result):
        self._store(key, result=result)
        if result == FAIL and not self._entry(key)["note"]:
            self._edit_note(self.row_of[key])
        self._refresh_stats()
        self._apply_filter()

    def _on_key_result(self, row, result):
        if not (0 <= row < len(self.rowinfo)) or self.rowinfo[row]["kind"] != "test":
            return
        key = self.rowinfo[row]["key"]
        self._store(key, result=result)
        self._refresh_stats()
        if result == FAIL:
            self._edit_note(row)
        elif result:
            self._advance(row)
        self._apply_filter()

    def _on_note_changed(self, item):
        if self._loading or item.column() != 2 or not (0 <= item.row() < len(self.rowinfo)):
            return
        info = self.rowinfo[item.row()]
        if info["kind"] == "test":
            self._entry(info["key"])["note"] = item.text().strip()
            self.db.set_test(self.device_id, info["key"], None, item.text().strip())
            self.dirty = True
            self._apply_filter()

    def _edit_note(self, row):
        def start():
            self.table.setCurrentCell(row, 2)
            item = self.table.item(row, 2)
            if item is not None:
                self.table.editItem(item)
        QTimer.singleShot(0, start)

    def _advance(self, row):
        for r in range(row + 1, self.table.rowCount()):
            if self.rowinfo[r]["kind"] == "test" and not self.table.isRowHidden(r):
                self.table.setCurrentCell(r, 0)
                return

    def _refresh_stats(self):
        c = summarise(self.results)
        self.progress.setMaximum(max(1, c["total"]))
        self.progress.setValue(c["done"])
        colour = theme.current["bad"] if c["fail"] else theme.current["good"]
        self.stats.setText(
            f"<b>{c['done']}</b> of {c['total']} tested  ·  "
            f"<span style='color:{theme.current['good']}'>Pass {c['pass']}</span>  ·  "
            f"<span style='color:{colour}'>Fail {c['fail']}</span>  ·  N/A {c['na']}  ·  "
            f"Not tested {c['untested']}")

    def _apply_filter(self, *_):
        mode = self.filter.currentText()
        words = self.search.text().lower().split()
        visible = {}
        for r, info in enumerate(self.rowinfo):
            if info["kind"] != "test":
                continue
            entry = self.results.get(info["key"], {})
            result = entry.get("result", "")
            ok = (mode == "All tests" or (mode == "Not tested" and not result) or result == mode)
            text = f"{info['label']} {entry.get('note', '')} {info['section']}".lower()
            ok = ok and all(w in text for w in words)
            self.table.setRowHidden(r, not ok)
            visible[info["section"]] = visible.get(info["section"], False) or ok
        for r, info in enumerate(self.rowinfo):
            if info["kind"] == "header":
                self.table.setRowHidden(r, not visible.get(info["section"], False))

    # ------------------------------------------------------------------ bulk actions
    def _untested_keys(self):
        return [i["key"] for i in self.rowinfo
                if i["kind"] == "test" and not self.results.get(i["key"], {}).get("result")]

    def _bulk(self, result):
        keys = self._untested_keys()
        if not keys:
            QMessageBox.information(self, "Test Device", "Nothing left untested.")
            return
        if QMessageBox.question(self, "Test Device",
                                f"Mark {len(keys)} untested item(s) as {result}?") != QMessageBox.Yes:
            return
        for key in keys:
            self._store(key, result=result)
        self._refresh_stats()
        self._apply_filter()

    def auto_na(self):
        device = {"brand": self.spec.value_of("brand"), "model": self.spec.value_of("model")}
        count = 0
        for key in not_applicable_keys(device):
            if key in self.row_of and not self.results.get(key, {}).get("result"):
                self._store(key, result=NA, note=NOTE_FOR[APPLIES[key]])
                count += 1
        self._refresh_stats()
        self._apply_filter()
        self.stats.setText(self.stats.text() + f"  ·  Auto N/A set on {count}")

    def clear_all(self):
        if QMessageBox.warning(self, "Clear All", "Reset every result and note for this device?",
                               QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.db.clear_tests(self.device_id)
        self.results = self.db.device_tests(self.device_id)
        self.dirty = True
        self._build_rows()
        self._refresh_stats()

    def add_custom(self):
        label, ok = QInputDialog.getText(self, "Add Custom Test", "What should be tested?")
        label = label.strip()
        if not ok or not label:
            return
        key = CUSTOM_PREFIX + label
        if key in self.results:
            QMessageBox.information(self, "Add Custom Test", "That test already exists.")
            return
        self.db.set_test(self.device_id, key, "", "")
        self.results[key] = {"result": "", "note": ""}
        self.dirty = True
        self.filter.setCurrentIndex(0)
        self.search.clear()
        self._build_rows()
        self._refresh_stats()
        self.table.setCurrentCell(self.row_of[key], 0)
        self.table.scrollToItem(self.table.item(self.row_of[key], 0))

    def _menu(self, pos):
        row = self.table.rowAt(pos.y())
        if not (0 <= row < len(self.rowinfo)) or self.rowinfo[row]["kind"] != "test":
            return
        self.table.setCurrentCell(row, 0)
        key = self.rowinfo[row]["key"]
        menu = QMenu(self)
        for name in RESULTS:
            menu.addAction(name, lambda n=name: self._on_key_result(row, n))
        menu.addAction("Clear result", lambda: self._on_key_result(row, ""))
        menu.addAction("Edit note", lambda: self._edit_note(row))
        if is_custom(key):
            menu.addSeparator()
            menu.addAction("Remove this custom test", lambda: self._remove_custom(key))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _remove_custom(self, key):
        self.db.delete_test(self.device_id, key)
        self.results.pop(key, None)
        self.dirty = True
        self._build_rows()
        self._refresh_stats()

    # ------------------------------------------------------------------ details tab
    def _build_details_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        fields = [replace(f, label="Cosmetic condition" if f.key == "grade" else f.label,
                          required=False)
                  for f in DEVICE_FIELDS if f.key in SPEC_KEYS]
        self.spec = FormPanel(self.db, "devices", fields)
        self.spec.set_values(self.device)
        self._spec_initial = self.spec.values()
        box = QGroupBox("Device details (printed on the report)")
        QVBoxLayout(box).addWidget(self.spec)
        lay.addWidget(box)
        self.imei_hint = QLabel()
        lay.addWidget(self.imei_hint)
        self.spec.widgets["imei"].editingFinished.connect(self._check_imei)

        info = QGroupBox("Test details")
        il = QVBoxLayout(info)
        row = QHBoxLayout()
        row.addWidget(QLabel("Tested by"))
        self.tested_by = QLineEdit(self.meta.get("tested_by") or self.db.setting("tester_name"))
        row.addWidget(self.tested_by, 1)
        row.addWidget(QLabel("Date tested"))
        self.tested_at = QDateEdit(calendarPopup=True)
        self.tested_at.setDisplayFormat("dd/MM/yyyy")
        d = QDate.fromString(str(self.meta.get("tested_at") or "")[:10], "yyyy-MM-dd")
        self.tested_at.setDate(d if d.isValid() else QDate.currentDate())
        row.addWidget(self.tested_at)
        il.addLayout(row)
        il.addWidget(QLabel("Overall notes (printed on the report)"))
        self.overall = QPlainTextEdit(self.meta.get("notes") or "")
        self.overall.setFixedHeight(100)
        il.addWidget(self.overall)
        lay.addWidget(info)
        lay.addStretch(1)
        self._meta_initial = self._meta_now()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, "Device details")

    def _meta_now(self):
        return (self.tested_by.text().strip(), self.tested_at.date().toString("yyyy-MM-dd"),
                self.overall.toPlainText().strip())

    def _check_imei(self):
        imei = self.spec.value_of("imei")
        msgs = []
        if imei and not imei_valid(imei):
            msgs.append("IMEI does not pass the checksum - double check it.")
        if imei and self.db.find_imei(imei, exclude_id=self.device_id):
            msgs.append("Same IMEI already exists on another item.")
        self.imei_hint.setStyleSheet(f"color: {theme.current['bad']}" if msgs else "")
        self.imei_hint.setText("  ".join(msgs))

    # ------------------------------------------------------------------ save / report
    def _save_details(self):
        spec = self.spec.values()
        if spec != self._spec_initial:
            self.db.update("devices", self.device_id, spec)
            self._spec_initial = spec
        meta = self._meta_now()
        if self.dirty or meta != self._meta_initial:
            self.db.set_test_meta(self.device_id, *meta)
            self._meta_initial = meta
        if meta[0]:
            self.db.set_setting("tester_name", meta[0])

    def _log_testing(self):
        if self.dirty:
            counts = summarise(self.results)
            self.db.log("Tested", "device", self.device_id,
                        f"{self.device.get('sku')} {summary_text(counts)}")
            self.dirty = False

    def generate_report(self):
        self._save_details()
        path = export_report(self, self.db, self.device_id)
        if path:
            self.stats.setText(f"Report saved: {fileio.display_name(path)}")

    def done(self, result):
        self._save_details()
        self._log_testing()
        super().done(result)
