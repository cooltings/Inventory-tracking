"""Backup & restore.

A backup is a single .zip holding a consistent snapshot of the database (taken
with SQLite's online backup API, so it is safe while the app is running), all
item photos, and a manifest.json describing what is inside.
"""
import datetime as dt
import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from . import __version__
from .paths import default_backup_dir, photos_dir

PREFIX = "ZafInventory-backup-"


def backup_dir(db) -> Path:
    path = Path(db.setting("backup_dir") or default_backup_dir())
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_backup(db, reason="manual", dest_dir=None) -> Path:
    dest = Path(dest_dir) if dest_dir else backup_dir(db)
    dest.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = dest / f"{PREFIX}{stamp}-{reason}.zip"
    n = 1
    while target.exists():
        target = dest / f"{PREFIX}{stamp}-{reason}-{n}.zip"
        n += 1

    db.conn.commit()
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "inventory.db"
        out = sqlite3.connect(str(snap))
        with out:
            db.conn.backup(out)
        out.close()

        counts = {}
        for table in ("devices", "parts", "sales", "notes", "photos", "sheet_rows"):
            counts[table] = db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        manifest = {"app": "Zaf Inventory", "version": __version__,
                    "created": dt.datetime.now().isoformat(timespec="seconds"),
                    "reason": reason, "counts": counts}

        partial = target.with_suffix(".zip.part")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snap, "inventory.db")
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            for photo in photos_dir().iterdir():
                if photo.is_file():
                    zf.write(photo, f"photos/{photo.name}")
        partial.replace(target)

    db.set_setting("last_backup", dt.datetime.now().isoformat(timespec="seconds"))

    secondary = db.setting("backup_dir_secondary")
    if secondary:
        try:
            Path(secondary).mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, Path(secondary) / target.name)
        except OSError:
            pass  # e.g. USB stick not plugged in - primary backup still exists
    return target


def is_backup(path) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            return "inventory.db" in zf.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def list_backups(db) -> list:
    """Every Zaf Inventory backup in the backup folder, whatever it is called."""
    files = [p for p in backup_dir(db).glob("*.zip") if is_backup(p)]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def prune_backups(db):
    """Keep the newest N automatic backups. Manual backups are never removed."""
    keep = max(1, int(float(db.setting("backup_keep") or 30)))
    for folder in filter(None, [str(backup_dir(db)), db.setting("backup_dir_secondary")]):
        autos = sorted((p for p in Path(folder).glob(f"{PREFIX}*-auto*.zip")),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for old in autos[keep:]:
            try:
                old.unlink()
            except OSError:
                pass


def backup_due(db) -> bool:
    if db.setting("auto_backup") != "1":
        return False
    last = db.setting("last_backup")
    if not last:
        return True
    try:
        hours = float(db.setting("auto_backup_hours") or 24)
        return dt.datetime.now() - dt.datetime.fromisoformat(last) >= dt.timedelta(hours=hours)
    except ValueError:
        return True


def read_manifest(path) -> dict:
    with zipfile.ZipFile(path) as zf:
        if "manifest.json" in zf.namelist():
            return json.loads(zf.read("manifest.json"))
    return {}


def verify_backup(path) -> dict:
    """Raise ValueError if the archive is damaged or not a Zaf Inventory backup."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad:
                raise ValueError(f"Corrupt file inside backup: {bad}")
            if "inventory.db" not in zf.namelist():
                raise ValueError("This zip is not a Zaf Inventory backup (no inventory.db).")
            with tempfile.TemporaryDirectory() as tmp:
                zf.extract("inventory.db", tmp)
                con = sqlite3.connect(str(Path(tmp) / "inventory.db"))
                try:
                    result = con.execute("PRAGMA integrity_check").fetchone()[0]
                    tables = {r[0] for r in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
                finally:
                    con.close()
            if result != "ok":
                raise ValueError(f"Database in backup failed integrity check: {result}")
            if not {"devices", "parts", "sales"} <= tables:
                raise ValueError("Backup database is missing inventory tables.")
            return json.loads(zf.read("manifest.json")) if "manifest.json" in zf.namelist() else {}
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Not a valid zip file: {exc}") from exc


def restore_backup(db, path) -> Path:
    """Replace the live database and photos with the backup's contents.

    A safety backup of the current state is taken first and its path returned,
    so a restore can always be undone.
    """
    verify_backup(path)
    safety = create_backup(db, reason="pre-restore")
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(path) as zf:
            zf.extractall(tmp)
        db.close()
        try:
            shutil.copy2(Path(tmp) / "inventory.db", db.path)
            pdir = photos_dir()
            for old in pdir.iterdir():
                if old.is_file():
                    old.unlink()
            src_photos = Path(tmp) / "photos"
            if src_photos.is_dir():
                for photo in src_photos.iterdir():
                    shutil.copy2(photo, pdir / photo.name)
        finally:
            db.open()
    db.log("Restored", "backup", None, f"From {Path(path).name}")
    return safety
