"""SQLite storage layer. No Qt imports here so it can be tested on its own."""
import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

from .checklist import summarise, summary_text
from .paths import is_android
from .fields import (DEFAULT_LISTS, DEVICE_FIELDS, EXCLUDED_SALE_STATUSES,
                     PART_FIELDS, SALE_FIELDS, SQL_TYPES, WRITTEN_OFF)

DEFAULT_SETTINGS = {
    "currency": "£",
    "fee_percent": "12.8",
    "fee_fixed": "0.30",
    "default_platform": "eBay",
    "aged_days": "60",
    "theme": "dark",
    "backup_dir": "",
    "backup_dir_secondary": "",
    "report_business": "",
    "tester_name": "",
    "auto_backup": "1",
    "auto_backup_hours": "24",
    "backup_on_exit": "1",
    "backup_keep": "30",
    "last_backup": "",
    # sync (these stay on each device; see SYNCED_SETTINGS for the shared ones)
    "sync_host_enabled": "0",
    "sync_code": "",
    "sync_peer": "",
    "sync_peer_code": "",
    "sync_auto": "0",          # 0 = manual only (default), 1 = automatic
    "sync_auto_minutes": "15",
    "last_sync": "",
    "sku_tag": "T" if is_android() else "",  # keeps tablet SKUs distinct from the PC's
}

SKU_PREFIX = {"working": "WP", "faulty": "FD", "parts": "PT"}

# Tables kept in step between devices by sync. Each gets a permanent "uid" (same on
# every device) and a "sync_ts" (UTC time of the last change), both maintained by
# triggers so every write path is covered; deletes are recorded in sync_deleted.
SYNC_TABLES = ("devices", "parts", "sales", "notes", "photos", "sheets", "sheet_rows",
               "device_tests", "device_test_meta", "settings", "activity", "joblots",
               "sell_lots")
SYNC_NOW = "strftime('%Y-%m-%dT%H:%M:%f', 'now')"
LEGACY_TS = "2000-01-01T00:00:00.000"

_EXTRA_COLUMNS = {
    "devices": [("category", "TEXT NOT NULL DEFAULT 'working'"), ("sold_at", "TEXT"),
                ("joblot_uid", "TEXT"), ("joblot_units", "INTEGER"),
                ("sell_lot_uid", "TEXT"), ("sell_lot_qty", "INTEGER")],
    "parts": [("joblot_uid", "TEXT"), ("joblot_units", "INTEGER"),
              ("sell_lot_uid", "TEXT"), ("sell_lot_qty", "INTEGER")],
    "sales": [("item_kind", "TEXT"), ("item_id", "INTEGER"), ("sell_lot_uid", "TEXT")],
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
            CREATE TABLE IF NOT EXISTS joblots (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, purchase_date TEXT,
                source TEXT, total_cost REAL DEFAULT 0, split TEXT DEFAULT 'even', notes TEXT,
                created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS sell_lots (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                status TEXT DEFAULT 'Draft', platform TEXT, list_price REAL DEFAULT 0,
                postage_charged REAL DEFAULT 0, postage_cost REAL DEFAULT 0,
                other_costs REAL DEFAULT 0, notes TEXT, listed_at TEXT,
                sold_price REAL, sold_fees REAL, sold_date TEXT, sale_info TEXT,
                fee_percent REAL, fee_fixed REAL, promo_percent REAL,
                created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS device_tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL, test_key TEXT NOT NULL,
                result TEXT, note TEXT, updated_at TEXT,
                UNIQUE(device_id, test_key));
            CREATE TABLE IF NOT EXISTS device_test_meta (
                device_id INTEGER PRIMARY KEY, tested_at TEXT, tested_by TEXT, notes TEXT);
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
        have = {r["name"] for r in c.execute("PRAGMA table_info(sell_lots)")}
        for col in ("fee_percent", "fee_fixed", "promo_percent"):   # added in 1.5.1
            if col not in have:
                c.execute(f"ALTER TABLE sell_lots ADD COLUMN {col} REAL")
        self._sync_schema()
        self._listing_schema()
        self._repaired_sku_migration()
        c.commit()
        self._columns = {}

    def _repaired_sku_migration(self):
        """One-off: phones already moved between sections get the SKU they'd get now."""
        c = self.conn
        if c.execute("SELECT 1 FROM settings WHERE key='migrated_repaired_sku'").fetchone():
            return
        for row in c.execute("SELECT id, sku, category FROM devices WHERE "
                             "(category='working' AND sku LIKE 'FD-%') OR "
                             "(category='faulty' AND sku LIKE 'WP-%')").fetchall():
            new = sku_for_section(row["sku"], row["category"])
            if new != row["sku"]:
                c.execute("UPDATE devices SET sku=? WHERE id=?", (new, row["id"]))
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('migrated_repaired_sku', ?)",
                  (now_iso(),))

    def _listing_schema(self):
        """Record the date an item is first listed, whichever way its status changes."""
        c = self.conn
        for t in ("devices", "parts"):
            c.executescript(f"""
                CREATE TRIGGER IF NOT EXISTS listed_{t}_upd AFTER UPDATE OF status ON {t}
                WHEN NEW.status = 'Listed' AND COALESCE(NEW.listed_at, '') = '' BEGIN
                    UPDATE {t} SET listed_at = date('now', 'localtime') WHERE rowid = NEW.rowid;
                END;
                CREATE TRIGGER IF NOT EXISTS listed_{t}_ins AFTER INSERT ON {t}
                WHEN NEW.status = 'Listed' AND COALESCE(NEW.listed_at, '') = '' BEGIN
                    UPDATE {t} SET listed_at = date('now', 'localtime') WHERE rowid = NEW.rowid;
                END;
            """)
        # one-off for items from before listing dates were kept: use the first
        # "Set Status -> Listed" entry in the activity log where there is one
        done = c.execute("SELECT value FROM settings WHERE key='migrated_listed_at'").fetchone()
        if done is None:
            for t, entity in (("devices", "device"), ("parts", "part")):
                c.execute(f"""
                    UPDATE {t} SET listed_at = (
                        SELECT substr(min(a.ts), 1, 10) FROM activity a
                        WHERE a.entity = '{entity}' AND a.entity_id = {t}.id
                          AND a.action = 'Status' AND a.details LIKE '%-> Listed')
                    WHERE COALESCE(listed_at, '') = '' AND EXISTS (
                        SELECT 1 FROM activity a WHERE a.entity = '{entity}'
                          AND a.entity_id = {t}.id AND a.action = 'Status'
                          AND a.details LIKE '%-> Listed')""")
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('migrated_listed_at', ?)",
                      (now_iso(),))

    def _sync_schema(self):
        c = self.conn
        c.execute("CREATE TABLE IF NOT EXISTS sync_deleted (tbl TEXT NOT NULL, uid TEXT NOT NULL, "
                  "deleted_at TEXT NOT NULL, PRIMARY KEY (tbl, uid))")
        for t in SYNC_TABLES:
            cols = {r["name"] for r in c.execute(f"PRAGMA table_info({t})")}
            for name in ("uid", "sync_ts"):
                if name not in cols:
                    c.execute(f"ALTER TABLE {t} ADD COLUMN {name} TEXT")
            # settings are identified by name on every device; everything else gets a
            # random uid (rows from before sync existed included)
            new_uid = "'S-' || NEW.key" if t == "settings" else "lower(hex(randomblob(16)))"
            c.execute(f"UPDATE {t} SET uid = {new_uid.replace('NEW.', '')} WHERE uid IS NULL")
            c.execute(f"UPDATE {t} SET sync_ts = '{LEGACY_TS}' WHERE sync_ts IS NULL")
            c.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{t}_uid ON {t}(uid)")
            changed = "NEW.sync_ts IS OLD.sync_ts"
            if t == "settings":  # saving Settings re-writes every value; only real changes count
                changed += " AND NEW.value IS NOT OLD.value"
            c.executescript(f"""
                CREATE TRIGGER IF NOT EXISTS sync_{t}_ins AFTER INSERT ON {t}
                WHEN NEW.uid IS NULL OR NEW.sync_ts IS NULL BEGIN
                    UPDATE {t} SET uid = COALESCE(NEW.uid, {new_uid}),
                                   sync_ts = COALESCE(NEW.sync_ts, {SYNC_NOW})
                    WHERE rowid = NEW.rowid;
                END;
                CREATE TRIGGER IF NOT EXISTS sync_{t}_upd AFTER UPDATE ON {t}
                WHEN {changed} BEGIN
                    UPDATE {t} SET sync_ts = {SYNC_NOW} WHERE rowid = NEW.rowid;
                END;
                CREATE TRIGGER IF NOT EXISTS sync_{t}_del AFTER DELETE ON {t}
                WHEN OLD.uid IS NOT NULL BEGIN
                    INSERT OR REPLACE INTO sync_deleted (tbl, uid, deleted_at)
                    VALUES ('{t}', OLD.uid, {SYNC_NOW});
                END;
            """)

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
            tag = self.setting("sku_tag")
            self.conn.execute(f"UPDATE {table} SET sku=? WHERE id=?",
                              (f"{prefix}-{tag}{new_id:05d}", new_id))
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
        if table == "devices":
            self.conn.execute("DELETE FROM device_tests WHERE device_id=?", (row_id,))
            self.conn.execute("DELETE FROM device_test_meta WHERE device_id=?", (row_id,))
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
        lots = self.joblot_pl_map()
        selling = self.sell_lot_names()
        tested = {}
        for t in self.conn.execute("SELECT device_id, test_key, result FROM device_tests"):
            tested.setdefault(t["device_id"], {})[t["test_key"]] = {"result": t["result"]}
        for r in rows:
            counts = summarise(tested.get(r["id"], {}))
            r["test_fail"] = counts["fail"]
            r["test_summary"] = summary_text(counts)
            r["total_cost"] = round(num(r.get("purchase_price")) + num(r.get("repair_cost")), 2)
            r["quantity"] = int(num(r.get("quantity"), 1)) if r.get("quantity") is not None else 1
            r["age_days"] = _age_days(r.get("purchase_date") or r.get("created_at"), today)
            r["est_profit"] = round(num(r.get("list_price")) - r["total_cost"], 2) \
                if r.get("list_price") else None
            r["written_off"] = r.get("status") == WRITTEN_OFF
            r["days_listed"] = _age_days(r.get("listed_at"), today) if r.get("listed_at") else None
            lot = lots.get(("devices", r["id"]))
            r["joblot_name"], r["lot_pl"] = (lot[0], lot[1]) if lot else ("", None)
            r["sell_lot_name"] = selling.get(r.get("sell_lot_uid"), "")
        return rows

    def list_parts(self):
        rows = self._with_counts("parts", "part")
        lots = self.joblot_pl_map()
        selling = self.sell_lot_names()
        for r in rows:
            r["sell_lot_name"] = selling.get(r.get("sell_lot_uid"), "")
            qty = int(num(r.get("quantity")))
            r["stock_value"] = round(qty * num(r.get("unit_cost")), 2)
            r["written_off"] = r.get("status") == WRITTEN_OFF
            r["low_stock"] = not r["written_off"] and qty <= int(num(r.get("min_qty"), 0))
            lot = lots.get(("parts", r["id"]))
            r["joblot_name"], r["lot_pl"] = (lot[0], lot[1]) if lot else ("", None)
        return rows

    def list_sales(self):
        rows = self._with_counts("sales", "sale")
        rows.sort(key=lambda r: (str(r.get("sale_date") or ""), r["id"]))
        started = {d["id"]: dict(d) for d in self.conn.execute(
            "SELECT id, listed_at, purchase_date, created_at FROM devices")}
        running = 0.0
        for r in rows:
            r["days_to_sell"], r["days_basis"] = None, ""
            dev = started.get(r.get("item_id")) if r.get("item_kind") == "device" else None
            if dev and r.get("sale_date"):
                start, basis = listing_start(dev)
                days = _days_between(start, r["sale_date"])
                if days is not None:
                    r["days_to_sell"], r["days_basis"] = days, basis
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

    def add_photo(self, entity, entity_id, src_path, caption=None):
        import shutil
        import uuid
        from .paths import photos_dir
        src = Path(src_path)
        caption = caption or src.name
        suffix = Path(caption).suffix.lower() or src.suffix.lower()
        name = f"{entity}-{entity_id}-{uuid.uuid4().hex[:8]}{suffix}"
        shutil.copy2(src, photos_dir() / name)
        self.conn.execute("INSERT INTO photos (entity, entity_id, filename, caption, created_at) "
                          "VALUES (?,?,?,?,?)", (entity, entity_id, name, caption, now_iso()))
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
        with self.conn:
            return self._record_sale_nocommit(kind, item_id, sale)

    def _record_sale_nocommit(self, kind, item_id, sale):
        sale = dict(sale)
        sale["item_kind"] = kind
        sale["item_id"] = item_id
        sale["profit"] = calc_profit(sale)
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

    def save_adjustment(self, data, sale_id=None):
        """Past sales entered as one lump: counts in every profit / revenue total."""
        revenue, profit = round(num(data.get("revenue")), 2), round(num(data.get("profit")), 2)
        row = {"item_kind": "adjustment", "item_id": None, "sku": "ADJ",
               "title": (data.get("title") or "Past sales").strip(),
               "sale_date": data.get("sale_date") or "", "quantity": int(num(data.get("quantity"))),
               "sale_price": revenue, "postage_charged": 0, "fees": 0, "postage_cost": 0,
               "other_costs": 0, "cost_basis": round(revenue - profit, 2), "status": "Completed",
               "platform": data.get("platform") or "", "brand": data.get("brand") or ""}
        row["profit"] = calc_profit(row)
        if sale_id:
            self.update("sales", sale_id, row)
            self.log("Edited", "sale", sale_id, f"Adjustment: {row['title']} ({row['profit']:.2f})")
            return sale_id
        sale_id = self.insert("sales", row)
        self.log("Adjustment", "sale", sale_id, f"{row['title']}: {row['quantity']} sold, "
                                                 f"profit {row['profit']:.2f}")
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

    def write_off(self, table, row_id, reason=""):
        """Mark a device or part as written off, keeping it on record with a note."""
        entity = "device" if table == "devices" else "part"
        item = self.get(table, row_id)
        with self.conn:
            self.update(table, row_id, {"status": WRITTEN_OFF}, commit=False)
            self.add_note(entity, row_id, "Written off" + (f": {reason}" if reason else "."),
                          commit=False)
            self.log(WRITTEN_OFF, entity, row_id,
                     f"{item.get('sku')}" + (f" - {reason}" if reason else ""), commit=False)

    def move_device(self, device_id, category):
        device = self.get("devices", device_id)
        status = device.get("status")
        if category == "working" and status in ("Awaiting Repair", "In Repair"):
            status = "In Stock"
        if category == "faulty" and status in ("In Stock", "Listed"):
            status = "Awaiting Repair"
        old_sku = device.get("sku") or ""
        new_sku = sku_for_section(old_sku, category)
        with self.conn:
            self.update("devices", device_id, {"category": category, "status": status,
                                               "sku": new_sku}, commit=False)
            label = "Working Phones" if category == "working" else "Faulty / Repairs"
            change = f" SKU {old_sku} -> {new_sku}." if new_sku != old_sku else ""
            self.add_note("device", device_id, f"Moved to {label}.{change}", commit=False)
            self.log("Moved", "device", device_id, f"{old_sku} -> {label}{change}", commit=False)

    # ------------------------------------------------------------------ job lots
    def joblots(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM joblots ORDER BY COALESCE(purchase_date, created_at) DESC, id DESC")]

    def joblot(self, lot_id):
        row = self.conn.execute("SELECT * FROM joblots WHERE id=?", (lot_id,)).fetchone()
        return dict(row) if row else None

    def save_joblot(self, data, lot_id=None):
        data = {k: data.get(k) for k in ("name", "purchase_date", "source", "total_cost",
                                          "split", "notes") if k in data}
        if lot_id:
            self.update("joblots", lot_id, data)
        else:
            data.setdefault("split", "even")
            lot_id = self.insert("joblots", data)
            self.log("Job lot", "joblot", lot_id,
                     f"Created {data.get('name')} ({num(data.get('total_cost')):.2f})")
        self.reallocate_joblot(lot_id)
        return lot_id

    def delete_joblot(self, lot_id):
        lot = self.joblot(lot_id)
        if not lot:
            return
        with self.conn:
            for t in ("devices", "parts"):
                self.conn.execute(f"UPDATE {t} SET joblot_uid=NULL, joblot_units=NULL "
                                  "WHERE joblot_uid=?", (lot["uid"],))
            self.conn.execute("DELETE FROM joblots WHERE id=?", (lot_id,))
            self.log("Job lot", "joblot", lot_id, f"Deleted {lot['name']} (items kept)",
                     commit=False)

    def add_to_joblot(self, lot_id, items):
        """items: [(table, id)] with table 'devices' or 'parts'."""
        lot = self.joblot(lot_id)
        with self.conn:
            for table, item_id in items:
                item = self.get(table, item_id)
                units = max(1, int(num(item.get("quantity"), 1)))
                self.update(table, item_id, {"joblot_uid": lot["uid"], "joblot_units": units},
                            commit=False)
                self.add_note("device" if table == "devices" else "part", item_id,
                              f"Added to job lot: {lot['name']}", commit=False)
            self.log("Job lot", "joblot", lot_id, f"Added {len(items)} item(s) to {lot['name']}",
                     commit=False)
        self.reallocate_joblot(lot_id)

    def remove_from_joblot(self, table, item_id):
        item = self.get(table, item_id)
        lot = self.conn.execute("SELECT id FROM joblots WHERE uid=?",
                                (item.get("joblot_uid"),)).fetchone()
        self.update(table, item_id, {"joblot_uid": None, "joblot_units": None})
        if lot:
            self.reallocate_joblot(lot["id"])

    def _lot_rows(self, lot_uid):
        rows = []
        for table in ("devices", "parts"):
            for r in self.conn.execute(f"SELECT * FROM {table} WHERE joblot_uid=? ORDER BY id",
                                       (lot_uid,)):
                rows.append({"table": table, **dict(r)})
        return rows

    def reallocate_joblot(self, lot_id):
        """Share the lot price across its items and re-cost any sales of them."""
        lot = self.joblot(lot_id)
        if not lot or lot.get("split") == "manual":
            return
        rows = self._lot_rows(lot["uid"])
        if not rows:
            return
        units = [max(1, int(num(r.get("joblot_units"), 1))) for r in rows]
        if lot.get("split") == "list":
            prices = [num(r.get("list_price")) for r in rows]
            known = [p for p in prices if p > 0]
            fill = sum(known) / len(known) if known else 1.0
            weights = [p if p > 0 else fill for p in prices]
        else:
            weights = [1.0] * len(rows)
        denom = sum(w * u for w, u in zip(weights, units)) or 1.0
        total = num(lot.get("total_cost"))
        with self.conn:
            for r, w in zip(rows, weights):
                unit_cost = round(total * w / denom, 2)
                col = "purchase_price" if r["table"] == "devices" else "unit_cost"
                if round(num(r.get(col)), 2) != unit_cost:
                    self.update(r["table"], r["id"], {col: unit_cost}, commit=False)
                repair = num(r.get("repair_cost")) if r["table"] == "devices" else 0.0
                kind = "device" if r["table"] == "devices" else "part"
                for s in self.conn.execute("SELECT * FROM sales WHERE item_kind=? AND item_id=?",
                                           (kind, r["id"])).fetchall():
                    s = dict(s)
                    s["cost_basis"] = round((unit_cost + repair) * max(1, int(num(s.get("quantity"), 1))), 2)
                    self.update("sales", s["id"], {"cost_basis": s["cost_basis"],
                                                   "profit": calc_profit(s)}, commit=False)

    def joblot_report(self, lot_id):
        """Every item in a lot with its profit / loss, plus the lot totals."""
        lot = self.joblot(lot_id)
        fee_pct, fee_fixed = num(self.setting("fee_percent")), num(self.setting("fee_fixed"))
        items, tot = [], {"paid": num(lot.get("total_cost")), "repairs": 0.0, "net_sales": 0.0,
                          "units": 0, "sold_units": 0, "remaining_value": 0.0}
        excl = tuple(EXCLUDED_SALE_STATUSES)
        for r in self._lot_rows(lot["uid"]):
            kind = "device" if r["table"] == "devices" else "part"
            units = max(1, int(num(r.get("joblot_units"), 1)))
            unit_cost = num(r.get("purchase_price" if kind == "device" else "unit_cost"))
            repair = num(r.get("repair_cost")) if kind == "device" else 0.0
            sold_units, net = 0, 0.0
            for s in self.conn.execute(
                    f"SELECT * FROM sales WHERE item_kind=? AND item_id=? AND COALESCE(status,'') "
                    f"NOT IN ({','.join('?' for _ in excl)})", (kind, r["id"], *excl)):
                sold_units += max(1, int(num(s["quantity"], 1)))
                net += (num(s["sale_price"]) + num(s["postage_charged"]) - num(s["fees"])
                        - num(s["postage_cost"]) - num(s["other_costs"]))
            sold_units = min(sold_units, units)
            remaining = 0 if r.get("status") in ("Sold", WRITTEN_OFF) else units - sold_units
            cost = (unit_cost + repair) * units
            list_price = num(r.get("list_price"))
            est = 0.0
            if remaining and list_price:
                est = remaining * (list_price - list_price * fee_pct / 100 - fee_fixed)
            pl = round(net + est - cost, 2)
            if kind == "device":
                title, where = device_title(r), ("Sold" if r.get("status") == "Sold" else
                                                 "Working" if r.get("category") == "working" else "Faulty")
            else:
                title, where = part_title(r), "Parts"
            if r.get("status") == WRITTEN_OFF:
                where = "Written off"
            items.append({"table": r["table"], "id": r["id"], "sku": r.get("sku"), "title": title,
                          "where": where, "units": units, "unit_cost": unit_cost, "repairs": repair,
                          "cost": round(cost, 2), "sold_units": sold_units, "net_sales": round(net, 2),
                          "remaining": remaining, "pl": pl, "estimated": bool(remaining and list_price),
                          "unpriced": bool(remaining and not list_price)})
            tot["repairs"] += repair * units
            tot["net_sales"] += net
            tot["units"] += units
            tot["sold_units"] += sold_units
            tot["remaining_value"] += est
        tot["pl_so_far"] = round(tot["net_sales"] - tot["paid"] - tot["repairs"], 2)
        tot["pl_potential"] = round(tot["pl_so_far"] + tot["remaining_value"], 2)
        tot["allocated"] = round(sum(i["unit_cost"] * i["units"] for i in items), 2)
        return {"lot": lot, "items": items, "totals": tot}

    def joblot_pl_map(self):
        """{(table, id): (lot name, P/L, estimated)} for the stock tabs."""
        out = {}
        for lot in self.joblots():
            rep = self.joblot_report(lot["id"])
            for i in rep["items"]:
                out[(i["table"], i["id"])] = (lot["name"], i["pl"], i["estimated"])
        return out

    # ------------------------------------------------------------------ job lots to sell
    # Your own job lots: stock picked out to sell together as one listing.
    SELL_LOT_FIELDS = ("name", "status", "platform", "list_price", "postage_charged",
                       "postage_cost", "other_costs", "notes", "fee_percent", "fee_fixed",
                       "promo_percent")

    def sell_lots(self, include_sold=True):
        where = "" if include_sold else "WHERE COALESCE(status,'') != 'Sold'"
        return [dict(r) for r in self.conn.execute(
            f"SELECT * FROM sell_lots {where} ORDER BY (status='Sold'), id DESC")]

    def sell_lot(self, lot_id):
        row = self.conn.execute("SELECT * FROM sell_lots WHERE id=?", (lot_id,)).fetchone()
        return dict(row) if row else None

    def sell_lot_names(self):
        return {r["uid"]: r["name"] for r in self.conn.execute(
            "SELECT uid, name FROM sell_lots WHERE COALESCE(status,'') != 'Sold'")}

    def save_sell_lot(self, data, lot_id=None):
        data = {k: data[k] for k in self.SELL_LOT_FIELDS if k in data}
        if lot_id:
            old = self.sell_lot(lot_id)
            if data.get("status") == "Listed" and old.get("status") != "Listed":
                data["listed_at"] = old.get("listed_at") or now_iso()
            with self.conn:
                self.update("sell_lots", lot_id, data, commit=False)
                if data.get("status") == "Listed" and old.get("status") != "Listed":
                    self._set_lot_items_status(old["uid"], "Listed")
        else:
            data.setdefault("status", "Draft")
            if data["status"] == "Listed":
                data["listed_at"] = now_iso()
            lot_id = self.insert("sell_lots", data)
            self.log("Selling lot", "sell_lot", lot_id, f"Created {data.get('name')}")
        return lot_id

    def _set_lot_items_status(self, lot_uid, status):
        for r in self._sell_lot_rows(lot_uid):
            if r.get("status") in ("In Stock", "Awaiting Repair", "Reserved", None, ""):
                self.update(r["table"], r["id"], {"status": status}, commit=False)

    def delete_sell_lot(self, lot_id):
        lot = self.sell_lot(lot_id)
        if not lot:
            return
        with self.conn:
            for t in ("devices", "parts"):
                for r in self.conn.execute(f"SELECT id FROM {t} WHERE sell_lot_uid=? AND "
                                           "COALESCE(status,'') != 'Sold'", (lot["uid"],)).fetchall():
                    item = self.get(t, r["id"])
                    data = {"sell_lot_uid": None, "sell_lot_qty": None}
                    if item.get("status") == "Listed":
                        data["status"] = ("Awaiting Repair" if item.get("category") == "faulty"
                                          else "In Stock")
                    self.update(t, r["id"], data, commit=False)
            self.conn.execute("DELETE FROM sell_lots WHERE id=?", (lot_id,))
            self.log("Selling lot", "sell_lot", lot_id, f"Deleted {lot['name']} (items kept)",
                     commit=False)

    def add_to_sell_lot(self, lot_id, items):
        """items: [(table, id)] or [(table, id, qty)]; a part can go in with part of its stock."""
        lot = self.sell_lot(lot_id)
        with self.conn:
            for it in items:
                table, item_id = it[0], it[1]
                item = self.get(table, item_id)
                stock = max(1, int(num(item.get("quantity"), 1)))
                qty = stock if len(it) < 3 or not it[2] else max(1, min(stock, int(it[2])))
                self.update(table, item_id, {"sell_lot_uid": lot["uid"], "sell_lot_qty": qty},
                            commit=False)
                self.add_note("device" if table == "devices" else "part", item_id,
                              f"Added to selling job lot: {lot['name']}", commit=False)
            if lot.get("status") == "Listed":
                self._set_lot_items_status(lot["uid"], "Listed")
            self.log("Selling lot", "sell_lot", lot_id,
                     f"Added {len(items)} item(s) to {lot['name']}", commit=False)

    def remove_from_sell_lot(self, table, item_id):
        item = self.get(table, item_id)
        data = {"sell_lot_uid": None, "sell_lot_qty": None}
        if item.get("status") == "Listed":   # it was listed as part of the lot
            data["status"] = "Awaiting Repair" if item.get("category") == "faulty" else "In Stock"
        self.update(table, item_id, data)

    def _sell_lot_rows(self, lot_uid):
        rows = []
        for table in ("devices", "parts"):
            for r in self.conn.execute(f"SELECT * FROM {table} WHERE sell_lot_uid=? ORDER BY id",
                                       (lot_uid,)):
                rows.append({"table": table, **dict(r)})
        return rows

    def sell_lot_fees(self, lot):
        """(fee %, fixed fee, promoted %) for a lot: its own rates, else your settings."""
        def pick(key, default):
            v = lot.get(key) if lot else None
            return num(default) if v is None or v == "" else num(v)
        return (pick("fee_percent", self.setting("fee_percent")),
                pick("fee_fixed", self.setting("fee_fixed")), pick("promo_percent", 0))

    def sell_lot_report(self, lot_id, price=None, postage_charged=None, postage_cost=None,
                        other_costs=None, fees=None, rates=None):
        """Costs, fees and estimated profit for the lot and each item in it.

        The lot price (and fees / postage) is shared over the items by their own list
        price (unpriced ones count as the average), else by their cost, else evenly."""
        lot = self.sell_lot(lot_id)
        sold = lot.get("status") == "Sold"
        if sold:
            return self._sold_lot_report(lot)
        fee_pct, fee_fixed, promo = rates or self.sell_lot_fees(lot)
        price = num(lot.get("list_price")) if price is None else num(price)
        pp = num(lot.get("postage_charged")) if postage_charged is None else num(postage_charged)
        post = num(lot.get("postage_cost")) if postage_cost is None else num(postage_cost)
        other = num(lot.get("other_costs")) if other_costs is None else num(other_costs)
        gross = price + pp
        if fees is None:
            fees = gross * (fee_pct + promo) / 100 + (fee_fixed if gross > 0 else 0.0)
        items, total_cost = [], 0.0
        for r in self._sell_lot_rows(lot["uid"]):
            dev = r["table"] == "devices"
            qty = max(1, int(num(r.get("sell_lot_qty"), 1)))
            unit = (num(r.get("purchase_price")) + num(r.get("repair_cost"))) if dev \
                else num(r.get("unit_cost"))
            cost = round(unit * qty, 2)
            total_cost += cost
            items.append({"table": r["table"], "id": r["id"], "sku": r.get("sku"),
                          "title": device_title(r) if dev else part_title(r),
                          "where": ("Working" if r.get("category") == "working" else "Faulty")
                          if dev else "Parts", "qty": qty, "cost": cost,
                          "list_price": num(r.get("list_price")) * qty, "status": r.get("status")})
        self._share(items, price, pp, post, other, fees)
        indiv = None
        if items and all(i["list_price"] > 0 for i in items):
            indiv = round(sum(i["list_price"] - i["list_price"] * (fee_pct + promo) / 100 - fee_fixed
                              - i["cost"] for i in items), 2)
        pct = (fee_pct + promo) / 100
        costs = total_cost + post + other
        break_even = (costs + fee_fixed) / (1 - pct) - pp if pct < 1 else None
        profit = round(gross - fees - costs, 2)
        return {"lot": lot, "items": items, "sold": False, "totals": {
            "price": price, "postage_charged": pp, "postage_cost": post, "other_costs": other,
            "gross": gross, "fees": round(fees, 2), "cost": round(total_cost, 2),
            "profit": profit, "margin": profit / gross * 100 if gross else None,
            "break_even": round(break_even, 2) if break_even is not None else None,
            "individual": indiv, "count": len(items),
            "fee_percent": fee_pct, "fee_fixed": fee_fixed, "promo_percent": promo,
            "units": sum(i["qty"] for i in items)}}

    @staticmethod
    def _share(items, price, pp, post, other, fees):
        """Give each item its part of the lot money; the last item takes the rounding."""
        if not items:
            return
        known = [i["list_price"] for i in items if i["list_price"] > 0]
        if known:
            fill = sum(known) / len(known)
            weights = [i["list_price"] if i["list_price"] > 0 else fill for i in items]
        elif sum(i["cost"] for i in items) > 0:
            weights = [i["cost"] for i in items]
        else:
            weights = [i["qty"] for i in items]
        denom = sum(weights) or 1.0
        for key, total in (("price", price), ("postage_charged", pp), ("postage_cost", post),
                           ("other_costs", other), ("fees", fees)):
            left = round(total, 2)
            for n, (i, w) in enumerate(zip(items, weights)):
                share = left if n == len(items) - 1 else round(total * w / denom, 2)
                i[key] = share
                left = round(left - share, 2)
        for i in items:
            i["profit"] = round(i["price"] + i["postage_charged"] - i["fees"] - i["postage_cost"]
                                - i["other_costs"] - i["cost"], 2)

    def _sold_lot_report(self, lot):
        items = []
        for s in self.conn.execute("SELECT * FROM sales WHERE sell_lot_uid=? ORDER BY id",
                                   (lot["uid"],)):
            s = dict(s)
            items.append({"table": "devices" if s["item_kind"] == "device" else "parts",
                          "id": s["item_id"], "sale_id": s["id"], "sku": s.get("sku"),
                          "title": s.get("title"), "where": "Sold",
                          "qty": int(num(s.get("quantity"), 1)), "cost": num(s.get("cost_basis")),
                          "list_price": 0.0, "price": num(s.get("sale_price")),
                          "postage_charged": num(s.get("postage_charged")),
                          "postage_cost": num(s.get("postage_cost")),
                          "other_costs": num(s.get("other_costs")), "fees": num(s.get("fees")),
                          "profit": num(s.get("profit")), "status": s.get("status")})
        t = {k: round(sum(i[k] for i in items), 2) for k in
             ("price", "postage_charged", "postage_cost", "other_costs", "fees", "cost", "profit")}
        t["gross"] = round(t["price"] + t["postage_charged"], 2)
        t.update({"margin": t["profit"] / t["gross"] * 100 if t["gross"] else None,
                  "break_even": None, "individual": None, "count": len(items),
                  "units": sum(i["qty"] for i in items)})
        return {"lot": lot, "items": items, "sold": True, "totals": t}

    def sell_lot_sold(self, lot_id, sale):
        """Record the lot as sold: one sale per item, with the money shared out."""
        rep = self.sell_lot_report(lot_id, price=sale.get("sale_price"),
                                   postage_charged=sale.get("postage_charged"),
                                   postage_cost=sale.get("postage_cost"),
                                   other_costs=sale.get("other_costs"), fees=sale.get("fees"))
        lot = rep["lot"]
        if rep["sold"] or not rep["items"]:
            return []
        ids = []
        with self.conn:
            for i in rep["items"]:
                kind = "device" if i["table"] == "devices" else "part"
                row = {"sku": i["sku"], "title": f"{i['title']} [job lot: {lot['name']}]",
                       "sale_date": sale.get("sale_date") or today_iso(),
                       "platform": sale.get("platform") or lot.get("platform"),
                       "order_number": sale.get("order_number"), "buyer": sale.get("buyer"),
                       "status": sale.get("status") or "Completed", "quantity": i["qty"],
                       "sale_price": i["price"], "postage_charged": i["postage_charged"],
                       "postage_cost": i["postage_cost"], "other_costs": i["other_costs"],
                       "fees": i["fees"], "cost_basis": i["cost"], "sell_lot_uid": lot["uid"]}
                if kind == "device":
                    d = self.get("devices", i["id"])
                    row.update({"brand": d.get("brand"), "model": d.get("model"),
                                "imei": d.get("imei"), "serial": d.get("serial")})
                row = {k: v for k, v in row.items() if k in self.columns("sales")}
                ids.append(self._record_sale_nocommit(kind, i["id"], row))
                if kind == "part":   # any stock left over is no longer in the lot
                    self.update("parts", i["id"], {"sell_lot_uid": None, "sell_lot_qty": None},
                                commit=False)
            self.update("sell_lots", lot_id, {
                "status": "Sold", "sold_price": round(num(sale.get("sale_price")), 2),
                "sold_fees": rep["totals"]["fees"], "sold_date": sale.get("sale_date") or today_iso(),
                "sale_info": json.dumps({k: sale.get(k) for k in ("platform", "order_number", "buyer")})},
                commit=False)
            self.log("Selling lot", "sell_lot", lot_id,
                     f"Sold {lot['name']} ({len(ids)} items) for "
                     f"{num(sale.get('sale_price')):.2f} (profit {rep['totals']['profit']:.2f})",
                     commit=False)
        return ids

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

    # ------------------------------------------------------------------ device tests
    def device_tests(self, device_id) -> dict:
        rows = self.conn.execute("SELECT test_key, result, note FROM device_tests "
                                 "WHERE device_id=?", (device_id,))
        return {r["test_key"]: {"result": r["result"] or "", "note": r["note"] or ""}
                for r in rows}

    def set_test(self, device_id, key, result=None, note=None):
        """Save one test. None leaves that part as it was; an empty row is removed."""
        current = self.device_tests(device_id).get(key, {"result": "", "note": ""})
        result = current["result"] if result is None else result
        note = current["note"] if note is None else note.strip()
        if not result and not note and not key.startswith("custom:"):
            self.conn.execute("DELETE FROM device_tests WHERE device_id=? AND test_key=?",
                              (device_id, key))
        else:
            self.conn.execute(
                "INSERT INTO device_tests (device_id, test_key, result, note, updated_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(device_id, test_key) DO UPDATE SET "
                "result=excluded.result, note=excluded.note, updated_at=excluded.updated_at",
                (device_id, key, result, note, now_iso()))
        self.conn.commit()

    def delete_test(self, device_id, key):
        self.conn.execute("DELETE FROM device_tests WHERE device_id=? AND test_key=?",
                          (device_id, key))
        self.conn.commit()

    def clear_tests(self, device_id):
        """Reset every result but keep the device's custom tests."""
        self.conn.execute("DELETE FROM device_tests WHERE device_id=? AND test_key NOT LIKE 'custom:%'",
                          (device_id,))
        self.conn.execute("UPDATE device_tests SET result='', note='' WHERE device_id=?",
                          (device_id,))
        self.conn.commit()

    def test_meta(self, device_id) -> dict:
        row = self.conn.execute("SELECT * FROM device_test_meta WHERE device_id=?",
                                (device_id,)).fetchone()
        return dict(row) if row else {}

    def set_test_meta(self, device_id, tested_by, tested_at, notes):
        self.conn.execute(
            "INSERT INTO device_test_meta (device_id, tested_at, tested_by, notes) VALUES (?,?,?,?) "
            "ON CONFLICT(device_id) DO UPDATE SET tested_at=excluded.tested_at, "
            "tested_by=excluded.tested_by, notes=excluded.notes",
            (device_id, tested_at, tested_by, notes))
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
                             FROM devices WHERE category=? AND COALESCE(status,'') NOT IN ('Sold', ?)""",
                          (cat, WRITTEN_OFF)).fetchone()
            out[cat] = dict(r)
        r = c.execute("""SELECT COUNT(*) lines, COALESCE(SUM(quantity),0) qty,
                                COALESCE(SUM(quantity*unit_cost),0) cost,
                                COALESCE(SUM(quantity*list_price),0) listv FROM parts
                         WHERE COALESCE(status,'') != ?""", (WRITTEN_OFF,)).fetchone()
        out["parts"] = dict(r)
        out["low_stock"] = [dict(x) for x in c.execute(
            "SELECT * FROM parts WHERE COALESCE(quantity,0) <= COALESCE(min_qty,0) "
            "AND COALESCE(min_qty,0) > 0 AND COALESCE(status,'') != ? ORDER BY quantity LIMIT 50",
            (WRITTEN_OFF,))]
        d = c.execute("""SELECT COALESCE(SUM(COALESCE(quantity,1)),0) n,
                                COALESCE(SUM(COALESCE(quantity,1)*(COALESCE(purchase_price,0)+COALESCE(repair_cost,0))),0) cost
                         FROM devices WHERE status=?""", (WRITTEN_OFF,)).fetchone()
        p = c.execute("""SELECT COALESCE(SUM(quantity),0) n, COALESCE(SUM(quantity*unit_cost),0) cost
                         FROM parts WHERE status=?""", (WRITTEN_OFF,)).fetchone()
        out["written_off"] = {"devices": d["n"], "parts": p["n"],
                              "cost": round(d["cost"] + p["cost"], 2)}

        excl = ",".join(f"'{s}'" for s in EXCLUDED_SALE_STATUSES)
        valid = f"COALESCE(status,'') NOT IN ({excl})"
        today = dt.date.today()
        month_start = today.replace(day=1).isoformat()
        year_start = today.replace(month=1, day=1).isoformat()

        def sales_between(start=None, end=None):
            q = f"""SELECT COALESCE(SUM({UNITS}),0) n,
                           COALESCE(SUM(sale_price+COALESCE(postage_charged,0)),0) revenue,
                           COALESCE(SUM(profit),0) profit FROM sales WHERE {valid}"""
            params = []
            if start:  # undated sales only count towards all-time totals
                q += " AND sale_date >= ?"
                params.append(start)
            if end:
                q += " AND sale_date < ?"
                params.append(end)
            return dict(c.execute(q, params).fetchone())

        out["month"] = sales_between(month_start)
        out["year"] = sales_between(year_start)
        out["all"] = sales_between()
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
            SELECT COALESCE(NULLIF(brand,''),'(none)') brand, SUM({UNITS}) n,
                   SUM(sale_price) revenue, SUM(profit) profit
            FROM sales WHERE {valid} AND COALESCE(item_kind, '') != 'adjustment'
            GROUP BY 1 ORDER BY profit DESC LIMIT 10""")]

        # average days from listing to sale for phones (falls back to the bought / added
        # date for phones sold before listing dates were recorded)
        r = c.execute(f"""SELECT AVG(MAX(0, julianday(s.sale_date) - julianday(COALESCE(
                                   NULLIF(d.listed_at, ''), d.purchase_date, substr(d.created_at,1,10))))) days,
                                 SUM(COALESCE(d.listed_at, '') != '') listed,
                                 SUM(COALESCE(d.listed_at, '') = '') fallback
                          FROM sales s JOIN devices d ON s.item_kind='device' AND d.id=s.item_id
                          WHERE {valid.replace('status', 's.status')} AND COALESCE(s.sale_date,'') != ''
                          """).fetchone()
        out["avg_days_to_sell"] = r["days"]
        out["days_listed_n"] = r["listed"] or 0
        out["days_fallback_n"] = r["fallback"] or 0

        cutoff = (today - dt.timedelta(days=aged)).isoformat()
        out["aged"] = [dict(x) for x in c.execute("""
            SELECT * FROM devices WHERE COALESCE(status,'') NOT IN ('Sold', ?)
            AND COALESCE(purchase_date, substr(created_at,1,10)) <= ?
            ORDER BY COALESCE(purchase_date, created_at) LIMIT 50""", (WRITTEN_OFF, cutoff))]
        out["aged_days"] = aged
        return out

    def integrity_check(self) -> str:
        return self.conn.execute("PRAGMA integrity_check").fetchone()[0]

    def vacuum(self):
        self.conn.commit()
        self.conn.execute("VACUUM")


def sku_for_section(sku, category) -> str:
    """WP-/FD- prefix for the section; phones moved to Working from Faulty get "-R"
    (repaired). Custom SKUs that don't follow the pattern are left alone."""
    m = re.match(r"^(WP|FD)-(.+?)(-R)?$", str(sku or ""))
    if not m:
        return sku
    return f"WP-{m.group(2)}-R" if category == "working" else f"FD-{m.group(2)}"


UNITS = "CASE WHEN item_kind = 'adjustment' THEN COALESCE(quantity, 0) ELSE 1 END"


def listing_start(device) -> tuple:
    """(date, basis) that days-to-sell counts from: when it was listed, else bought / added."""
    if device.get("listed_at"):
        return str(device["listed_at"])[:10], "listed"
    return str(device.get("purchase_date") or device.get("created_at") or "")[:10], "added"


def _days_between(start, end):
    try:
        return max(0, (dt.date.fromisoformat(str(end)[:10])
                       - dt.date.fromisoformat(str(start)[:10])).days)
    except ValueError:
        return None


def _age_days(date_text, today):
    if not date_text:
        return None
    try:
        return (today - dt.date.fromisoformat(str(date_text)[:10])).days
    except ValueError:
        return None
