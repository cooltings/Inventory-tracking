"""eBay fees & profit calculator."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)

from . import theme
from .db import num
from .widgets import UI, money


def calculate(price, pp_charged, item_cost, postage_cost, other_costs,
              fee_pct, fee_fixed, promo_pct, extra_pct):
    """Return a dict with the full fee / profit breakdown. Negative results are allowed."""
    gross = price + pp_charged
    fvf = gross * fee_pct / 100
    fixed = fee_fixed if gross > 0 else 0.0
    promo = gross * promo_pct / 100
    extra = gross * extra_pct / 100
    fees = fvf + fixed + promo + extra
    costs = item_cost + postage_cost + other_costs
    profit = gross - fees - costs
    pct_total = (fee_pct + promo_pct + extra_pct) / 100

    def price_for(target):
        # gross*(1-pct) - fixed - costs = target  ->  solve for the item price
        if pct_total >= 1:
            return None
        return (target + costs + fee_fixed) / (1 - pct_total) - pp_charged

    return {"gross": gross, "fvf": fvf, "fixed": fixed, "promo": promo, "extra": extra,
            "fees": fees, "costs": costs, "profit": profit,
            "margin": profit / gross * 100 if gross else None,
            "roi": profit / costs * 100 if costs else None,
            "break_even": price_for(0.0), "price_for": price_for}


class FeeCalculatorTab(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)

        left = QVBoxLayout()
        sale = QGroupBox("Sale")
        f = QFormLayout(sale)
        self.price = self._money()
        self.pp = self._money()
        f.addRow("Sale price", self.price)
        f.addRow("P&&P charged to buyer", self.pp)
        left.addWidget(sale)

        costs = QGroupBox("Your costs")
        f = QFormLayout(costs)
        self.cost = self._money()
        self.postage = self._money()
        self.other = self._money()
        f.addRow("Item cost (bought for + repairs)", self.cost)
        f.addRow("Postage cost", self.postage)
        f.addRow("Packaging / other", self.other)
        left.addWidget(costs)

        fees = QGroupBox("Fees")
        f = QFormLayout(fees)
        self.fee_pct = self._pct()
        self.fee_fixed = self._money()
        self.promo = self._pct()
        self.extra = self._pct()
        f.addRow("Final value fee %", self.fee_pct)
        f.addRow("Fixed fee per order", self.fee_fixed)
        f.addRow("Promoted listing %", self.promo)
        f.addRow("Other % (intl., payment, VAT on fees)", self.extra)
        reset = QPushButton("Reset fees to my defaults")
        reset.clicked.connect(self.load_defaults)
        f.addRow("", reset)
        left.addWidget(fees)

        target = QGroupBox("Target")
        f = QFormLayout(target)
        self.target = self._money()
        self.target.setValue(20)
        f.addRow("Profit I want", self.target)
        left.addWidget(target)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        left.addWidget(clear)
        left.addStretch(1)
        outer.addLayout(left, 2)

        # ---- results
        card = QFrame()
        card.setObjectName("card")
        right = QVBoxLayout(card)
        right.setContentsMargins(24, 20, 24, 20)
        head = QLabel("Result")
        head.setObjectName("cardTitle")
        right.addWidget(head)
        self.profit = QLabel()
        self.profit.setStyleSheet("font-size: 30pt; font-weight: 800;")
        right.addWidget(self.profit)
        self.margin = QLabel()
        self.margin.setObjectName("h2")
        right.addWidget(self.margin)
        right.addSpacing(12)
        self.grid = QGridLayout()
        self.grid.setVerticalSpacing(6)
        right.addLayout(self.grid)
        self.labels = {}
        rows = [("gross", "Buyer pays (price + P&P)"), ("fvf", "Final value fee"),
                ("fixed", "Fixed order fee"), ("promo", "Promoted listing fee"),
                ("extra", "Other fees"), ("fees", "Total fees"), ("costs", "Total costs"),
                ("net", "You receive after fees"), ("break_even", "Break-even sale price"),
                ("target", "Sale price for target profit")]
        for i, (key, label) in enumerate(rows):
            name = QLabel(label)
            value = QLabel()
            value.setAlignment(Qt.AlignRight)
            if key in ("fees", "break_even", "target"):
                name.setStyleSheet("font-weight: 600;")
                value.setStyleSheet("font-weight: 600;")
            self.grid.addWidget(name, i, 0)
            self.grid.addWidget(value, i, 1)
            self.labels[key] = value
        right.addStretch(1)
        tip = QLabel("eBay charges its final value fee on the total the buyer pays, including "
                     "postage. Check your current rate for the category on eBay's fees page and "
                     "set it in Settings.")
        tip.setWordWrap(True)
        tip.setObjectName("muted")
        right.addWidget(tip)
        outer.addWidget(card, 3)

        self.load_defaults()

    def _money(self):
        w = QDoubleSpinBox()
        w.setRange(-1_000_000, 10_000_000)
        w.setDecimals(2)
        w.setPrefix(UI["currency"])
        w.setAlignment(Qt.AlignRight)
        w.valueChanged.connect(self.recalc)
        return w

    def _pct(self):
        w = QDoubleSpinBox()
        w.setRange(0, 100)
        w.setDecimals(2)
        w.setSuffix(" %")
        w.setAlignment(Qt.AlignRight)
        w.valueChanged.connect(self.recalc)
        return w

    def load_defaults(self):
        for w in (self.price, self.pp, self.cost, self.postage, self.other, self.fee_fixed,
                  self.target):
            w.setPrefix(UI["currency"])
        self.fee_pct.setValue(num(self.db.setting("fee_percent")))
        self.fee_fixed.setValue(num(self.db.setting("fee_fixed")))
        self.recalc()

    def clear(self):
        for w in (self.price, self.pp, self.cost, self.postage, self.other, self.promo, self.extra):
            w.setValue(0)

    def load_item(self, cost, price):
        self.cost.setValue(cost)
        self.price.setValue(price)

    def recalc(self):
        if not hasattr(self, "labels"):
            return  # still building the widgets
        r = calculate(self.price.value(), self.pp.value(), self.cost.value(), self.postage.value(),
                      self.other.value(), self.fee_pct.value(), self.fee_fixed.value(),
                      self.promo.value(), self.extra.value())
        c = theme.current
        colour = c["good"] if r["profit"] >= 0 else c["bad"]
        word = "Profit" if r["profit"] >= 0 else "Loss"
        self.profit.setText(f"<span style='color:{colour}'>{word} {money(r['profit'])}</span>")
        parts = []
        if r["margin"] is not None:
            parts.append(f"Margin {r['margin']:.1f}%")
        if r["roi"] is not None:
            parts.append(f"Return on cost {r['roi']:.1f}%")
        self.margin.setText("    ".join(parts) or "Enter a sale price to start")
        L = self.labels
        for key in ("gross", "fvf", "fixed", "promo", "extra", "fees", "costs"):
            L[key].setText(money(r[key]))
        L["net"].setText(money(r["gross"] - r["fees"]))
        be = r["break_even"]
        L["break_even"].setText(money(be) if be is not None else "n/a")
        tp = r["price_for"](self.target.value())
        L["target"].setText(money(tp) if tp is not None else "n/a")
