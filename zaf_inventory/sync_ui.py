"""Sync screen and background sync controller (Qt side of sync.py)."""
import socket

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton,
                               QRadioButton, QSpinBox, QVBoxLayout)

from . import sync
from .db import num
from .paths import db_path, is_android
from .widgets import fmt_ts


def device_name() -> str:
    return "Android tablet" if is_android() else socket.gethostname()


class _Worker(QThread):
    progress = Signal(str)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, address, code, parent=None):
        super().__init__(parent)
        self.address, self.code = address, code

    def run(self):
        try:
            summary = sync.sync_with(db_path(), self.address, self.code, device_name(),
                                     progress=self.progress.emit)
        except sync.SyncError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # unexpected - show it rather than crash
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit(summary)


def describe(summary) -> str:
    r, s = summary.get("received", {}), summary.get("sent", {})
    parts = [f"received {r.get('added', 0)} new / {r.get('updated', 0)} changed / "
             f"{r.get('deleted', 0)} deleted",
             f"sent {s.get('added', 0)} new / {s.get('updated', 0)} changed / "
             f"{s.get('deleted', 0)} deleted"]
    photos = summary.get("photos_received", 0) + summary.get("photos_sent", 0)
    if photos:
        parts.append(f"{photos} photo(s) copied")
    return f"Synced with {summary.get('host')}: " + "; ".join(parts)


class SyncController(QObject):
    """Owns the host server and runs client syncs without freezing the window."""
    host_synced = Signal(dict)      # another device synced with us (from a server thread)
    sync_started = Signal()
    sync_progress = Signal(str)
    sync_finished = Signal(dict)
    sync_failed = Signal(str)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.host = None
        self.worker = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.sync_now(quiet=True))

    # ---------------------------------------------------------------- host
    def pairing_code(self) -> str:
        code = self.db.setting("sync_code")
        if not code:
            code = sync.new_code()
            self.db.set_setting("sync_code", code)
        return code

    def start_host(self):
        self.stop_host()
        try:
            self.host = sync.SyncHost(db_path(), self.pairing_code(), device_name(),
                                      port=sync.HTTP_PORT, on_synced=self.host_synced.emit).start()
        except OSError as exc:
            self.host = None
            raise sync.SyncError(f"Could not start syncing on port {sync.HTTP_PORT}: {exc}. "
                                 "Is another copy of the app already running?") from exc

    def stop_host(self):
        if self.host:
            self.host.stop()
            self.host = None

    def restart_host_if_running(self):
        if self.host:
            self.start_host()

    # ---------------------------------------------------------------- client
    def configured(self) -> bool:
        return bool(self.db.setting("sync_peer") and self.db.setting("sync_peer_code"))

    def busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def sync_now(self, quiet=False):
        if self.busy() or not self.configured():
            return False
        self.worker = _Worker(self.db.setting("sync_peer"), self.db.setting("sync_peer_code"), self)
        self.worker.progress.connect(self.sync_progress)
        self.worker.done.connect(self.sync_finished)
        self.worker.failed.connect(lambda msg: self.sync_failed.emit(("[auto] " if quiet else "") + msg))
        self.sync_started.emit()
        self.worker.start()
        return True

    def apply_auto_setting(self):
        """Manual-only unless automatic sync is switched on."""
        self.timer.stop()
        if self.db.setting("sync_auto") == "1" and self.configured():
            minutes = max(1, int(num(self.db.setting("sync_auto_minutes"), 15)))
            self.timer.start(minutes * 60 * 1000)
            QTimer.singleShot(5000, lambda: self.sync_now(quiet=True))

    def shutdown(self):
        self.timer.stop()
        self.stop_host()
        if self.worker:
            self.worker.wait(15000)


class SyncDialog(QDialog):
    def __init__(self, db, controller, parent=None):
        super().__init__(parent)
        self.db, self.ctl = db, controller
        self.setWindowTitle("Sync")
        self.resize(720, 640)
        lay = QVBoxLayout(self)

        # ---------------------------------------------------------------- host
        host = QGroupBox("Let a tablet sync with this device (use this on the Windows PC)")
        hl = QVBoxLayout(host)
        self.host_on = QCheckBox("Allow other devices to sync with this one")
        self.host_on.setChecked(controller.host is not None)
        self.host_on.toggled.connect(self._toggle_host)
        hl.addWidget(self.host_on)
        self.host_info = QLabel()
        self.host_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.host_info.setWordWrap(True)
        hl.addWidget(self.host_info)
        row = QHBoxLayout()
        newcode = QPushButton("New Pairing Code")
        newcode.setToolTip("Devices using the old code will need the new one")
        newcode.clicked.connect(self._new_code)
        row.addWidget(newcode)
        row.addStretch(1)
        hl.addLayout(row)
        tip = QLabel("Windows may ask whether to allow Zaf Inventory through the firewall the first "
                     "time: allow it on <b>private</b> networks. Both devices must be on the same Wi-Fi.")
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        hl.addWidget(tip)
        lay.addWidget(host)

        # ---------------------------------------------------------------- client
        client = QGroupBox("Sync this device with a PC (use this on the tablet)")
        cl = QVBoxLayout(client)
        row = QHBoxLayout()
        row.addWidget(QLabel("PC address"))
        self.peer = QLineEdit(db.setting("sync_peer"))
        self.peer.setPlaceholderText("e.g. 192.168.1.20 (shown on the PC's Sync screen)")
        row.addWidget(self.peer, 1)
        find = QPushButton("Find PC")
        find.clicked.connect(self._find)
        row.addWidget(find)
        cl.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Pairing code"))
        self.peer_code = QLineEdit(db.setting("sync_peer_code"))
        self.peer_code.setMaxLength(6)
        self.peer_code.setPlaceholderText("6 digits from the PC")
        row.addWidget(self.peer_code)
        row.addStretch(1)
        self.sync_btn = QPushButton("Sync Now")
        self.sync_btn.setProperty("primary", True)
        self.sync_btn.clicked.connect(self._sync)
        row.addWidget(self.sync_btn)
        cl.addLayout(row)

        self.manual = QRadioButton("Manual only: sync just when I press Sync")
        self.auto = QRadioButton("Automatic: also sync when the app opens and every")
        group = QButtonGroup(self)
        group.addButton(self.manual)
        group.addButton(self.auto)
        (self.auto if db.setting("sync_auto") == "1" else self.manual).setChecked(True)
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 24 * 60)
        self.minutes.setSuffix(" minutes")
        self.minutes.setValue(int(num(db.setting("sync_auto_minutes"), 15)))
        cl.addWidget(self.manual)
        row = QHBoxLayout()
        row.addWidget(self.auto)
        row.addWidget(self.minutes)
        row.addStretch(1)
        cl.addLayout(row)
        lay.addWidget(client)

        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.log = QListWidget()
        lay.addWidget(self.log, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)

        controller.sync_progress.connect(self._progress)
        controller.sync_finished.connect(self._finished)
        controller.sync_failed.connect(self._failed)
        controller.host_synced.connect(self._host_synced)
        self._refresh_host()
        self._show_last()
        if controller.busy():
            self._busy(True)

    # ---------------------------------------------------------------- host side
    def _refresh_host(self):
        if self.ctl.host is None:
            self.host_info.setText("Syncing is <b>off</b> on this device.")
            return
        ips = sync.local_addresses() or ["(no network found)"]
        addresses = "<br>".join(f"<b style='font-size:13pt'>{ip}</b>" for ip in ips)
        self.host_info.setText(
            f"On the tablet, press <b>Find PC</b> or type this address:<br>{addresses}<br>"
            f"Pairing code: <b style='font-size:16pt; letter-spacing:3px'>"
            f"{self.ctl.pairing_code()}</b>")

    def _toggle_host(self, on):
        try:
            if on:
                self.ctl.start_host()
            else:
                self.ctl.stop_host()
            self.db.set_setting("sync_host_enabled", "1" if on else "0")
        except sync.SyncError as exc:
            QMessageBox.warning(self, "Sync", str(exc))
            self.host_on.blockSignals(True)
            self.host_on.setChecked(False)
            self.host_on.blockSignals(False)
        self._refresh_host()

    def _new_code(self):
        self.db.set_setting("sync_code", sync.new_code())
        self.ctl.restart_host_if_running()
        self._refresh_host()

    def _host_synced(self, received):
        self.log.insertItem(0, f"{fmt_ts(self.db.setting('last_sync'))}  A device synced with "
                               f"this one: received {received.get('added', 0)} new, "
                               f"{received.get('updated', 0)} changed, "
                               f"{received.get('deleted', 0)} deleted")
        self._show_last()

    # ---------------------------------------------------------------- client side
    def _save_client(self):
        self.db.set_setting("sync_peer", self.peer.text().strip())
        self.db.set_setting("sync_peer_code", self.peer_code.text().strip())
        self.db.set_setting("sync_auto", "1" if self.auto.isChecked() else "0")
        self.db.set_setting("sync_auto_minutes", self.minutes.value())
        self.ctl.apply_auto_setting()

    def _find(self):
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            found = sync.discover()
        finally:
            QGuiApplication.restoreOverrideCursor()
        if not found:
            QMessageBox.information(self, "Find PC", "No PC found. Check syncing is switched on in "
                                                     "the PC app and both are on the same Wi-Fi, "
                                                     "or type the address shown on the PC.")
            return
        self.peer.setText(found[0]["address"])
        self.status.setText(f"Found {found[0]['name']} at {found[0]['address']}"
                            + (f" (+{len(found) - 1} more)" if len(found) > 1 else ""))

    def _sync(self):
        if not self.peer.text().strip() or len(self.peer_code.text().strip()) != 6:
            QMessageBox.information(self, "Sync", "Enter the PC address and the 6-digit pairing "
                                                  "code shown on the PC's Sync screen.")
            return
        self._save_client()
        if self.ctl.sync_now():
            self._busy(True)

    def _busy(self, busy):
        self.sync_btn.setEnabled(not busy)
        self.sync_btn.setText("Syncing..." if busy else "Sync Now")

    def _progress(self, msg):
        self.status.setText(msg)

    def _finished(self, summary):
        self._busy(False)
        text = describe(summary)
        self.status.setText(text)
        self.log.insertItem(0, f"{fmt_ts(self.db.setting('last_sync'))}  {text}")

    def _failed(self, msg):
        self._busy(False)
        self.status.setText(f"<span style='color:#f87171'>Sync failed: {msg}</span>")
        self.log.insertItem(0, f"Failed: {msg}")

    def _show_last(self):
        last = self.db.setting("last_sync")
        if not self.status.text():
            self.status.setText(f"Last sync: {fmt_ts(last) if last else 'never'}")

    def done(self, result):
        self._save_client()
        for sig, slot in ((self.ctl.sync_progress, self._progress),
                          (self.ctl.sync_finished, self._finished),
                          (self.ctl.sync_failed, self._failed),
                          (self.ctl.host_synced, self._host_synced)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        super().done(result)
