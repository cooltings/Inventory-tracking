from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QScrollArea, QToolTip, QVBoxLayout, QWidget)

from . import theme
from .db import device_title, num, part_title
from .widgets import money


class Card(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        t = QLabel(title)
        t.setObjectName("cardTitle")
        self.value = QLabel("-")
        self.value.setObjectName("cardValue")
        self.sub = QLabel("")
        self.sub.setObjectName("cardSub")
        lay.addWidget(t)
        lay.addWidget(self.value)
        lay.addWidget(self.sub)

    def set(self, value, sub="", colour=None):
        self.value.setText(value)
        self.value.setStyleSheet(f"color: {colour};" if colour else "")
        self.sub.setText(sub)


class BarChart(QWidget):
    """Monthly revenue (light bars) and profit (solid bars)."""

    def __init__(self):
        super().__init__()
        self.series = []
        self.setMinimumHeight(260)
        self.setMouseTracking(True)
        self._bars = []

    def set_series(self, series):
        self.series = series
        self.update()

    def paintEvent(self, _):
        c = theme.current
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        left, right, top, bottom = 70, 16, 30, 34
        p.setPen(QColor(c["text"]))
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        p.drawText(left, 18, "Last 12 months - revenue vs profit")
        p.setFont(self.font())
        if not self.series:
            return
        vals = [s["revenue"] for s in self.series] + [s["profit"] for s in self.series]
        vmax = max(max(vals), 1)
        vmin = min(min(vals), 0)
        span = vmax - vmin
        ch = h - top - bottom
        cw = w - left - right

        def y(v):
            return top + (vmax - v) / span * ch

        grid = QPen(QColor(c["border"]))
        grid.setStyle(Qt.DashLine)
        for i in range(5):
            v = vmin + span * i / 4
            yy = y(v)
            p.setPen(grid)
            p.drawLine(left, int(yy), w - right, int(yy))
            p.setPen(QColor(c["muted"]))
            p.drawText(QRectF(0, yy - 9, left - 8, 18), Qt.AlignRight | Qt.AlignVCenter,
                       money(round(v)))
        p.setPen(QPen(QColor(c["muted"])))
        p.drawLine(left, int(y(0)), w - right, int(y(0)))
        n = len(self.series)
        slot = cw / n
        bw = max(4, slot * 0.32)
        self._bars = []
        for i, s in enumerate(self.series):
            x = left + i * slot + slot / 2
            rev = QRectF(x - bw - 1, y(max(s["revenue"], 0)), bw,
                         abs(y(s["revenue"]) - y(0)))
            pr = QRectF(x + 1, min(y(s["profit"]), y(0)), bw, abs(y(s["profit"]) - y(0)))
            p.setPen(Qt.NoPen)
            col = QColor(theme.ACCENT)
            col.setAlpha(90)
            p.setBrush(col)
            p.drawRoundedRect(rev, 3, 3)
            p.setBrush(QColor(c["good"] if s["profit"] >= 0 else c["bad"]))
            p.drawRoundedRect(pr, 3, 3)
            p.setPen(QColor(c["muted"]))
            p.drawText(QRectF(x - slot / 2, h - bottom + 6, slot, 18), Qt.AlignCenter, s["label"])
            self._bars.append((QRectF(x - slot / 2, top, slot, ch), s))
        p.end()

    def mouseMoveEvent(self, e):
        for rect, s in self._bars:
            if rect.contains(e.position()):
                QToolTip.showText(e.globalPosition().toPoint(),
                                  f"{s['label']}\n{s['n']} sales\nRevenue {money(s['revenue'])}\n"
                                  f"Profit {money(s['profit'])}", self)
                return
        QToolTip.hideText()


class Dashboard(QWidget):
    open_item = Signal(str, int)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(14)

        grid = QGridLayout()
        grid.setSpacing(12)
        names = [("working", "Working phones in stock"), ("faulty", "Faulty / repair devices"),
                 ("parts", "Parts in stock"), ("stock_value", "Stock value (cost)"),
                 ("month", "Profit this month"), ("year", "Profit this year"),
                 ("all", "Profit all time"), ("speed", "Avg. days to sell")]
        self.cards = {}
        for i, (key, title) in enumerate(names):
            card = Card(title)
            self.cards[key] = card
            grid.addWidget(card, i // 4, i % 4)
        lay.addLayout(grid)

        chart_frame = QFrame()
        chart_frame.setObjectName("card")
        cl = QVBoxLayout(chart_frame)
        self.chart = BarChart()
        cl.addWidget(self.chart)
        lay.addWidget(chart_frame)

        lists = QHBoxLayout()
        lists.setSpacing(12)
        self.low = self._list_box(lists, "Low stock parts")
        self.aged = self._list_box(lists, "Aged stock")
        self.brands = self._list_box(lists, "Most profitable brands")
        lay.addLayout(lists)
        lay.addStretch(1)

    def _list_box(self, parent_layout, title):
        frame = QFrame()
        frame.setObjectName("card")
        lay = QVBoxLayout(frame)
        label = QLabel(title)
        label.setObjectName("h2")
        lay.addWidget(label)
        lst = QListWidget()
        lst.setMinimumHeight(200)
        lst.itemDoubleClicked.connect(self._open)
        lay.addWidget(lst)
        frame.title_label = label
        parent_layout.addWidget(frame, 1)
        return lst

    def _open(self, item):
        target = item.data(Qt.UserRole)
        if target:
            self.open_item.emit(*target)

    def refresh(self):
        s = self.db.stats()
        c = theme.current
        good = lambda v: c["good"] if v >= 0 else c["bad"]
        self.cards["working"].set(str(int(s["working"]["n"])),
                                  f"Cost {money(s['working']['cost'])}  ·  "
                                  f"listed at {money(s['working']['listv'])}")
        self.cards["faulty"].set(str(int(s["faulty"]["n"])), f"Cost {money(s['faulty']['cost'])}")
        self.cards["parts"].set(str(int(s["parts"]["qty"])),
                                f"{s['parts']['lines']} lines  ·  {len(s['low_stock'])} low stock")
        total = s["working"]["cost"] + s["faulty"]["cost"] + s["parts"]["cost"]
        self.cards["stock_value"].set(money(total), "Phones + faulty + parts")
        for key in ("month", "year", "all"):
            d = s[key]
            margin = f"  ·  {d['profit'] / d['revenue'] * 100:.0f}% margin" if d["revenue"] else ""
            self.cards[key].set(money(d["profit"]),
                                f"{d['n']} sales  ·  revenue {money(d['revenue'])}{margin}",
                                good(d["profit"]))
        days = s["avg_days_to_sell"]
        self.cards["speed"].set(f"{days:.0f}" if days is not None else "-",
                                f"Last 30 days: {s['last30']['n']} sales, "
                                f"{money(s['last30']['profit'])} profit")
        self.chart.set_series(s["monthly"])

        self.low.clear()
        for p in s["low_stock"]:
            it = QListWidgetItem(f"{p['sku']}  {part_title(p)}  -  qty {p['quantity'] or 0}")
            it.setData(Qt.UserRole, ("parts", p["id"]))
            self.low.addItem(it)
        if not s["low_stock"]:
            self.low.addItem("Nothing low. Set 'Low Stock At' on a part to track it.")

        self.aged.parentWidget().title_label.setText(f"Aged stock ({s['aged_days']}+ days)")
        self.aged.clear()
        for d in s["aged"]:
            it = QListWidgetItem(f"{d['sku']}  {device_title(d)}  -  "
                                 f"{d.get('purchase_date') or d['created_at'][:10]}")
            it.setData(Qt.UserRole, (d["category"], d["id"]))
            self.aged.addItem(it)
        if not s["aged"]:
            self.aged.addItem("No aged stock.")

        self.brands.clear()
        for b in s["brands"]:
            self.brands.addItem(f"{b['brand']}  -  {b['n']} sold, profit {money(num(b['profit']))}")
        if not s["brands"]:
            self.brands.addItem("No sales yet.")
