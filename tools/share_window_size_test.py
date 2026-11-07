"""The Share window is always tall enough for what's in it.

Regression for the squeezed Share window (GitHub issue #1): with an own
address, "Other sharing methods" starts unfolded, but the window was sized
before the own-address texts filled in, so on first open the fold-outs ran
into each other until a fold was clicked. Contents that change while the
window is closed (a fold, the sharing state) must not squeeze it either.

Throwaway data dir, offscreen Qt, stubbed link/tunnel/fixed link: no network.
Set QT_QPA_FONTDIR=C:/Windows/Fonts for real text metrics (the offscreen
platform has no fonts of its own). Exits 0 on success.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
DATA = Path(tempfile.mkdtemp(prefix="vrcpr-sharesize-"))
os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = str(DATA)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# real font metrics: offscreen Qt has no fonts of its own, and without
# them wrapped text measures inconsistently — size checks would lie
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PySide6.QtWidgets import QApplication  # noqa: E402

from vrc_parameter_relay import __version__  # noqa: E402
from vrc_parameter_relay.core import AppCore  # noqa: E402
from vrc_parameter_relay.store import Store  # noqa: E402

FAILS: list[str] = []


def check(desc: str, cond: bool) -> None:
    print(("OK   " if cond else "FAIL ") + desc, flush=True)
    if not cond:
        FAILS.append(desc)


class StubLink:
    on_param = on_avatar = on_full_sync = on_status = None

    def send_param(self, *a):
        return True

    def refetch(self):
        pass


class StubTunnel:
    state = "off"
    url = None
    error = None
    progress = 0.0

    def __init__(self):
        self.on_change = lambda st: None

    def provider(self):
        return "cloudflare"

    def start(self, port):
        pass

    def stop(self):
        pass

    def restart(self, port):
        pass


class StubFixed:
    state = "off"
    error = None

    def __init__(self, store):
        self.store = store
        self.on_change = lambda st: None

    def url(self):
        return f"https://pages.example/remote/#{'ab' * 32}.{self.store.settings['guest_token']}"

    def start(self):
        self.state = "online"

    def stop(self):
        self.state = "off"


class StubWeb:
    port = 3080
    requested_port = 3080
    loop = None

    def rebind(self, port=None):
        pass


def main() -> int:
    qapp = QApplication.instance() or QApplication([])
    (DATA / "settings.json").write_text(json.dumps({
        "guest_token": "ab" * 16,
        "public_url": "https://relay.example.com",
        "share_password": "hunter2hunter2",
        "last_seen_version": __version__,
    }), "utf-8")

    from vrc_parameter_relay.ui.main_window import MainWindow
    store = Store()
    core = AppCore(store, StubLink())
    win = MainWindow(core, StubTunnel(), StubWeb(), StubFixed(store))
    win.show()
    qapp.processEvents()
    dlg = win.share_dialog

    def fits(desc: str) -> None:
        qapp.processEvents()
        need = dlg.layout().totalHeightForWidth(dlg.width())
        check(f"{desc} ({dlg.height()}px for {need}px of contents)", dlg.height() >= need)

    check("own address: 'Other sharing methods' starts unfolded",
          win.share_method() == "own" and not dlg.more_box.isHidden())
    win._open_share()
    fits("first open is tall enough, no fold clicked yet")

    dlg.admin_btn.click()
    fits("unfolding the admin panel makes room")
    dlg.admin_btn.click()
    folded = dlg.height()
    fits("folding it again still fits")

    dlg.hide()
    qapp.processEvents()
    dlg.admin_btn.click()        # changed while the window is closed
    win._open_share()
    fits("reopened after a change while closed")
    check("...and it grew for the unfolded admin panel", dlg.height() > folded)

    dlg.resize(dlg.width(), dlg.height() + 120)
    qapp.processEvents()
    bigger = dlg.height()
    core.set_sharing(True)       # live state: texts change while it's open
    dlg.refresh_state()
    fits("a state change while open still fits")
    check("...without undoing the user's own resize", dlg.height() >= bigger)

    win._quitting = True
    win.close()
    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
