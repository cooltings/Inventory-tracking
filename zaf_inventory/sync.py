"""Two-way Wi-Fi sync between copies of the app (e.g. Windows PC <-> Android tablet).

How it works
* Every synced row has a permanent ``uid`` and a ``sync_ts`` (UTC time of its last
  change); deletes leave a record in ``sync_deleted``. Triggers keep these up to date
  (see db.py), so nothing else in the app needs to know about sync.
* A sync is one round trip. The device that presses Sync (the client) sends its whole
  state to the other (the host). The host merges it, then replies with its merged
  state, which the client merges in turn. Both end up identical.
* Merging is per row: the newer edit wins; a delete wins over older edits; an edit
  made after a delete brings the item back. Photos are copied whichever way is missing.
* Requests are signed with the host's pairing code (HMAC-SHA256), so nobody else on
  the network can read or change the data without it.

No Qt here, so the logic can be tested on its own.
"""
import gzip
import hashlib
import hmac
import json
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import APP_ID, __version__
from .db import SYNC_TABLES, Database, now_iso
from .paths import photos_dir

PROTOCOL = 1
HTTP_PORT = 47822
DISCOVERY_PORT = 47821
DISCOVER_MSG = b"ZAF-INVENTORY-DISCOVER"
MAX_BODY = 512 * 1024 * 1024
CLOCK_SKEW = 15 * 60  # seconds a request's timestamp may differ from ours

ENTITY_TABLE = {"device": "devices", "part": "parts", "sale": "sales"}
# local integer reference columns, replaced by the parent's uid when sent
REFS = {
    "sales": ("item_id", "item"),
    "notes": ("entity_id", "entity"),
    "photos": ("entity_id", "entity"),
    "sheet_rows": ("sheet_id", "sheets"),
    "device_tests": ("device_id", "devices"),
    "device_test_meta": ("device_id", "devices"),
    "activity": ("entity_id", None),  # informational only; not linked across devices
}
# rows that are the same thing even if they were created separately on each device
NATURAL_KEYS = {"device_tests": ("device_id", "test_key"),
                "device_test_meta": ("device_id",),
                "settings": ("key",)}
# settings shared between devices; everything else (theme, backups, sync) stays local
SYNCED_SETTINGS = {"currency", "fee_percent", "fee_fixed", "default_platform", "aged_days",
                   "report_business"}
# apply parents before children so references can be resolved
ORDER = ("devices", "parts", "sheets", "sales", "notes", "photos", "sheet_rows",
         "device_tests", "device_test_meta", "settings", "activity")


class SyncError(Exception):
    pass


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def is_synced_setting(key) -> bool:
    return key in SYNCED_SETTINGS or str(key).startswith("list_")


# ============================================================================ state
def _parent_table(table, row, ref):
    if ref == "item":
        return "devices" if row.get("item_kind") == "device" else "parts"
    if ref == "entity":
        return ENTITY_TABLE.get(row.get("entity"))
    return ref


def export_state(db) -> dict:
    """Everything that syncs, with local ids swapped for uids."""
    c = db.conn
    uid_of = {}
    for t in ("devices", "parts", "sales", "sheets"):
        uid_of[t] = {r["id"]: r["uid"] for r in c.execute(f"SELECT id, uid FROM {t}")}
    tables = {}
    for t in SYNC_TABLES:
        rows = []
        col, ref = REFS.get(t, (None, None))
        for r in c.execute(f"SELECT * FROM {t}"):
            row = dict(r)
            if t == "settings" and not is_synced_setting(row["key"]):
                continue
            row.pop("id", None)
            if col:
                local_id = row.pop(col, None)
                parent = _parent_table(t, row, ref) if ref else None
                row["_ref"] = uid_of.get(parent, {}).get(local_id) if parent else None
            rows.append(row)
        tables[t] = rows
    deleted = [dict(r) for r in c.execute("SELECT tbl, uid, deleted_at FROM sync_deleted")]
    photos = sorted(p.name for p in photos_dir().iterdir() if p.is_file())
    return {"protocol": PROTOCOL, "app_version": __version__, "tables": tables,
            "deleted": deleted, "photos": photos}


def apply_state(db, state) -> dict:
    """Merge another device's state into this database. Returns change counts."""
    if state.get("protocol") != PROTOCOL:
        raise SyncError("The other device runs an incompatible version - update both apps.")
    c = db.conn
    stats = {"added": 0, "updated": 0, "deleted": 0}
    try:
        c.execute("BEGIN")
        # 1. deletions: remove our copy unless we changed it after it was deleted
        for d in state.get("deleted", []):
            t, uid, when = d["tbl"], d["uid"], d["deleted_at"]
            if t not in SYNC_TABLES:
                continue
            row = c.execute(f"SELECT rowid AS rid, sync_ts FROM {t} WHERE uid=?", (uid,)).fetchone()
            if row and (row["sync_ts"] or "") > when:
                continue  # changed here after it was deleted there: keep it
            prev = c.execute("SELECT deleted_at FROM sync_deleted WHERE tbl=? AND uid=?",
                             (t, uid)).fetchone()
            if row:
                if t == "photos":
                    _remove_photo_file(c, row["rid"])
                c.execute(f"DELETE FROM {t} WHERE rowid=?", (row["rid"],))  # trigger logs "now"
                stats["deleted"] += 1
            # keep the original deletion time so both devices hold identical records
            final = max(prev["deleted_at"], when) if prev else when
            c.execute("INSERT OR REPLACE INTO sync_deleted (tbl, uid, deleted_at) VALUES (?,?,?)",
                      (t, uid, final))
        tomb = {(r["tbl"], r["uid"]): r["deleted_at"]
                for r in c.execute("SELECT tbl, uid, deleted_at FROM sync_deleted")}

        # 2. rows, parents first
        tables = state.get("tables", {})
        for t in ORDER:
            cols = db.columns(t)
            col, ref = REFS.get(t, (None, None))
            for remote in tables.get(t, []):
                uid, ts = remote.get("uid"), remote.get("sync_ts") or ""
                if not uid or (t == "settings" and not is_synced_setting(remote.get("key"))):
                    continue
                if (t, uid) in tomb:
                    if tomb[(t, uid)] >= ts:
                        continue  # we deleted it after this version was made
                    # edited elsewhere after we deleted it: it comes back
                    c.execute("DELETE FROM sync_deleted WHERE tbl=? AND uid=?", (t, uid))
                data = {k: v for k, v in remote.items() if k in cols and k not in ("id", "_ref")}
                if col:
                    parent = _parent_table(t, remote, ref) if ref else None
                    local_parent = None
                    if parent and remote.get("_ref"):
                        p = c.execute(f"SELECT id FROM {parent} WHERE uid=?",
                                      (remote["_ref"],)).fetchone()
                        local_parent = p["id"] if p else None
                    if local_parent is None and t not in ("sales", "activity"):
                        continue  # its parent no longer exists here
                    data[col] = local_parent
                local = c.execute(f"SELECT rowid AS rid, sync_ts FROM {t} WHERE uid=?", (uid,)).fetchone()
                if local is None and t in NATURAL_KEYS:
                    keys = NATURAL_KEYS[t]
                    if all(data.get(k) is not None for k in keys):
                        where = " AND ".join(f"{k}=?" for k in keys)
                        local = c.execute(f"SELECT rowid AS rid, sync_ts FROM {t} WHERE {where}",
                                          [data[k] for k in keys]).fetchone()
                if local is None:
                    names = ", ".join(data)
                    c.execute(f"INSERT INTO {t} ({names}) VALUES ({', '.join('?' for _ in data)})",
                              list(data.values()))
                    stats["added"] += t != "activity"  # log entries aren't changes to report
                elif ts > (local["sync_ts"] or ""):
                    sets = ", ".join(f"{k}=?" for k in data)
                    c.execute(f"UPDATE {t} SET {sets} WHERE rowid=?",
                              list(data.values()) + [local["rid"]])
                    stats["updated"] += t != "activity"

        # 3. tidy up anything left pointing at a parent that was deleted
        for t, (col, ref) in REFS.items():
            if t in ("sales", "activity"):
                continue
            if ref == "entity":
                for kind, parent in ENTITY_TABLE.items():
                    c.execute(f"DELETE FROM {t} WHERE entity=? AND {col} NOT IN "
                              f"(SELECT id FROM {parent})", (kind,))
            else:
                c.execute(f"DELETE FROM {t} WHERE {col} NOT IN (SELECT id FROM {ref})")
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    return stats


def _remove_photo_file(c, rid):
    row = c.execute("SELECT filename FROM photos WHERE rowid=?", (rid,)).fetchone()
    if row:
        try:
            (photos_dir() / row["filename"]).unlink()
        except OSError:
            pass


def safe_photo_name(name) -> str:
    name = Path(str(name)).name
    if not name or name.startswith(".") or name != str(name).strip():
        raise SyncError(f"Bad photo name: {name!r}")
    return name


# ============================================================================ signing
def _sign(code, method, path, stamp, body) -> str:
    msg = f"{method}\n{path}\n{stamp}\n{hashlib.sha256(body).hexdigest()}".encode()
    return hmac.new(str(code).encode(), msg, hashlib.sha256).hexdigest()


def _pack(obj) -> bytes:
    return gzip.compress(json.dumps(obj, separators=(",", ":")).encode())


def _unpack(body) -> dict:
    return json.loads(gzip.decompress(body).decode())


# ============================================================================ host (server)
class SyncHost:
    """Lets other devices sync with this one. Runs in background threads."""

    def __init__(self, db_path, code, name=None, port=HTTP_PORT, on_synced=None):
        self.db_path, self.code, self.port = str(db_path), str(code), port
        self.name = name or socket.gethostname()
        self.on_synced = on_synced  # called (from a background thread) after each sync
        self.lock = threading.Lock()
        self.httpd = None
        self.udp = None
        self.threads = []

    def start(self):
        host = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _body(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    raise SyncError("Request too large")
                return self.rfile.read(length) if length else b""

            def _authorised(self, body):
                try:
                    stamp = int(self.headers.get("X-Zaf-Time", "0"))
                except ValueError:
                    return False
                if abs(time.time() - stamp) > CLOCK_SKEW:
                    return False
                expected = _sign(host.code, self.command, self.path, stamp, body)
                return hmac.compare_digest(expected, self.headers.get("X-Zaf-Sig", ""))

            def _reply(self, status, body=b"", ctype="application/octet-stream"):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _handle(self):
                try:
                    body = self._body()
                except SyncError as exc:
                    return self._reply(413, str(exc).encode(), "text/plain")
                if self.path == "/ping":
                    return self._reply(200, json.dumps({"app": APP_ID, "name": host.name,
                                                        "version": __version__}).encode(),
                                       "application/json")
                if not self._authorised(body):
                    return self._reply(403, b"Wrong pairing code (or the clocks differ by "
                                            b"more than 15 minutes).", "text/plain")
                try:
                    if self.command == "POST" and self.path == "/sync":
                        return self._reply(200, host.handle_sync(_unpack(body)))
                    if self.path.startswith("/photo/"):
                        name = safe_photo_name(self.path[len("/photo/"):])
                        target = photos_dir() / name
                        if self.command == "GET":
                            if not target.is_file():
                                return self._reply(404)
                            return self._reply(200, target.read_bytes())
                        if self.command == "POST":
                            if not target.exists():
                                tmp = target.with_suffix(target.suffix + ".part")
                                tmp.write_bytes(body)
                                tmp.replace(target)
                            return self._reply(200)
                    return self._reply(404)
                except Exception as exc:  # report, never crash the server
                    return self._reply(500, f"{type(exc).__name__}: {exc}".encode(),
                                       "text/plain")

            do_GET = do_POST = _handle

        self.httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self.httpd.daemon_threads = True
        self._spawn(self.httpd.serve_forever)
        self._start_discovery()
        return self

    def _spawn(self, fn):
        t = threading.Thread(target=fn, daemon=True)
        t.start()
        self.threads.append(t)

    def _start_discovery(self):
        try:
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp.bind(("0.0.0.0", DISCOVERY_PORT))
            udp.settimeout(1.0)
        except OSError:
            return  # discovery is a convenience; typing the address still works
        self.udp = udp

        def serve():
            while self.udp is not None:
                try:
                    data, addr = udp.recvfrom(256)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if data == DISCOVER_MSG:
                    reply = json.dumps({"app": APP_ID, "name": self.name, "port": self.port})
                    try:
                        udp.sendto(reply.encode(), addr)
                    except OSError:
                        pass
        self._spawn(serve)

    def handle_sync(self, request) -> bytes:
        with self.lock:
            db = Database(self.db_path)
            try:
                received = apply_state(db, request["state"])
                db.set_setting("last_sync", now_iso())
                db.log("Synced", "sync", None,
                       f"with {request.get('from') or 'a device'}: received "
                       f"{received['added']} new, {received['updated']} changed, "
                       f"{received['deleted']} deleted")
                state = export_state(db)
            finally:
                db.close()
        missing = sorted(set(request["state"].get("photos", [])) - set(state["photos"]))
        if self.on_synced:
            try:
                self.on_synced(received)
            except Exception:
                pass
        return _pack({"state": state, "received": received, "missing_photos": missing,
                      "name": self.name})

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.udp:
            udp, self.udp = self.udp, None
            udp.close()


# ============================================================================ client
def _request(base, code, method, path, body=b"", timeout=60):
    stamp = int(time.time())
    req = urllib.request.Request(base + path, data=body if method == "POST" else None,
                                 method=method)
    req.add_header("X-Zaf-Time", str(stamp))
    req.add_header("X-Zaf-Sig", _sign(code, method, path, stamp, body))
    req.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace").strip()
        if exc.code == 403:
            raise SyncError(f"The PC refused the pairing code. {detail}") from exc
        raise SyncError(f"The PC reported an error ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise SyncError(f"Could not reach {base}: {reason}. Check both devices are on the "
                        "same Wi-Fi and syncing is switched on in the PC app.") from exc


def normalise_address(address) -> str:
    address = str(address).strip().rstrip("/")
    if not address:
        raise SyncError("Enter the PC's address (or press Find).")
    if "://" not in address:
        address = "http://" + address
    host_part = address.split("://", 1)[1]
    if ":" not in host_part:
        address += f":{HTTP_PORT}"
    return address


def sync_with(db_path, address, code, my_name=None, progress=None) -> dict:
    """Run a full two-way sync with a host. Returns a summary dict."""
    say = progress or (lambda msg: None)
    base = normalise_address(address)
    db = Database(db_path)
    try:
        say("Sending changes...")
        state = export_state(db)
        reply = _unpack(_request(base, code, "POST", "/sync",
                                 _pack({"from": my_name or socket.gethostname(),
                                        "state": state}), timeout=300))
        say("Merging changes from the PC...")
        got = apply_state(db, reply["state"])
        sent_photos = 0
        for name in reply.get("missing_photos", []):
            name = safe_photo_name(name)
            path = photos_dir() / name
            if path.is_file():
                say(f"Uploading photo {sent_photos + 1}...")
                _request(base, code, "POST", f"/photo/{name}", path.read_bytes(), timeout=300)
                sent_photos += 1
        got_photos = 0
        for name in reply["state"].get("photos", []):
            name = safe_photo_name(name)
            target = photos_dir() / name
            if not target.exists():
                say(f"Downloading photo {got_photos + 1}...")
                data = _request(base, code, "GET", f"/photo/{name}", timeout=300)
                tmp = target.with_suffix(target.suffix + ".part")
                tmp.write_bytes(data)
                tmp.replace(target)
                got_photos += 1
        summary = {"host": reply.get("name") or base, "sent": reply.get("received", {}),
                   "received": got, "photos_sent": sent_photos, "photos_received": got_photos}
        db.set_setting("last_sync", now_iso())
        db.log("Synced", "sync", None,
               f"with {summary['host']}: received {got['added']} new, {got['updated']} changed, "
               f"{got['deleted']} deleted")
        return summary
    finally:
        db.close()


def discover(timeout=1.5) -> list:
    """Find PCs on the local network that have syncing switched on."""
    found = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.3)
        for target in ("255.255.255.255", "<broadcast>"):
            try:
                sock.sendto(DISCOVER_MSG, (target, DISCOVERY_PORT))
            except OSError:
                pass
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, (ip, _) = sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                info = json.loads(data.decode())
            except ValueError:
                continue
            if info.get("app") == APP_ID:
                found[ip] = {"address": f"{ip}:{info.get('port', HTTP_PORT)}",
                             "name": info.get("name") or ip}
    finally:
        sock.close()
    return list(found.values())


def local_addresses() -> list:
    """This machine's LAN IP addresses, for showing on the host screen."""
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent; picks the outgoing interface
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))
