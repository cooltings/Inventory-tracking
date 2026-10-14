from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QApplication, QLabel, QMainWindow, QMessageBox, QTabWidget,
                               QToolBar)

from . import APP_ID, APP_NAME, __version__, backup, theme
from .calculator import FeeCalculatorTab
from .dashboard import Dashboard
from .db import num
from .dialogs import (BackupDialog, GlobalSearchDialog, SettingsDialog, choose_backup_file,
                      restore_with_prompt)
from .paths import data_dir, is_portable
from .sheets import SheetsTab
from .views import ActivityTab, InventoryTab
from .widgets import UI, fmt_ts

SHORTCUTS = """
<b>Ctrl+N</b> Add item &nbsp; <b>Enter</b> Edit &nbsp; <b>Del</b> Delete<br>
<b>Ctrl+M</b> Notes &amp; photos &nbsp; <b>Ctrl+S</b> Mark as sold<br>
<b>Ctrl+T</b> Test device &nbsp; <b>Ctrl+R</b> Test report PDF<br>
<b>Ctrl+U</b> Use part on device &nbsp; <b>Ctrl+D</b> Duplicate<br>
<b>Ctrl+F</b> Search this tab &nbsp; <b>Ctrl+K</b> Search everything<br>
<b>Ctrl+Shift+I</b> Import backup .zip<br>
<b>Ctrl+B</b> Back up now &nbsp; <b>F5</b> Refresh &nbsp; <b>Ctrl+1..8</b> Switch tab<br>
<b>Ctrl+C</b> Copy selected rows (paste into Excel / Sheets)<br>
Right-click a column heading to show / hide columns.
"""


class MainWindow(QMainWindow):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.setWindowTitle(APP_NAME + ("  (portable)" if is_portable() else ""))
        self.setWindowIcon(theme.app_icon())
        self.resize(1440, 860)
        UI["currency"] = db.setting("currency")

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.setCentralWidget(self.tabs)
        self.dashboard = Dashboard(db)
        self.dashboard.open_item.connect(self.jump_to)
        self.inv = {k: InventoryTab(db, k) for k in ("working", "faulty", "parts", "sales")}
        self.sheets = SheetsTab(db)
        self.calc = FeeCalculatorTab(db)
        self.activity = ActivityTab(db)
        self.tabs.addTab(self.dashboard, "Dashboard")
        self.tabs.addTab(self.inv["working"], "Working Phones")
        self.tabs.addTab(self.inv["faulty"], "Faulty / Repairs")
        self.tabs.addTab(self.inv["parts"], "Parts")
        self.tabs.addTab(self.inv["sales"], "Sold")
        self.tabs.addTab(self.sheets, "Sheets")
        self.tabs.addTab(self.calc, "Fee Calculator")
        self.tabs.addTab(self.activity, "Activity Log")
        for tab in self.inv.values():
            tab.changed.connect(self._data_changed)
            tab.status_message.connect(lambda m: self.statusBar().showMessage(m, 6000))
        self.tabs.currentChanged.connect(self._tab_changed)

        self._build_actions()
        self.backup_label = QLabel()
        self.statusBar().addPermanentWidget(self.backup_label)
        self._update_backup_label()

        settings = QSettings(APP_ID, APP_ID)
        geo = settings.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        self.tabs.setCurrentIndex(int(settings.value("window/tab", 0)))
        self._tab_changed(self.tabs.currentIndex())

        # automatic backups: check now (after the window shows) and then every 30 minutes
        QTimer.singleShot(1500, self._auto_backup)
        self.backup_timer = QTimer(self)
        self.backup_timer.timeout.connect(self._auto_backup)
        self.backup_timer.start(30 * 60 * 1000)

    # ------------------------------------------------------------------ actions
    def _act(self, text, slot, shortcut=None, tip=None):
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.WindowShortcut)
        a.setToolTip(f"{tip or text}" + (f"  ({shortcut})" if shortcut else ""))
        self.addAction(a)
        return a

    def _build_actions(self):
        t = self._on_tab
        self.a_add = self._act("Add", t("add"), "Ctrl+N", "Add a new item")
        self.a_edit = self._act("Edit", t("edit"), None, "Edit the selected item")
        self.a_notes = self._act("Notes", t("notes"), "Ctrl+M", "Notes & photos for the item")
        self.a_test = self._act("Test", t("test_device"), "Ctrl+T",
                                "Run the test checklist on this phone")
        self.a_report = self._act("Test Report", t("test_report"), "Ctrl+R",
                                  "Create a PDF test report for this phone")
        self.a_sell = self._act("Mark Sold", t("sell"), "Ctrl+S", "Record a sale for this item")
        self.a_part = self._act("Use Part", t("use_part"), "Ctrl+U", "Fit a part from stock")
        self.a_move = self._act("Move", t("move"), None, "Move between Working and Faulty")
        self.a_return = self._act("Return/Refund", t("return_sale"), None,
                                  "Mark a sale returned / refunded")
        self.a_dup = self._act("Duplicate", t("duplicate"), "Ctrl+D")
        self.a_writeoff = self._act("Write Off...", t("write_off"), None,
                                    "Write off the selected items (kept on record, not counted)")
        self.a_delete = self._act("Delete", t("delete"), None, "Delete the selected items")
        self.a_calc = self._act("Calculate", self._send_to_calc, None,
                                "Open the fee calculator with this item's cost and price")
        self.a_search = self._act("Search All", self.global_search, "Ctrl+K",
                                  "Search every tab, notes included")
        self.a_backup = self._act("Back Up", self.backup_now, "Ctrl+B", "Back up now")
        self.a_backups = self._act("Backup && Restore...", self.open_backups)
        self.a_import_backup = self._act("Import Backup (.zip)...", self.import_backup,
                                         "Ctrl+Shift+I", "Load a backup / import zip")
        self.a_settings = self._act("Settings", self.open_settings, "Ctrl+,")
        self.a_export = self._act("Export CSV...", t("export_csv"), "Ctrl+E")
        self.a_import = self._act("Import CSV...", t("import_csv"), "Ctrl+I")
        self.a_refresh = self._act("Refresh", self.refresh_all, "F5")
        for i in range(8):
            self._act(f"Tab {i + 1}", lambda _=False, i=i: self.tabs.setCurrentIndex(i),
                      f"Ctrl+{i + 1}")

        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb)
        for a in (self.a_add, self.a_edit, self.a_notes, self.a_test, self.a_report, self.a_sell,
                  self.a_part, self.a_move,
                  self.a_return, self.a_calc, self.a_delete):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.a_search, self.a_backup, self.a_settings):
            tb.addAction(a)

        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction(self.a_import_backup)
        m.addAction(self.a_import)
        m.addAction(self.a_export)
        m.addSeparator()
        m.addAction(self.a_backup)
        m.addAction(self.a_backups)
        m.addAction("Open Data Folder", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(data_dir()))))
        m.addAction("Check && Compact Database", self.maintenance)
        m.addSeparator()
        m.addAction("Exit", self.close)
        m = mb.addMenu("&Edit")
        for a in (self.a_add, self.a_edit, self.a_dup, self.a_delete):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_settings)
        m = mb.addMenu("&Item")
        for a in (self.a_notes, self.a_test, self.a_report, self.a_sell, self.a_part, self.a_move,
                  self.a_return, self.a_calc, self.a_writeoff):
            m.addAction(a)
        m = mb.addMenu("&View")
        m.addAction(self.a_search)
        m.addAction(self.a_refresh)
        m.addAction("Toggle Dark / Light Theme", self.toggle_theme)
        m = mb.addMenu("&Help")
        m.addAction("Keyboard Shortcuts", lambda: QMessageBox.information(
            self, "Keyboard Shortcuts", SHORTCUTS))
        m.addAction("About", self.about)

    def _on_tab(self, method):
        def run(*_):
            tab = self.tabs.currentWidget()
            if isinstance(tab, InventoryTab):
                getattr(tab, method)()
            elif method == "add" and tab is self.sheets:
                self.sheets.add_row()
            elif method == "export_csv" and tab is self.sheets:
                self.sheets.export_csv()
            else:
                self.statusBar().showMessage("Switch to an inventory tab to use this.", 4000)
        return run

    def _tab_changed(self, index):
        tab = self.tabs.widget(index)
        inv = isinstance(tab, InventoryTab)
        kind = tab.kind if inv else None
        devices = kind in ("working", "faulty")
        self.a_add.setEnabled(inv and kind != "sales" or tab is self.sheets)
        for a in (self.a_edit, self.a_notes, self.a_delete, self.a_export, self.a_import):
            a.setEnabled(inv)
        self.a_export.setEnabled(inv or tab is self.sheets)
        self.a_sell.setEnabled(inv and kind != "sales")
        self.a_dup.setEnabled(inv and kind != "sales")
        self.a_writeoff.setEnabled(inv and kind != "sales")
        self.a_part.setEnabled(devices)
        self.a_test.setEnabled(devices)
        self.a_report.setEnabled(devices)
        self.a_move.setEnabled(devices)
        self.a_move.setText("Move to Faulty" if kind == "working" else "Move to Working")
        self.a_return.setEnabled(kind == "sales")
        self.a_calc.setEnabled(inv and kind != "sales")
        if tab is self.dashboard:
            self.dashboard.refresh()
        elif tab is self.activity:
            self.activity.refresh()
        elif tab is self.sheets:
            self.sheets.load_rows()
        elif tab is self.calc:
            self.calc.load_defaults()
        elif inv:
            tab.refresh()

    def _data_changed(self):
        for tab in self.inv.values():
            if tab is not self.tabs.currentWidget():
                tab.refresh()

    def refresh_all(self):
        UI["currency"] = self.db.setting("currency")
        for tab in self.inv.values():
            tab.refresh()
        self.sheets.refresh()
        self._tab_changed(self.tabs.currentIndex())
        self._update_backup_label()

    def jump_to(self, where, row_id):
        key = {"sold": None}.get(where, where)
        if where == "sold":
            # a sold device: find its sale record
            row = self.db.conn.execute("SELECT id FROM sales WHERE item_kind='device' AND item_id=? "
                                       "ORDER BY id DESC LIMIT 1", (row_id,)).fetchone()
            key, row_id = "sales", row["id"] if row else None
        tab = self.inv.get(key)
        if not tab:
            return
        self.tabs.setCurrentWidget(tab)
        if row_id is not None:
            tab.select_id(row_id)

    def _send_to_calc(self):
        tab = self.tabs.currentWidget()
        if not isinstance(tab, InventoryTab):
            return
        r = tab.current()
        if not r:
            return
        if tab.kind == "parts":
            cost, price = num(r.get("unit_cost")), num(r.get("list_price"))
        else:
            cost, price = num(r.get("purchase_price")) + num(r.get("repair_cost")), \
                num(r.get("list_price"))
        self.tabs.setCurrentWidget(self.calc)
        self.calc.load_item(cost, price)

    def global_search(self):
        dlg = GlobalSearchDialog(self.db, self)
        dlg.chosen.connect(self.jump_to)
        dlg.exec()

    # ------------------------------------------------------------------ settings / theme
    def open_settings(self):
        if SettingsDialog(self.db, self).exec():
            theme.apply_theme(QApplication.instance(), self.db.setting("theme"))
            self.refresh_all()
            self.statusBar().showMessage("Settings saved.", 4000)

    def toggle_theme(self):
        new = "light" if self.db.setting("theme") == "dark" else "dark"
        self.db.set_setting("theme", new)
        theme.apply_theme(QApplication.instance(), new)
        self.refresh_all()

    def about(self):
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<h3>{APP_NAME} {__version__}</h3>"
                          "<p>Stock, parts, repairs, sales and profit tracking for eBay sellers.</p>"
                          f"<p>Data folder:<br><code>{data_dir()}</code></p>"
                          "<p>All data stays on this computer.</p>")

    def maintenance(self):
        result = self.db.integrity_check()
        if result != "ok":
            QMessageBox.critical(self, "Database Check",
                                 f"Problem found: {result}\n\nRestore from a recent backup "
                                 "(File > Backup & Restore).")
            return
        backup.create_backup(self.db, "pre-compact")
        self.db.vacuum()
        self._update_backup_label()
        QMessageBox.information(self, "Database Check", "Database is healthy and has been compacted.")

    # ------------------------------------------------------------------ backups
    def _update_backup_label(self):
        last = self.db.setting("last_backup")
        self.backup_label.setText(f"Last backup: {fmt_ts(last) if last else 'never'}  ")

    def backup_now(self):
        try:
            path = backup.create_backup(self.db, "manual")
        except Exception as exc:
            QMessageBox.critical(self, "Backup Failed", str(exc))
            return
        self._update_backup_label()
        self.statusBar().showMessage(f"Backed up to {path}", 8000)

    def _auto_backup(self):
        if not backup.backup_due(self.db):
            return
        try:
            backup.create_backup(self.db, "auto")
            backup.prune_backups(self.db)
            self._update_backup_label()
            self.statusBar().showMessage("Automatic backup complete.", 5000)
        except Exception as exc:
            self.statusBar().showMessage(f"Automatic backup failed: {exc}", 15000)

    def import_backup(self):
        path = choose_backup_file(self, self.db)
        if path and restore_with_prompt(self, self.db, path):
            self.refresh_all()

    def open_backups(self):
        dlg = BackupDialog(self.db, self)
        dlg.restored.connect(self.refresh_all)
        dlg.exec()
        self._update_backup_label()

    def closeEvent(self, event):
        settings = QSettings(APP_ID, APP_ID)
        settings.setValue("window/geometry", self.saveGeometry())
        settings.setValue("window/tab", self.tabs.currentIndex())
        if self.db.setting("backup_on_exit") == "1":
            try:
                backup.create_backup(self.db, "auto")
                backup.prune_backups(self.db)
            except Exception as exc:
                if QMessageBox.question(self, "Backup Failed",
                                        f"Backup on exit failed:\n{exc}\n\nClose anyway?") \
                        != QMessageBox.Yes:
                    event.ignore()
                    return
        self.db.close()
        event.accept()
