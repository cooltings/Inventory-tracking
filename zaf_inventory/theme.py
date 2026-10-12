from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap

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
    """)


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
