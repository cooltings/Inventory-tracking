"""Two-way sync between a 'PC' (host, separate process) and a 'tablet' (this process).

Run locally:  python tests/test_sync.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PC_DIR = Path(tempfile.mkdtemp(prefix="zaf-pc-"))
TAB_DIR = Path(tempfile.mkdtemp(prefix="zaf-tablet-"))
os.environ["ZAF_DATA_DIR"] = str(TAB_DIR)  # this process is the tablet

from zaf_inventory import sync  # noqa: E402
from zaf_inventory.db import Database  # noqa: E402
from zaf_inventory.paths import db_path  # noqa: E402

CODE = "482913"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


PORT = free_port()
host = subprocess.Popen([sys.executable, str(ROOT / "tests" / "sync_host_helper.py"), CODE, str(PORT)],
                        env={**os.environ, "ZAF_DATA_DIR": str(PC_DIR)},
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
assert host.stdout.readline().strip() == "READY", "host did not start"
ADDR = f"127.0.0.1:{PORT}"


def pc():
    return Database(PC_DIR / "inventory.db")


def tablet():
    return Database(db_path())


def snapshot(db):
    """Everything that should match after a sync, independent of local ids/order."""
    state = sync.export_state(db)
    out = {}
    for t, rows in state["tables"].items():
        if t == "activity":
            continue  # each side logs its own "Synced" entry
        out[t] = sorted(json.dumps(r, sort_keys=True, default=str) for r in rows)
    out["deleted"] = sorted(json.dumps(d, sort_keys=True) for d in state["deleted"])
    return out


def do_sync():
    return sync.sync_with(db_path(), ADDR, CODE, my_name="Test tablet")


def converged(msg):
    a, b = pc(), tablet()
    try:
        sa, sb = snapshot(a), snapshot(b)
        for t in sa:
            assert sa[t] == sb[t], f"{msg}: '{t}' differs\nPC:     {sa[t][:3]}\ntablet: {sb[t][:3]}"
    finally:
        a.close()
        b.close()


try:
    # ------------------------------------------------------------------ 1. PC data -> empty tablet
    p = pc()
    iphone = p.insert("devices", {"category": "working", "brand": "Apple", "model": "iPhone 12",
                                  "imei": "356938035643809", "purchase_price": 200, "quantity": 1,
                                  "status": "In Stock"})
    galaxy = p.insert("devices", {"category": "faulty", "brand": "Samsung", "model": "S8",
                                  "faults": "Cracked screen", "quantity": 1})
    battery = p.insert("parts", {"part_type": "Battery", "quantity": 4, "unit_cost": 8})
    p.record_sale("device", galaxy, {"sku": "FD-00002", "title": "S8", "sale_price": 60,
                                     "quantity": 1, "status": "Paid", "sale_date": "2026-10-01"})
    p.add_note("device", iphone, "Tested fully")
    p.add_note("part", battery, "Genuine pulls")
    p.set_test(iphone, "power_on", "Pass")
    p.set_test(iphone, "net_5g", "Fail", "No 5G")
    p.set_test_meta(iphone, "Anthony", "2026-10-20", "Good phone")
    sheet = p.add_sheet("Cash", 100)
    p.add_sheet_row(sheet, {"description": "Float", "money_in": 50})
    p.set_setting("list_brands", json.dumps(["Apple", "Samsung", "Zaf Brand"]))
    p.set_setting("fee_percent", "11.5")
    p.set_setting("theme", "light")  # local only
    (PC_DIR / "photos").mkdir(exist_ok=True)
    (PC_DIR / "photos" / "device-1-abc.jpg").write_bytes(b"\xff\xd8 fake jpeg")
    p.conn.execute("INSERT INTO photos (entity, entity_id, filename, caption, created_at) "
                   "VALUES ('device', ?, 'device-1-abc.jpg', 'front', '2026-10-20')", (iphone,))
    p.conn.commit()
    p.close()

    summary = do_sync()
    assert summary["received"]["added"] >= 10, summary
    converged("first sync")
    t = tablet()
    assert [r["model"] for r in t.list_devices("working")] == ["iPhone 12"]
    tab_iphone = t.list_devices("working")[0]["id"]
    assert t.device_tests(tab_iphone)["net_5g"] == {"result": "Fail", "note": "No 5G"}
    assert t.notes("device", tab_iphone)[0]["body"] == "Tested fully"
    assert t.get_list("brands")[-1] == "Zaf Brand" and t.setting("fee_percent") == "11.5"
    assert t.setting("theme") != "light", "theme should stay local"
    sale = t.list_sales()[0]
    assert t.get("devices", sale["item_id"])["model"] == "S8", "sale must point at the same phone"
    assert (TAB_DIR / "photos" / "device-1-abc.jpg").read_bytes() == b"\xff\xd8 fake jpeg"
    assert len(t.sheet_rows(t.sheets()[0]["id"])) == 1
    t.close()

    # ------------------------------------------------------------------ 2. nothing changed -> no-op
    summary = do_sync()
    assert summary["received"] == {"added": 0, "updated": 0, "deleted": 0}, summary
    assert summary["sent"] == {"added": 0, "updated": 0, "deleted": 0}, summary

    # ------------------------------------------------------------------ 3. edits on both sides
    t = tablet()
    t_iphone = t.list_devices("working")[0]["id"]
    t.update("devices", t_iphone, {"list_price": 330})                    # tablet edit
    new_dev = t.insert("devices", {"category": "working", "brand": "Google", "model": "Pixel 7",
                                   "quantity": 1, "status": "In Stock"})  # tablet add
    assert t.get("devices", new_dev)["sku"].startswith("WP-"), "tablet SKU"
    t.add_note("device", new_dev, "Added on the tablet")
    t.set_test(t_iphone, "power_on", "")                                 # clear a test
    t.close()
    p = pc()
    p.update("parts", battery, {"quantity": 9})                           # PC edit
    p.delete_sheet_rows([r["id"] for r in p.sheet_rows(sheet)])           # PC delete
    p.close()
    summary = do_sync()
    converged("edits on both sides")
    p = pc()
    assert p.get("devices", iphone)["list_price"] == 330
    assert any(d["model"] == "Pixel 7" for d in p.list_devices("working"))
    assert "power_on" not in p.device_tests(iphone)
    p.close()
    t = tablet()
    assert next(x for x in t.list_parts())["quantity"] == 9
    assert t.sheet_rows(t.sheets()[0]["id"]) == []
    t.close()

    # ------------------------------------------------------------------ 4. same field, both sides
    p = pc()
    p.update("devices", iphone, {"colour": "Blue (PC)"})
    p.close()
    time.sleep(0.05)
    t = tablet()
    t.update("devices", t_iphone, {"colour": "Black (tablet, later)"})
    t.close()
    do_sync()
    converged("conflict")
    p = pc()
    assert p.get("devices", iphone)["colour"] == "Black (tablet, later)", "newest edit wins"
    p.close()

    # ------------------------------------------------------------------ 5. delete vs later edit
    p = pc()
    p.delete("devices", iphone)                                            # PC deletes...
    p.close()
    time.sleep(0.05)
    t = tablet()
    t.update("devices", t_iphone, {"location": "Shelf B"})  # ...tablet edits later
    t.close()
    do_sync()
    converged("edit after delete")
    p = pc()
    assert any(d["model"] == "iPhone 12" and d["location"] == "Shelf B"
               for d in p.list_devices("working")), "a later edit brings a deleted item back"
    p.close()

    # plain delete propagates
    t = tablet()
    pixel = next(d for d in t.list_devices("working") if d["model"] == "Pixel 7")
    t.delete("devices", pixel["id"])
    t.close()
    do_sync()
    converged("delete")
    p = pc()
    assert not any(d["model"] == "Pixel 7" for d in p.list_devices("working"))
    assert not any(n["body"] == "Added on the tablet"
                   for n in [dict(r) for r in p.conn.execute("SELECT body FROM notes")])
    p.close()

    # ------------------------------------------------------------------ 6. write-offs, sales, settings
    t = tablet()
    part = t.list_parts()[0]
    t.write_off("parts", part["id"], "Water damaged")
    t.set_setting("list_brands", json.dumps(["Apple"]))
    t.close()
    do_sync()
    converged("write-off + settings")
    p = pc()
    assert p.get("parts", battery)["status"] == "Written Off"
    assert p.get_list("brands") == ["Apple"]
    assert p.setting("theme") == "light"
    p.close()

    # ------------------------------------------------------------------ 7. security
    try:
        sync.sync_with(db_path(), ADDR, "000000")
    except sync.SyncError as exc:
        assert "pairing code" in str(exc), exc
    else:
        raise AssertionError("wrong pairing code was accepted")
    assert sync.safe_photo_name("../../evil.jpg") == "evil.jpg", "paths are stripped"
    assert sync.normalise_address("192.168.1.20") == "http://192.168.1.20:47822"

    # ------------------------------------------------------------------ 8. backups still work
    from zaf_inventory import backup
    t = tablet()
    before = snapshot(t)
    zip_path = backup.create_backup(t, "manual")
    backup.restore_backup(t, zip_path)
    assert snapshot(t) == before, "restore must keep sync ids"
    t.close()
    # ------------------------------------------------------------------ 9. the app side (Qt)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    from zaf_inventory.sync_ui import SyncController, SyncDialog
    app = QApplication.instance() or QApplication([])
    t = tablet()
    ctl = SyncController(t)
    assert t.setting("sync_auto") == "0", "manual-only must be the default"
    t.set_setting("sync_peer", ADDR)
    t.set_setting("sync_peer_code", CODE)
    ctl.apply_auto_setting()
    assert not ctl.timer.isActive(), "manual mode must not schedule syncs"
    dlg = SyncDialog(t, ctl)
    assert dlg.manual.isChecked() and not dlg.auto.isChecked()
    dlg.auto.setChecked(True)
    dlg.minutes.setValue(5)
    dlg._save_client()
    assert t.setting("sync_auto") == "1" and ctl.timer.isActive() and ctl.timer.interval() == 300000
    dlg.manual.setChecked(True)
    dlg._save_client()
    assert t.setting("sync_auto") == "0" and not ctl.timer.isActive()

    results = {}
    loop = QEventLoop()
    ctl.sync_finished.connect(lambda s: (results.setdefault("ok", s), loop.quit()))
    ctl.sync_failed.connect(lambda m: (results.setdefault("err", m), loop.quit()))
    p = pc()
    p.insert("devices", {"category": "working", "brand": "Nokia", "model": "3310", "quantity": 1})
    p.close()
    assert dlg.sync_btn.isEnabled()
    dlg._sync()                                   # press Sync Now
    assert not dlg.sync_btn.isEnabled(), "button should show it is busy"
    QTimer.singleShot(30000, loop.quit)
    loop.exec()
    assert "ok" in results, results
    assert any(d["model"] == "3310" for d in t.list_devices("working"))
    assert dlg.sync_btn.isEnabled() and "Synced with Test PC" in dlg.status.text(), dlg.status.text()

    # hosting from the app side: this controller hosts, a raw client syncs to it
    port2 = free_port()
    ctl.db.set_setting("sync_code", "135790")
    import zaf_inventory.sync as sync_mod
    old_port = sync_mod.HTTP_PORT
    sync_mod.HTTP_PORT = port2
    try:
        ctl.start_host()
        assert ctl.host is not None
        found_ping = sync._request(f"http://127.0.0.1:{port2}", "x", "GET", "/ping")
        assert b"ZafInventory" in found_ping
    finally:
        ctl.shutdown()
        sync_mod.HTTP_PORT = old_port
    dlg.close()
    t.close()
    print("sync test passed")
finally:
    host.stdin.close()
    host.wait(timeout=10)
