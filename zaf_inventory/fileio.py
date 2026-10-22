"""Reading and writing files the user picks.

On Windows / macOS / Linux the file dialogs return normal paths. On Android they return
``content://`` links, which Python's own file functions can't open but Qt's QFile can,
so everything that reads or writes a picked file goes through here.
"""
import tempfile
from pathlib import Path
from urllib.parse import unquote

from PySide6.QtCore import QFile, QIODevice, QUrl
from PySide6.QtGui import QDesktopServices


def is_uri(path) -> bool:
    text = str(path)
    return "://" in text and not text.startswith("file://")


def _qfile(path, mode):
    f = QFile(str(path))
    if not f.open(mode):
        raise OSError(f"Could not open {display_name(path)}: {f.errorString()}")
    return f


def read_bytes(path) -> bytes:
    if not is_uri(path):
        return Path(path).read_bytes()
    f = _qfile(path, QIODevice.ReadOnly)
    try:
        return bytes(f.readAll().data())
    finally:
        f.close()


def read_text(path, encoding="utf-8-sig") -> str:
    return read_bytes(path).decode(encoding)


def write_bytes(path, data: bytes):
    if not is_uri(path):
        Path(path).write_bytes(data)
        return
    f = _qfile(path, QIODevice.WriteOnly | QIODevice.Truncate)
    try:
        if f.write(data) != len(data):
            raise OSError(f"Could not write {display_name(path)}: {f.errorString()}")
    finally:
        f.close()


def display_name(path) -> str:
    """A readable file name, also for content:// links."""
    if not is_uri(path):
        return Path(path).name
    name = QUrl(str(path)).fileName() or str(path).rstrip("/").rsplit("/", 1)[-1]
    name = unquote(name)
    return name.rsplit("/", 1)[-1].rsplit(":", 1)[-1] or "file"


def local_copy(path, suffix="") -> str:
    """A normal file path for ``path``: itself, or a temporary copy of a content:// link."""
    if not is_uri(path):
        return str(path)
    suffix = suffix or Path(display_name(path)).suffix
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(read_bytes(path))
    tmp.close()
    return tmp.name


def open_external(path):
    """Open a saved file in the system's viewer (PDF reader, spreadsheet...)."""
    QDesktopServices.openUrl(QUrl(str(path)) if is_uri(path) else QUrl.fromLocalFile(str(path)))
