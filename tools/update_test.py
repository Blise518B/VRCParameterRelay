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
from vrc_parameter_relay.ui.main_window import MainWindow, _is_newer


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

app = QApplication([])
store = Store()
core = AppCore(store, StubLink())
web = GuestServer(core, 0)
tunnel = Tunnel(store)
win = MainWindow(core, tunnel, web)

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

print("ALL UPDATE TESTS PASSED")
