"""Runs a sync host in its own process (its own data folder), for tests.

Usage: ZAF_DATA_DIR=<dir> python tests/sync_host_helper.py <code> <port>
Prints READY once listening and stops when stdin closes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zaf_inventory.db import Database  # noqa: E402
from zaf_inventory.paths import db_path  # noqa: E402
from zaf_inventory.sync import SyncHost  # noqa: E402

code, port = sys.argv[1], int(sys.argv[2])
Database(db_path()).close()  # create / upgrade the database first
host = SyncHost(db_path(), code, name="Test PC", port=port).start()
print("READY", flush=True)
sys.stdin.read()
host.stop()
