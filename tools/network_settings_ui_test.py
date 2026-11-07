"""Desktop UI for the VRChat connection and ports, and admin-panel changes
reaching the desktop window.

Link settings → "VRChat on another device & ports": folded away by default,
opens by itself once something in it isn't the default, saves what changed
and restarts the VRChat link / moves the web listener only when needed, shows
what is in effect (and a taken port). The main window applies settings the
admin panel changed (settings_changed) like its own dialogs would.

Throwaway data dir, offscreen Qt, stubs: no network. Exits 0 on success.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = tempfile.mkdtemp(prefix="vrcpr-netui-")
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


def wait_for(cond, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return bool(cond())


class StubLink:
    on_param = on_avatar = on_full_sync = on_status = None

    def __init__(self, store):
        self.store, self.restarts, self.warnings = store, 0, []

    def send_param(self, *a):
        return True

    def refetch(self):
        pass

    def status(self):
        remote = bool(self.store.settings.get("osc_remote"))
        return {"osc_port": self.store.settings.get("osc_in_port") or 50001,
                "oscquery_port": self.store.settings.get("oscquery_port") or 50002,
                "remote": remote, "bind_host": "0.0.0.0" if remote else "127.0.0.1",
                "port_warnings": list(self.warnings), "send_target": "127.0.0.1:9000"}

    def restart(self):
        self.restarts += 1


class StubTunnel:
    state, url = "off", None

    def __init__(self):
        self.starts = 0
        self.ports: list = []   # the web port each start forwarded to
        self.on_change = lambda st: None

    def provider(self):
        return "cloudflare"

    def start(self, port):
        self.starts += 1
        self.ports.append(port)
        self.state = "starting"

    def stop(self):
        self.state = "off"

    def restart(self, port):
        self.stop()
        self.start(port)


class StubFixed:
    def __init__(self, store):
        self.store, self.state, self.error = store, "off", None
        self.on_change = lambda st: None

    def url(self):
        return f"https://pages.example/remote/#{'ab' * 32}.{self.store.settings['guest_token']}"

    def start(self):
        self.state = "online"

    def stop(self):
        self.state = "off"


class StubWeb:
    """Rebinds 'asynchronously' like the real one: the port changes a moment
    later, on the next event-loop turn."""
    port = requested_port = 3080
    loop = None
    bind_host = "127.0.0.1"

    def __init__(self):
        self.rebinds: list = []
        self.seq = self.bound_seq = 0

    def rebind(self, port=None):
        self.rebinds.append(port)
        self.seq += 1
        seq = self.seq
        from PySide6.QtCore import QTimer

        def done():
            if port:
                self.port = self.requested_port = port
            self.bound_seq = max(self.bound_seq, seq)
        QTimer.singleShot(150, done)
        return seq


def settle(web) -> None:
    """Let a Save finish: its rebind done, the steps waiting on it run."""
    wait_for(lambda: web.bound_seq >= web.seq, 3)
    wait_for(lambda: False, 0.4)


def main() -> int:
    qapp = QApplication.instance() or QApplication([])
    from vrc_parameter_relay.ui import dialogs
    from vrc_parameter_relay.ui.dialogs import LinkSettingsDialog
    warnings: list = []   # QMessageBox.warning would block an offscreen test
    dialogs.QMessageBox.warning = lambda parent, title, text, *a: warnings.append(text)
    from vrc_parameter_relay.ui.main_window import MainWindow

    store = Store()
    store.set("last_seen_version", __version__)
    link = StubLink(store)
    core = AppCore(store, link)
    tunnel, fixed, web = StubTunnel(), StubFixed(store), StubWeb()
    win = MainWindow(core, tunnel, web, fixed)
    win.show()
    qapp.processEvents()

    # -- Link settings: the new section --------------------------------------
    dlg = LinkSettingsDialog(win)
    dlg.show()
    qapp.processEvents()
    check("defaults: the section starts folded", dlg.net_box.isHidden()
          and dlg.net_btn.text().startswith("▸"))
    dlg.net_btn.click()
    qapp.processEvents()
    check("...and opens on click", not dlg.net_box.isHidden() and dlg.net_btn.text().startswith("▾"))
    check("the title escapes & (a single & would become a shortcut underline)",
          "Network && ports" in dlg.net_btn.text())
    check("defaults: same PC only, automatic ports, web port 3080",
          not dlg.remote_check.isChecked() and dlg.osc_port_spin.text() == "automatic"
          and dlg.oq_port_spin.text() == "automatic" and dlg.web_port_spin.value() == 3080)
    check("it shows what's in effect", "this PC only" in dlg.net_status.text()
          and "50001" in dlg.net_status.text())
    check("the window fits the open section",
          dlg.height() >= dlg.layout().totalHeightForWidth(dlg.width()) - 1)
    dlg._save()
    check("saving untouched: no VRChat restart, no rebind",
          link.restarts == 0 and web.rebinds == [])

    # the section opened before the window is shown (sized while hidden)
    dlg = LinkSettingsDialog(win)
    dlg.adv_check.setChecked(True)
    dlg.net_btn.click()
    dlg.show()
    qapp.processEvents()
    check("opened before showing: still tall enough, nothing squeezed",
          dlg.height() >= dlg.layout().totalHeightForWidth(dlg.width()) - 1)
    check("...and the port boxes keep their height",
          dlg.osc_port_spin.height() >= dlg.osc_port_spin.minimumSizeHint().height())
    dlg.reject()

    dlg = LinkSettingsDialog(win)
    dlg.net_btn.click()
    dlg.remote_check.setChecked(True)
    dlg.osc_port_spin.setValue(9101)
    dlg.oq_port_spin.setValue(9102)
    dlg.web_port_spin.setValue(8081)
    dlg._save()
    check("saved: other devices + fixed ports",
          store.settings["osc_remote"] is True and store.settings["osc_in_port"] == 9101
          and store.settings["oscquery_port"] == 9102)
    check("...the VRChat link restarts once (off the UI thread)", wait_for(lambda: link.restarts == 1))
    check("...web port saved and the listener moves", store.settings["web_port"] == 8081
          and web.rebinds == [8081])
    wait_for(lambda: web.port == 8081)

    # port problems are refused with a message, nothing saved
    for spin, value, why in (("osc_port_spin", 9000, "VRChat's own"),
                             ("oq_port_spin", 8081, "can't be the same"),
                             ("osc_port_spin", 80, "1024 or higher")):
        dlg = LinkSettingsDialog(win)
        getattr(dlg, spin).setValue(value)
        warnings.clear()
        restarts_before = link.restarts
        dlg._save()
        check(f"refused: {spin} = {value}", warnings and why in warnings[-1]
              and dlg.isVisible() is False and link.restarts == restarts_before
              and store.settings["osc_in_port"] == 9101)
        dlg.reject()

    # LAN switch: the listener moves (no new port)
    dlg = LinkSettingsDialog(win)
    dlg.lan_check.setChecked(True)
    dlg._save()
    check("web server reachable from the LAN: saved, the listener rebinds",
          store.settings["web_lan"] is True and web.rebinds[-1] is None)
    settle(web)

    # a web port change while the tunnel is up: it restarts on the new port
    store.set("share_method", "tunnel")
    win.start_sharing()
    check("(tunnel up)", wait_for(lambda: tunnel.starts >= 1))
    tunnel.state = "online"
    starts = tunnel.starts
    dlg = LinkSettingsDialog(win)
    dlg.web_port_spin.setValue(8090)
    dlg._save()
    check("tunnel restarts once the listener is on the new port",
          wait_for(lambda: tunnel.starts == starts + 1, 4))
    check("...and forwards to 8090", tunnel.ports[-1] == 8090 == web.port)

    # one Save that switches to the tunnel AND moves the port: one start, new port
    store.set("share_method", "fixed")
    win.apply_link_settings()
    tunnel.state = "off"
    starts = tunnel.starts
    dlg = LinkSettingsDialog(win)
    dlg.cf_radio.setChecked(True)
    dlg.web_port_spin.setValue(8091)
    dlg._save()
    check("switch to the tunnel + new port in one Save: the tunnel waits for the listener",
          tunnel.starts == starts)
    check("...then starts once, on the new port",
          wait_for(lambda: tunnel.starts == starts + 1, 4) and tunnel.ports[-1] == 8091
          and (QApplication.processEvents() or True) and tunnel.starts == starts + 1)

    # a low port set in settings.json on purpose survives an unrelated Save
    store.set("web_port", 80)
    web.port = 80
    rebinds = len(web.rebinds)
    dlg = LinkSettingsDialog(win)
    check("a stored web port 80 shows as 80 (not clamped)", dlg.web_port_spin.value() == 80)
    warnings.clear()
    dlg._save()
    check("...and saving without touching it is fine: no warning, no move",
          not warnings and len(web.rebinds) == rebinds and store.settings["web_port"] == 80)
    store.set("web_port", 8091)
    web.port = 8091

    link.warnings = ["OSC port 9101 is in use — using a random port"]
    dlg = LinkSettingsDialog(win)
    check("reopened: the section opens by itself (not the defaults)", not dlg.net_box.isHidden())
    check("...with the saved values", dlg.remote_check.isChecked()
          and dlg.osc_port_spin.value() == 9101 and dlg.web_port_spin.value() == 8091
          and dlg.lan_check.isChecked())
    check("...says it listens on the network", "your network" in dlg.net_status.text())
    check("...and shows a taken port", "in use" in dlg.net_status.text())
    dlg.reject()

    # -- admin panel changes reach the window ---------------------------------
    # (the web server moves its own listener before it tells the window)
    restarts, rebinds = link.restarts, len(web.rebinds)
    store.set("share_method", "fixed")
    win.apply_link_settings()
    store.set("share_method", "tunnel")
    core.emit({"t": "settings_changed", "keys": ["share_method"]})
    check("admin switched to the tunnel: the window starts it",
          wait_for(lambda: tunnel.state != "off" and fixed.state == "off"))
    tunnel.state = "online"
    starts = tunnel.starts
    web.port = 8095   # the server already moved
    store.set("web_port", 8095)
    core.emit({"t": "settings_changed", "keys": ["web_port"]})
    check("admin changed the web port: the tunnel restarts on it",
          wait_for(lambda: tunnel.starts == starts + 1) and tunnel.ports[-1] == 8095)
    store.set("public_url", "https://relay.example.com")
    core.emit({"t": "settings_changed", "keys": ["public_url"]})
    check("admin set an own address: the tunnel stops, no second rebind",
          wait_for(lambda: tunnel.state == "off") and len(web.rebinds) == rebinds)
    check("...and the Share window follows",
          wait_for(lambda: "relay.example.com" in win.share_dialog.url_edit.text()))
    store.set("osc_in_port", 9201)
    core.emit({"t": "settings_changed", "keys": ["osc_in_port"]})
    check("admin changed an OSC port: the VRChat link restarts",
          wait_for(lambda: link.restarts == restarts + 1))
    check("the status bar shows where OSC listens", wait_for(
        lambda: (win._update_vrc_chip(link.status()) or True)
        and "0.0.0.0:9201" in win.statusBar().currentMessage()))

    win._quitting = True
    win.close()
    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
