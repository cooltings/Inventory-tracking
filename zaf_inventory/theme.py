import os

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QDialog

from .paths import is_android

ACCENT = "#3b82f6"

COLORS = {
    "dark": {"bg": "#1e2128", "panel": "#262a33", "alt": "#2b303a", "text": "#e6e8ec",
             "muted": "#9aa3b2", "border": "#3a404c", "good": "#4ade80", "bad": "#f87171",
             "warn": "#fbbf24", "warn_bg": "#4a3b12", "bad_bg": "#4a1f1f", "good_bg": "#173a26"},
    "light": {"bg": "#f4f6f9", "panel": "#ffffff", "alt": "#f0f3f7", "text": "#1d2330",
              "muted": "#5b6474", "border": "#d5dae2", "good": "#15803d", "bad": "#b91c1c",
              "warn": "#a16207", "warn_bg": "#fff4cc", "bad_bg": "#fde2e2", "good_bg": "#dcfce7"},
}

current = dict(COLORS["dark"])


_COMPACT = None


TOUCH_QSS = """
    QPushButton { padding: 9px 16px; min-height: 22px; }
    QToolButton { padding: 9px 12px; }
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit { min-height: 30px; }
    QTabBar::tab { padding: 11px 18px; }
    QCheckBox::indicator, QRadioButton::indicator { width: 22px; height: 22px; }
    QScrollBar:vertical { width: 16px; } QScrollBar:horizontal { height: 16px; }
    QTabBar::scroller { width: 48px; }
    QTabBar QToolButton { min-height: 34px; border: 1px solid #3a404c; background: #262a33; }
    QToolBarExtension { min-width: 36px; }
"""


def compact() -> bool:
    """Smaller screens (tablets, small laptops): stack the filter bars, wrap button rows.
    ZAF_COMPACT=1 / 0 forces it on or off."""
    global _COMPACT
    if _COMPACT is None:
        forced = os.environ.get("ZAF_COMPACT")
        if forced in ("0", "1"):
            _COMPACT = forced == "1"
        else:
            screen = QGuiApplication.primaryScreen()
            g = screen.availableGeometry() if screen else None
            _COMPACT = is_android() or bool(g and (g.width() < 1300 or g.height() < 800))
    return _COMPACT


def touch() -> bool:
    """Bigger tap targets on Android (ZAF_TOUCH=1 forces it, for testing)."""
    return is_android() or os.environ.get("ZAF_TOUCH") == "1"


class FitToScreen(QObject):
    """Keeps every dialog within the screen, whatever size it asks for."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QDialog):
            screen = obj.screen() or QGuiApplication.primaryScreen()
            if screen is not None:
                g = screen.availableGeometry()
                w = min(obj.width(), int(g.width() * 0.97))
                h = min(obj.height(), int(g.height() * 0.95))
                if (w, h) != (obj.width(), obj.height()) or not g.contains(obj.frameGeometry()):
                    obj.resize(w, h)
                    obj.move(g.x() + (g.width() - w) // 2, g.y() + (g.height() - h) // 2)
        return False


def apply_theme(app, mode="dark"):
    mode = mode if mode in COLORS else "dark"
    c = COLORS[mode]
    current.clear()
    current.update(c)
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(c["bg"]))
    pal.setColor(QPalette.WindowText, QColor(c["text"]))
    pal.setColor(QPalette.Base, QColor(c["panel"]))
    pal.setColor(QPalette.AlternateBase, QColor(c["alt"]))
    pal.setColor(QPalette.Text, QColor(c["text"]))
    pal.setColor(QPalette.Button, QColor(c["panel"]))
    pal.setColor(QPalette.ButtonText, QColor(c["text"]))
    pal.setColor(QPalette.ToolTipBase, QColor(c["panel"]))
    pal.setColor(QPalette.ToolTipText, QColor(c["text"]))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.PlaceholderText, QColor(c["muted"]))
    pal.setColor(QPalette.Link, QColor(ACCENT))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(c["muted"]))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(c["muted"]))
    app.setPalette(pal)
    import sys
    if sys.platform == "win32":
        app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(f"""
        QToolBar {{ background: {c['panel']}; border: none; border-bottom: 1px solid {c['border']};
                    padding: 4px; spacing: 4px; }}
        QToolButton {{ padding: 5px 9px; border-radius: 6px; }}
        QToolButton:hover {{ background: {c['alt']}; }}
        QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 6px; top: -1px; }}
        QTabBar::tab {{ padding: 8px 16px; border: 1px solid transparent; margin-right: 2px;
                        border-top-left-radius: 6px; border-top-right-radius: 6px; color: {c['muted']}; }}
        QTabBar::tab:selected {{ background: {c['panel']}; border-color: {c['border']};
                                 border-bottom-color: {c['panel']}; color: {c['text']}; font-weight: 600; }}
        QTabBar::tab:hover {{ color: {c['text']}; }}
        QTableView {{ gridline-color: {c['border']}; border: 1px solid {c['border']};
                      border-radius: 6px; selection-background-color: {ACCENT}; }}
        QHeaderView::section {{ background: {c['alt']}; padding: 6px; border: none;
                                border-right: 1px solid {c['border']};
                                border-bottom: 1px solid {c['border']}; font-weight: 600; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QPlainTextEdit, QTextEdit {{
            border: 1px solid {c['border']}; border-radius: 5px; padding: 4px 6px;
            background: {c['panel']}; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
        QDateEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
        QPushButton {{ border: 1px solid {c['border']}; border-radius: 6px; padding: 6px 14px;
                       background: {c['panel']}; }}
        QPushButton:hover {{ border-color: {ACCENT}; }}
        QPushButton[primary="true"] {{ background: {ACCENT}; color: white; border-color: {ACCENT};
                                       font-weight: 600; }}
        QGroupBox {{ border: 1px solid {c['border']}; border-radius: 6px; margin-top: 14px;
                     padding-top: 8px; font-weight: 600; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
        QFrame#card {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 10px; }}
        QLabel#cardTitle {{ color: {c['muted']}; font-size: 9pt; }}
        QLabel#cardValue {{ font-size: 17pt; font-weight: 700; }}
        QLabel#cardSub {{ color: {c['muted']}; font-size: 9pt; }}
        QLabel#muted {{ color: {c['muted']}; }}
        QLabel#h2 {{ font-size: 12pt; font-weight: 700; }}
        QStatusBar {{ background: {c['panel']}; border-top: 1px solid {c['border']}; }}
        QListWidget {{ border: 1px solid {c['border']}; border-radius: 6px; }}
    """ + (TOUCH_QSS if touch() else ""))


def app_icon() -> QIcon:
    pix = QPixmap(256, 256)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(ACCENT))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(8, 8, 240, 240, 48, 48)
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(80, 40, 96, 176, 16, 16)
    p.setBrush(QColor(ACCENT))
    p.drawRoundedRect(92, 60, 72, 120, 6, 6)
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(118, 190, 20, 20)
    p.end()
    return QIcon(pix)
