"""Hermetic test of the update-notification logic (no network, offscreen)."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication

from vrc_parameter_relay.core import AppCore
from vrc_parameter_relay.store import Store
from vrc_parameter_relay.tunnel import Tunnel
from vrc_parameter_relay.webserver import GuestServer
from vrc_parameter_relay.ui.main_window import (
    MainWindow, UpdateDialog, _is_newer, _unique_path, pick_update_asset,
)


class StubLink:
    on_param = on_avatar = on_full_sync = on_status = None
    def send_param(self, *a): return True
    def refetch(self): pass


def check(desc, cond):
    print(("OK   " if cond else "FAIL ") + desc)
    if not cond:
        sys.exit(1)


# version comparison
check("newer minor", _is_newer("v1.1.0", "1.0.1"))
check("newer patch", _is_newer("v1.0.2", "1.0.1"))
check("same is not newer", not _is_newer("v1.0.1", "1.0.1"))
check("older is not newer", not _is_newer("v1.0.0", "1.0.1"))
check("double-digit compare", _is_newer("v1.10.0", "1.9.0"))
check("junk tag is safe", not _is_newer("nightly", "1.0.1"))

# the right download for each OS (real asset names from a release)
ASSETS = [
    {"name": "VRCParameterRelay-Linux", "url": "u1"},
    {"name": "VRCParameterRelay-v1.4.0-x86_64.AppImage", "url": "u2"},
    {"name": "VRCParameterRelay-v1.4.0-x86_64.AppImage.zsync", "url": "u3"},
    {"name": "VRCParameterRelay-Windows.exe", "url": "u4"},
]
check("windows picks the exe",
      pick_update_asset(ASSETS, "win32")["name"] == "VRCParameterRelay-Windows.exe")
check("linux picks the AppImage, not its zsync sidecar",
      pick_update_asset(ASSETS, "linux")["name"].endswith(".AppImage"))
check("unknown platform falls back to the releases page",
      pick_update_asset(ASSETS, "darwin") is None)
check("no assets falls back too", pick_update_asset([], "win32") is None)

# downloads never overwrite an earlier file
import tempfile
with tempfile.TemporaryDirectory() as td:
    folder = Path(td)
    check("fresh name kept", _unique_path(folder, "a.exe").name == "a.exe")
    (folder / "a.exe").write_text("x")
    check("existing name bumped", _unique_path(folder, "a.exe").name == "a (2).exe")

app = QApplication([])
store = Store()
core = AppCore(store, StubLink())
web = GuestServer(core, 0)
tunnel = Tunnel(store)
win = MainWindow(core, tunnel, web)

# the dialog's primary button matches the platform's download
win._latest_assets = ASSETS
dlg = UpdateDialog(win, "v9.9.99")
expect = "Windows" if sys.platform.startswith("win") else "Linux"
check("dialog offers the platform download",
      dlg.update_btn.text() == f"⬇ Download for {expect}")
win._latest_assets = []
dlg2 = UpdateDialog(win, "v9.9.99")
check("no matching asset -> generic releases-page button",
      dlg2.update_btn.text() == "Take me to the update")

# skip_update stores the tag and hides the label (isHidden reflects the
# explicit flag regardless of the offscreen window not being shown)
win.skip_update("v9.9.9")
check("skip stored in settings", store.settings["skip_update_version"] == "v9.9.9")
check("label hidden after skip", win.update_label.isHidden())

# a skipped version does not re-notify (early return, no modal)
win._show_update("v9.9.9")
check("skipped version stays silent", win.update_label.isHidden())

# a different (newer, non-skipped) version notifies -> label shown.
# UpdateDialog.exec() is modal, so close it right after it opens.
from PySide6.QtCore import QTimer
QTimer.singleShot(0, lambda: [d.reject() for d in app.topLevelWidgets()
                              if d.__class__.__name__ == "UpdateDialog"])
win._show_update("v9.9.10")
check("non-skipped version shows the status-bar link", not win.update_label.isHidden())

# window size is saved on close and restored on the next construction
win.resize(1234, 765)
win.close()
check("window size saved on close", store.settings["window_size"] == [1234, 765])
win2 = MainWindow(core, tunnel, web)
check("window size restored on start", (win2.width(), win2.height()) == (1234, 765))
store.set("window_size", [10, 10])  # absurd -> fall back to the default
win3 = MainWindow(core, tunnel, web)
check("bad saved size falls back to default",
      (win3.width(), win3.height()) == MainWindow.DEFAULT_SIZE)

print("ALL UPDATE TESTS PASSED")
