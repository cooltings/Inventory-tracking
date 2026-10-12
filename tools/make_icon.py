"""Render the app icon to app.ico / app.png for the packaged .exe."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from zaf_inventory.theme import app_icon  # noqa: E402

app = QGuiApplication(sys.argv)
root = Path(__file__).resolve().parent.parent
pix = app_icon().pixmap(256, 256)
pix.save(str(root / "app.png"))
if not pix.save(str(root / "app.ico")):
    print("Could not write app.ico (ICO plugin missing) - building without a custom icon.")
