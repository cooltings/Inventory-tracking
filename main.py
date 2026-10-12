"""Zaf Inventory - start here.  Run:  python main.py"""
import sys
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox

from zaf_inventory import APP_ID, APP_NAME
from zaf_inventory.db import Database
from zaf_inventory.main_window import MainWindow
from zaf_inventory.paths import data_dir, db_path
from zaf_inventory.theme import app_icon, apply_theme


def _excepthook(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        (data_dir() / "error.log").open("a", encoding="utf-8").write(text + "\n")
    except OSError:
        pass
    QMessageBox.critical(None, APP_NAME, f"Something went wrong:\n\n{exc}\n\n"
                                         "Details were saved to error.log in the data folder. "
                                         "Your data is safe.")


def main():
    if sys.platform == "win32":
        try:  # own taskbar icon/group instead of python.exe's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ID)
    app.setWindowIcon(app_icon())
    sys.excepthook = _excepthook
    try:
        db = Database(db_path())
    except Exception as exc:
        QMessageBox.critical(None, APP_NAME, f"Could not open the database:\n{exc}\n\n"
                                             f"Data folder: {data_dir()}")
        return 1
    apply_theme(app, db.setting("theme"))
    win = MainWindow(db)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
