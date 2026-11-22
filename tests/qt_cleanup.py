"""Close and delete every window before Python exits.

Left to Python's own shutdown, Qt objects can be freed in an order that crashes
(it shows up as a segfault after a test has passed, on a different OS each time).
"""
import gc

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication


def finish():
    app = QApplication.instance()
    if app is None:
        return
    for w in QApplication.topLevelWidgets():
        w.close()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    gc.collect()
