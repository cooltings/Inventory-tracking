"""SQLite storage layer. No Qt imports here so it can be tested on its own."""
import datetime as dt
import json
import sqlite3
from pathlib import Path

from .fields import (DEFAULT_LISTS, DEVICE_FIELDS, EXCLUDED_SALE_STATUSES,
                     PART_FIELDS, SALE_FIELDS, SQL_TYPES)

DEFAULT_SETTINGS = {
    "currency": "£",
    "fee_percent": "12.8",
    "fee_fixed": "0.30",
    "default_platform": "eBay",
    "aged_days": "60",
    "theme": "dark",
    "backup_dir": "",
    "backup_dir_secondary": "",
    "auto_backup": "1",
    "auto_backup_hours": "24",
    "backup_on_exit": "1",
    "backup_keep": "30",
    "last_backup": "",
}

SKU_PREFIX = {"working": "WP", "faulty": "FD", "parts": "PT"}

_EXTRA_COLUMNS = {
    "devices": [("category", "TEXT NOT NULL DEFAULT 'working'"), ("sold_at", "TEXT")],
    "parts": [],
    "sales": [("item_kind", "TEXT"), ("item_id", "INTEGER")],
}
_FIELDS = {"devices": DEVICE_FIELDS, "parts": PART_FIELDS, "sales": SALE_FIELDS}


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def today_iso() -> str:
    return dt.date.today().isoformat()


def num(value, default=0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "").replace("£", "").replace("$", "").replace("€", ""))
    except (TypeError, ValueError):
        return default


def split_faults(text) -> list:
    return [f.strip() for f in str(text or "").replace(",", ";").split(";") if f.strip()]


def imei_valid(imei: str) -> bool:
    """Luhn check for a 15 digit IMEI."""
    digits = "".join(c for c in str(imei) if c.isdigit())
    if len(digits) != 15:
        return False
    total = 0
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def calc_profit(sale: dict) -> float:
    return round(num(sale.get("sale_price")) + num(sale.get("postage_charged"))
                 - num(sale.get("fees")) - num(sale.get("postage_cost"))
                 - num(sale.get("other_costs")) - num(sale.get("cost_basis")), 2)


def device_title(d: dict) -> str:
    """Listing style title, e.g. 'Apple iPhone 12 128GB Black Unlocked - Grade B'."""
    parts = [d.get("brand"), d.get("model"), d.get("storage"), d.get("colour")]
    title = " ".join(str(p).strip() for p in parts if p and str(p).strip())
    network = d.get("network")
    if network and network not in ("Unknown",):
        title += f" {network}" if network == "Unlocked" else f" ({network})"
    grade = str(d.get("grade") or "")
    if grade.startswith("For Parts") or d.get("category") == "faulty":
        title += " - Faulty / Spares or Repair"
    elif " - " in grade:
        title += f" - Grade {grade.split(' - ')[0]}"
    elif grade:
        title += f" - {grade}"
    return title


def part_title(p: dict) -> str:
    bits = [p.get("brand"), p.get("compatible"), p.get("part_type")]
    title = " ".join(str(b).strip() for b in bits if b and str(b).strip())
    if p.get("condition"):
        title += f" ({p['condition']})"
    return title


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.conn = None
        self._columns = {}
        self.open()

    # ------------------------------------------------------------------ lifecycle
    def open(self):
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self):
        if self.conn is not None:
            self.conn.commit()
            self.conn.close()
            self.conn = None

    def _migrate(self):
        c = self.conn
        for table, fields in _FIELDS.items():
            c.execute(f"CREATE TABLE IF NOT EXISTS {table} ("
                      "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                      "created_at TEXT, updated_at TEXT)")
            existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            wanted = [(f.key, SQL_TYPES.get(f.kind, "TEXT")) for f in fields]
            wanted += _EXTRA_COLUMNS[table]
            for name, sqltype in wanted:
                if name not in existing:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}")
                    existing.add(name)
        c.executescript("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity TEXT NOT NULL, entity_id INTEGER NOT NULL,
                created_at TEXT NOT NULL, body TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS ix_notes_entity ON notes(entity, entity_id);
            CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity TEXT NOT NULL, entity_id INTEGER NOT NULL,
                filename TEXT NOT NULL, caption TEXT, created_at TEXT);
            CREATE INDEX IF NOT EXISTS ix_photos_entity ON photos(entity, entity_id);
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, action TEXT, entity TEXT, entity_id INTEGER,
                details TEXT);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS sheets (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                opening_balance REAL DEFAULT 0, position INTEGER DEFAULT 0, created_at TEXT);
            CREATE TABLE IF NOT EXISTS sheet_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sheet_id INTEGER NOT NULL REFERENCES sheets(id) ON DELETE CASCADE,
                row_date TEXT, description TEXT, category TEXT, reference TEXT,
                money_in REAL, money_out REAL, notes TEXT, created_at TEXT);
            CREATE INDEX IF NOT EXISTS ix_sheet_rows ON sheet_rows(sheet_id, row_date);
            CREATE INDEX IF NOT EXISTS ix_devices_cat ON devices(category, status);
            CREATE INDEX IF NOT EXISTS ix_devices_imei ON devices(imei);
            CREATE INDEX IF NOT EXISTS ix_sales_date ON sales(sale_date);
        """)
        c.commit()
        self._columns = {}

    def columns(self, table) -> set:
        if table not in self._columns:
            self._columns[table] = {r["name"] for r in
                                    self.conn.execute(f"PRAGMA table_info({table})")}
        return self._columns[table]

    # ------------------------------------------------------------------ generic CRUD
    def _clean(self, table, data):
        cols = self.columns(table)
        return {k: v for k, v in data.items() if k in cols and k != "id"}

    def insert(self, table, data, commit=True) -> int:
        data = self._clean(table, data)
        now = now_iso()
        data.setdefault("created_at", now)
        data["updated_at"] = now
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        cur = self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})",
                                list(data.values()))
        new_id = cur.lastrowid
        if table in ("devices", "parts") and not data.get("sku"):
            prefix = SKU_PREFIX["parts"] if table == "parts" else \
                SKU_PREFIX.get(data.get("category", "working"), "WP")
            self.conn.execute(f"UPDATE {table} SET sku=? WHERE id=?",
                              (f"{prefix}-{new_id:05d}", new_id))
        if commit:
            self.conn.commit()
        return new_id

    def update(self, table, row_id, data, commit=True):
        data = self._clean(table, data)
        data.pop("created_at", None)
        data["updated_at"] = now_iso()
        sets = ", ".join(f"{k}=?" for k in data)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE id=?",
                          list(data.values()) + [row_id])
        if commit:
            self.conn.commit()

    def get(self, table, row_id):
        row = self.conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
        return dict(row) if row else None

    def delete(self, table, row_id):
        entity = {"devices": "device", "parts": "part", "sales": "sale"}[table]
        for photo in self.photos(entity, row_id):
            self._remove_photo_file(photo["filename"])
        self.conn.execute("DELETE FROM notes WHERE entity=? AND entity_id=?", (entity, row_id))
        self.conn.execute("DELETE FROM photos WHERE entity=? AND entity_id=?", (entity, row_id))
        self.conn.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
        self.conn.commit()

    def distinct(self, table, column) -> list:
        if column not in self.columns(table):
            return []
        rows = self.conn.execute(
            f"SELECT DISTINCT {column} AS v FROM {table} "
            f"WHERE {column} IS NOT NULL AND {column} != '' ORDER BY {column} COLLATE NOCASE")
        return [r["v"] for r in rows]

    # ------------------------------------------------------------------ listings
    def _with_counts(self, table, entity, where="1=1", params=()):
        sql = f"""
            SELECT t.*,
              (SELECT COUNT(*) FROM notes n WHERE n.entity='{entity}' AND n.entity_id=t.id) AS note_count,
              (SELECT body FROM notes n WHERE n.entity='{entity}' AND n.entity_id=t.id
                 ORDER BY n.id DESC LIMIT 1) AS latest_note,
              (SELECT COUNT(*) FROM photos p WHERE p.entity='{entity}' AND p.entity_id=t.id) AS photo_count
            FROM {table} t WHERE {where}"""
        return [dict(r) for r in self.conn.execute(sql, params)]

    def list_devices(self, category):
        rows = self._with_counts("devices", "device",
                                 "t.category=? AND COALESCE(t.status,'') != 'Sold'", (category,))
        today = dt.date.today()
        for r in rows:
            r["total_cost"] = round(num(r.get("purchase_price")) + num(r.get("repair_cost")), 2)
            r["quantity"] = int(num(r.get("quantity"), 1)) if r.get("quantity") is not None else 1
            r["age_days"] = _age_days(r.get("purchase_date") or r.get("created_at"), today)
            r["est_profit"] = round(num(r.get("list_price")) - r["total_cost"], 2) \
                if r.get("list_price") else None
        return rows

    def list_parts(self):
        rows = self._with_counts("parts", "part")
        for r in rows:
            qty = int(num(r.get("quantity")))
            r["stock_value"] = round(qty * num(r.get("unit_cost")), 2)
            r["low_stock"] = qty <= int(num(r.get("min_qty"), 0))
        return rows

    def list_sales(self):
        rows = self._with_counts("sales", "sale")
        rows.sort(key=lambda r: (str(r.get("sale_date") or ""), r["id"]))
        running = 0.0
        for r in rows:
            if r.get("status") not in EXCLUDED_SALE_STATUSES:
                running += num(r.get("profit"))
            r["running_profit"] = round(running, 2)
            price = num(r.get("sale_price")) + num(r.get("postage_charged"))
            r["margin"] = round(num(r.get("profit")) / price * 100, 1) if price else None
            r["excluded"] = r.get("status") in EXCLUDED_SALE_STATUSES
        return rows

    def find_imei(self, imei, exclude_id=None):
        digits = "".join(c for c in str(imei) if c.isalnum())
        if not digits:
            return []
        rows = self.conn.execute("SELECT * FROM devices WHERE REPLACE(REPLACE(imei,' ',''),'-','')=?",
                                 (digits,))
        return [dict(r) for r in rows if r["id"] != exclude_id]

    def global_search(self, text, limit=200):
        like = f"%{text.strip()}%"
        out = []
        q = """SELECT id, category, sku, brand, model, storage, colour, imei, status
               FROM devices WHERE sku LIKE :q OR imei LIKE :q OR serial LIKE :q OR brand LIKE :q
               OR model LIKE :q OR faults LIKE :q OR description LIKE :q OR ebay_item_id LIKE :q
               OR id IN (SELECT entity_id FROM notes WHERE entity='device' AND body LIKE :q)"""
        for r in self.conn.execute(q, {"q": like}):
            r = dict(r)
            where = "sold" if r["status"] == "Sold" else r["category"]
            out.append({"where": where, "id": r["id"], "sku": r["sku"],
                        "title": device_title(r), "detail": r["imei"] or "", "status": r["status"]})
        q = """SELECT * FROM parts WHERE sku LIKE :q OR part_type LIKE :q OR brand LIKE :q
               OR compatible LIKE :q OR part_number LIKE :q OR description LIKE :q
               OR imei LIKE :q OR serial LIKE :q
               OR id IN (SELECT entity_id FROM notes WHERE entity='part' AND body LIKE :q)"""
        for r in self.conn.execute(q, {"q": like}):
            r = dict(r)
            out.append({"where": "parts", "id": r["id"], "sku": r["sku"], "title": part_title(r),
                        "detail": f"Qty {r['quantity'] or 0}", "status": r["status"]})
        q = """SELECT * FROM sales WHERE sku LIKE :q OR title LIKE :q OR order_number LIKE :q
               OR buyer LIKE :q OR imei LIKE :q OR serial LIKE :q OR tracking LIKE :q"""
        for r in self.conn.execute(q, {"q": like}):
            r = dict(r)
            out.append({"where": "sales", "id": r["id"], "sku": r["sku"], "title": r["title"],
                        "detail": f"{r['sale_date']}  {r['buyer'] or ''}", "status": r["status"]})
        return out[:limit]

    # ------------------------------------------------------------------ notes & photos
    def notes(self, entity, entity_id):
        rows = self.conn.execute("SELECT * FROM notes WHERE entity=? AND entity_id=? "
                                 "ORDER BY id DESC", (entity, entity_id))
        return [dict(r) for r in rows]

    def add_note(self, entity, entity_id, body, commit=True):
        body = body.strip()
        if not body:
            return None
        cur = self.conn.execute("INSERT INTO notes (entity, entity_id, created_at, body) "
                                "VALUES (?,?,?,?)", (entity, entity_id, now_iso(), body))
        if commit:
            self.conn.commit()
        return cur.lastrowid

    def update_note(self, note_id, body):
        self.conn.execute("UPDATE notes SET body=? WHERE id=?", (body.strip(), note_id))
        self.conn.commit()

    def delete_note(self, note_id):
        self.conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
        self.conn.commit()

    def photos(self, entity, entity_id):
        rows = self.conn.execute("SELECT * FROM photos WHERE entity=? AND entity_id=? ORDER BY id",
                                 (entity, entity_id))
        return [dict(r) for r in rows]

    def add_photo(self, entity, entity_id, src_path):
        import shutil
        import uuid
        from .paths import photos_dir
        src = Path(src_path)
        name = f"{entity}-{entity_id}-{uuid.uuid4().hex[:8]}{src.suffix.lower()}"
        shutil.copy2(src, photos_dir() / name)
        self.conn.execute("INSERT INTO photos (entity, entity_id, filename, caption, created_at) "
                          "VALUES (?,?,?,?,?)", (entity, entity_id, name, src.name, now_iso()))
        self.conn.commit()

    def delete_photo(self, photo_id):
        row = self.conn.execute("SELECT filename FROM photos WHERE id=?", (photo_id,)).fetchone()
        if row:
            self._remove_photo_file(row["filename"])
        self.conn.execute("DELETE FROM photos WHERE id=?", (photo_id,))
        self.conn.commit()

    @staticmethod
    def _remove_photo_file(filename):
        from .paths import photos_dir
        try:
            (photos_dir() / filename).unlink()
        except OSError:
            pass

    # ------------------------------------------------------------------ business actions
    def record_sale(self, kind, item_id, sale):
        """Create a sale and take the item out of stock, all in one transaction."""
        sale = dict(sale)
        sale["item_kind"] = kind
        sale["item_id"] = item_id
        sale["profit"] = calc_profit(sale)
        with self.conn:
            sale_id = self.insert("sales", sale, commit=False)
            sold_qty = int(num(sale.get("quantity"), 1)) or 1
            if kind == "device":
                device = self.get("devices", item_id)
                left = max(0, int(num(device.get("quantity"), 1)) - sold_qty)
                changes = {"quantity": left}
                if left == 0:
                    # keep the original line quantity for the record, mark it sold
                    changes = {"status": "Sold", "sold_at": now_iso(),
                               "quantity": int(num(device.get("quantity"), 1)) or 1}
                self.update("devices", item_id, changes, commit=False)
            else:
                part = self.get("parts", item_id)
                left = max(0, int(num(part.get("quantity"))) - sold_qty)
                self.update("parts", item_id, {"quantity": left}, commit=False)
            self.log("Sold", kind, item_id,
                     f"{sale.get('sku')} {sale.get('title')} for "
                     f"{num(sale.get('sale_price')):.2f} (profit {sale['profit']:.2f})", commit=False)
        return sale_id

    def update_sale(self, sale_id, data):
        merged = {**(self.get("sales", sale_id) or {}), **data}
        data["profit"] = calc_profit(merged)
        self.update("sales", sale_id, data)

    def return_sale(self, sale_id, new_status, restock):
        sale = self.get("sales", sale_id)
        with self.conn:
            self.update("sales", sale_id, {"status": new_status}, commit=False)
            if restock and sale.get("item_id"):
                device = self.get("devices", sale["item_id"]) if sale["item_kind"] == "device" else None
                if device:
                    back = int(num(sale.get("quantity"), 1)) or 1
                    qty = back if device.get("status") == "Sold" else \
                        int(num(device.get("quantity"), 1)) + back
                    self.update("devices", sale["item_id"],
                                {"status": "In Stock", "sold_at": None, "quantity": qty}, commit=False)
                    self.add_note("device", sale["item_id"],
                                  f"Returned to stock from sale {sale.get('order_number') or sale_id} "
                                  f"({new_status}).", commit=False)
                elif sale["item_kind"] == "part" and self.get("parts", sale["item_id"]):
                    part = self.get("parts", sale["item_id"])
                    qty = int(num(part.get("quantity"))) + int(num(sale.get("quantity"), 1))
                    self.update("parts", sale["item_id"], {"quantity": qty}, commit=False)
            self.log(new_status, "sale", sale_id,
                     f"{sale.get('sku')} {sale.get('title')}" + (" - restocked" if restock else ""),
                     commit=False)

    def use_part(self, part_id, device_id, qty):
        """Fit a part from stock into a device: stock goes down, device cost goes up."""
        part = self.get("parts", part_id)
        device = self.get("devices", device_id)
        cost = round(num(part.get("unit_cost")) * qty, 2)
        with self.conn:
            self.update("parts", part_id,
                        {"quantity": max(0, int(num(part.get("quantity"))) - qty)}, commit=False)
            self.update("devices", device_id,
                        {"repair_cost": round(num(device.get("repair_cost")) + cost, 2)}, commit=False)
            self.add_note("device", device_id,
                          f"Fitted {qty} x {part.get('sku')} {part_title(part)} (+{cost:.2f} cost)",
                          commit=False)
            self.add_note("part", part_id,
                          f"{qty} used in {device.get('sku')} {device_title(device)}", commit=False)
            self.log("Part used", "device", device_id,
                     f"{qty} x {part.get('sku')} into {device.get('sku')}", commit=False)
        return cost

    def move_device(self, device_id, category):
        device = self.get("devices", device_id)
        status = device.get("status")
        if category == "working" and status in ("Awaiting Repair", "In Repair"):
            status = "In Stock"
        if category == "faulty" and status in ("In Stock", "Listed"):
            status = "Awaiting Repair"
        with self.conn:
            self.update("devices", device_id, {"category": category, "status": status}, commit=False)
            label = "Working Phones" if category == "working" else "Faulty / Repairs"
            self.add_note("device", device_id, f"Moved to {label}.", commit=False)
            self.log("Moved", "device", device_id, f"{device.get('sku')} -> {label}", commit=False)

    # ------------------------------------------------------------------ sheets (ledgers)
    SHEET_COLUMNS = ("row_date", "description", "category", "reference",
                     "money_in", "money_out", "notes")

    def sheets(self):
        rows = self.conn.execute("SELECT * FROM sheets ORDER BY position, id")
        return [dict(r) for r in rows]

    def add_sheet(self, name, opening_balance=0.0):
        pos = self.conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM sheets").fetchone()[0]
        cur = self.conn.execute("INSERT INTO sheets (name, opening_balance, position, created_at) "
                                "VALUES (?,?,?,?)", (name, opening_balance, pos, now_iso()))
        self.conn.commit()
        return cur.lastrowid

    def update_sheet(self, sheet_id, **data):
        for k, v in data.items():
            if k in ("name", "opening_balance", "position"):
                self.conn.execute(f"UPDATE sheets SET {k}=? WHERE id=?", (v, sheet_id))
        self.conn.commit()

    def delete_sheet(self, sheet_id):
        self.conn.execute("DELETE FROM sheet_rows WHERE sheet_id=?", (sheet_id,))
        self.conn.execute("DELETE FROM sheets WHERE id=?", (sheet_id,))
        self.conn.commit()

    def sheet_rows(self, sheet_id):
        """Rows in date order with a running balance (opening balance + in - out)."""
        sheet = self.conn.execute("SELECT * FROM sheets WHERE id=?", (sheet_id,)).fetchone()
        balance = num(sheet["opening_balance"]) if sheet else 0.0
        rows = [dict(r) for r in self.conn.execute(
            "SELECT * FROM sheet_rows WHERE sheet_id=? "
            "ORDER BY COALESCE(NULLIF(row_date,''),'9999'), id", (sheet_id,))]
        for r in rows:
            balance = round(balance + num(r.get("money_in")) - num(r.get("money_out")), 2)
            r["balance"] = balance
        return rows

    def add_sheet_row(self, sheet_id, data=None):
        data = {k: v for k, v in (data or {}).items() if k in self.SHEET_COLUMNS}
        data.setdefault("row_date", today_iso())
        cols = ["sheet_id", "created_at"] + list(data)
        cur = self.conn.execute(
            f"INSERT INTO sheet_rows ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [sheet_id, now_iso()] + list(data.values()))
        self.conn.commit()
        return cur.lastrowid

    def update_sheet_cell(self, row_id, column, value):
        if column not in self.SHEET_COLUMNS:
            raise ValueError(column)
        self.conn.execute(f"UPDATE sheet_rows SET {column}=? WHERE id=?", (value, row_id))
        self.conn.commit()

    def delete_sheet_rows(self, row_ids):
        self.conn.executemany("DELETE FROM sheet_rows WHERE id=?", [(i,) for i in row_ids])
        self.conn.commit()

    # ------------------------------------------------------------------ activity & settings
    def log(self, action, entity, entity_id, details, commit=True):
        self.conn.execute("INSERT INTO activity (ts, action, entity, entity_id, details) "
                          "VALUES (?,?,?,?,?)", (now_iso(), action, entity, entity_id, details))
        if commit:
            self.conn.commit()

    def activity(self, limit=2000):
        rows = self.conn.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def setting(self, key, default=None):
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is not None:
            return row["value"]
        if default is not None:
            return default
        if key in DEFAULT_SETTINGS:
            return DEFAULT_SETTINGS[key]
        if key.startswith("list_"):
            return json.dumps(DEFAULT_LISTS.get(key[5:], []))
        return ""

    def set_setting(self, key, value):
        self.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        self.conn.commit()

    def get_list(self, name) -> list:
        try:
            return list(json.loads(self.setting(f"list_{name}")))
        except (TypeError, ValueError):
            return list(DEFAULT_LISTS.get(name, []))

    def set_list(self, name, values):
        self.set_setting(f"list_{name}", json.dumps([v for v in values if v.strip()]))

    def all_faults(self) -> list:
        known = self.get_list("faults")
        seen = {f.lower() for f in known}
        for row in self.conn.execute("SELECT faults FROM devices WHERE faults IS NOT NULL"):
            for f in split_faults(row["faults"]):
                if f.lower() not in seen:
                    seen.add(f.lower())
                    known.append(f)
        return known

    # ------------------------------------------------------------------ dashboard
    def stats(self):
        c = self.conn
        aged = int(num(self.setting("aged_days"), 60))
        out = {}
        for cat in ("working", "faulty"):
            r = c.execute("""SELECT COALESCE(SUM(COALESCE(quantity,1)),0) n,
                                    COALESCE(SUM(COALESCE(quantity,1)*(COALESCE(purchase_price,0)+COALESCE(repair_cost,0))),0) cost,
                                    COALESCE(SUM(COALESCE(quantity,1)*COALESCE(list_price,0)),0) listv
                             FROM devices WHERE category=? AND COALESCE(status,'')!='Sold'""",
                          (cat,)).fetchone()
            out[cat] = dict(r)
        r = c.execute("""SELECT COUNT(*) lines, COALESCE(SUM(quantity),0) qty,
                                COALESCE(SUM(quantity*unit_cost),0) cost,
                                COALESCE(SUM(quantity*list_price),0) listv FROM parts""").fetchone()
        out["parts"] = dict(r)
        out["low_stock"] = [dict(x) for x in c.execute(
            "SELECT * FROM parts WHERE COALESCE(quantity,0) <= COALESCE(min_qty,0) "
            "AND COALESCE(min_qty,0) > 0 ORDER BY quantity LIMIT 50")]

        excl = ",".join(f"'{s}'" for s in EXCLUDED_SALE_STATUSES)
        valid = f"COALESCE(status,'') NOT IN ({excl})"
        today = dt.date.today()
        month_start = today.replace(day=1).isoformat()
        year_start = today.replace(month=1, day=1).isoformat()

        def sales_between(start, end=None):
            q = f"""SELECT COUNT(*) n, COALESCE(SUM(sale_price+COALESCE(postage_charged,0)),0) revenue,
                           COALESCE(SUM(profit),0) profit FROM sales WHERE {valid} AND sale_date >= ?"""
            params = [start]
            if end:
                q += " AND sale_date < ?"
                params.append(end)
            return dict(c.execute(q, params).fetchone())

        out["month"] = sales_between(month_start)
        out["year"] = sales_between(year_start)
        out["all"] = sales_between("0000-01-01")
        out["last30"] = sales_between((today - dt.timedelta(days=30)).isoformat())

        months = []
        y, m = today.year, today.month
        for _ in range(12):
            months.append((y, m))
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        months.reverse()
        series = []
        for y, m in months:
            start = dt.date(y, m, 1)
            end = dt.date(y + (m == 12), m % 12 + 1, 1)
            s = sales_between(start.isoformat(), end.isoformat())
            series.append({"label": start.strftime("%b %y"), **s})
        out["monthly"] = series

        out["brands"] = [dict(x) for x in c.execute(f"""
            SELECT COALESCE(NULLIF(brand,''),'(none)') brand, COUNT(*) n,
                   SUM(sale_price) revenue, SUM(profit) profit
            FROM sales WHERE {valid} GROUP BY 1 ORDER BY profit DESC LIMIT 10""")]

        # average days from purchase to sale for devices
        r = c.execute(f"""SELECT AVG(MAX(0, julianday(s.sale_date) - julianday(COALESCE(d.purchase_date, substr(d.created_at,1,10))))) days
                          FROM sales s JOIN devices d ON s.item_kind='device' AND d.id=s.item_id
                          WHERE {valid.replace('status', 's.status')}""").fetchone()
        out["avg_days_to_sell"] = r["days"]

        cutoff = (today - dt.timedelta(days=aged)).isoformat()
        out["aged"] = [dict(x) for x in c.execute("""
            SELECT * FROM devices WHERE COALESCE(status,'')!='Sold'
            AND COALESCE(purchase_date, substr(created_at,1,10)) <= ?
            ORDER BY COALESCE(purchase_date, created_at) LIMIT 50""", (cutoff,))]
        out["aged_days"] = aged
        return out

    def integrity_check(self) -> str:
        return self.conn.execute("PRAGMA integrity_check").fetchone()[0]

    def vacuum(self):
        self.conn.commit()
        self.conn.execute("VACUUM")


def _age_days(date_text, today):
    if not date_text:
        return None
    try:
        return (today - dt.date.fromisoformat(str(date_text)[:10])).days
    except ValueError:
        return None
