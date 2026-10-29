"""Hermetic test of the update-notification logic (no network, offscreen)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("VRC_PARAMETER_RELAY_DATA_DIR", tempfile.mkdtemp(prefix="vrcpr-upd-"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from vrc_parameter_relay import updater
from vrc_parameter_relay.core import AppCore
from vrc_parameter_relay.store import Store
from vrc_parameter_relay.tunnel import Tunnel
from vrc_parameter_relay.updater import is_newer
from vrc_parameter_relay.webserver import GuestServer
from vrc_parameter_relay.ui.main_window import (
    MainWindow, UpdateDialog, _unique_path, pick_update_asset,
)

updater.check_for_update = lambda *a, **k: None  # the window's launch check stays offline


class StubLink:
    on_param = on_avatar = on_full_sync = on_status = None
    def send_param(self, *a): return True
    def refetch(self): pass


def check(desc, cond):
    print(("OK   " if cond else "FAIL ") + desc)
    if not cond:
        sys.exit(1)


def release(tag, assets=None):
    return {"available": True, "tag": tag, "latest": tag.lstrip("v"),
            "url": "https://github.com/Blise518B/VRCParameterRelay/releases", "asset": None,
            "assets": assets or []}


def close_dialogs_soon(seen):
    def close():
        for d in app.topLevelWidgets():
            if d.__class__.__name__ == "UpdateDialog" and d.isVisible():
                seen.append(d.tag)
                d.reject()
    QTimer.singleShot(0, close)


# version comparison
check("newer minor", is_newer("v1.1.0", "1.0.1"))
check("newer patch", is_newer("v1.0.2", "1.0.1"))
check("same is not newer", not is_newer("v1.0.1", "1.0.1"))
check("older is not newer", not is_newer("v1.0.0", "1.0.1"))
check("double-digit compare", is_newer("v1.10.0", "1.9.0"))
check("junk tag is safe", not is_newer("nightly", "1.0.1"))
check("1.3 equals 1.3.0", not is_newer("v1.3", "1.3.0"))

# the right download for each OS (real asset names from a release) — what a
# build that can't replace itself fetches into Downloads
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

# a build that can't swap itself (this source run) offers its own download;
# with none that fits, the release page
dlg = UpdateDialog(win, release("v9.9.99", ASSETS))
expect = "Windows" if sys.platform.startswith("win") else "Linux"
check("no self-update here -> the platform download is the primary button",
      dlg.install_btn is None and dlg.download_btn is not None
      and dlg.download_btn.text() == f"⬇ Download for {expect}"
      and dlg.download_btn.objectName() == "Primary")
dlg2 = UpdateDialog(win, release("v9.9.99"))
check("no matching asset -> the release page is primary",
      dlg2.download_btn is None and dlg2.page_btn.objectName() == "Primary")

# skip_update stores the tag and hides the label (isHidden reflects the
# explicit flag regardless of the offscreen window not being shown)
win.skip_update("v9.9.9")
check("skip stored in settings", store.settings["skip_update_version"] == "v9.9.9")
check("label hidden after skip", win.update_label.isHidden())

# a skipped version: the status-bar link appears, but no window pops up
seen: list = []
close_dialogs_soon(seen)
win._on_update_checked(release("v9.9.9"), False)
check("skipped version doesn't pop up on launch", seen == [])

# a different (newer, non-skipped) version pops the window
seen.clear()
close_dialogs_soon(seen)
win._on_update_checked(release("v9.9.10"), False)
check("non-skipped version pops up", seen == ["v9.9.10"])
check("non-skipped version shows the status-bar link", not win.update_label.isHidden())

# asking by hand always shows it, even for a skipped version
store.set("skip_update_version", "v9.9.10")
seen.clear()
close_dialogs_soon(seen)
win._on_update_checked(release("v9.9.10"), True)
check("manual check shows even a skipped version", seen == ["v9.9.10"])

# nothing new / offline: no window
seen.clear()
close_dialogs_soon(seen)
win._on_update_checked({**release("v1.0.0"), "available": False}, False)
win._on_update_checked(None, False)
check("no window when up to date or offline", seen == [])

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
