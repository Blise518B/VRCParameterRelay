"""Hermetic test of sharing on your own address — the one field in the Share window.

Covers: how the typed address is cleaned, the links that come out of it, the
listener opening to the LAN (and back) with a live rebind, which peers may
name a guest's real address, and the Share window driving it all — switching
the tunnel off and back on, the password row, and Enter not pressing a button.

Throwaway data dir, offscreen Qt, no tunnel binary, no mDNS. Safe to run
while VRChat is open. Exits 0 on success.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = tempfile.mkdtemp(prefix="vrcpr-own-")
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from vrc_parameter_relay.core import AppCore
from vrc_parameter_relay.store import Store
from vrc_parameter_relay.webserver import (
    GuestServer, compose_public_url, normalize_public_url,
)


class StubLink:
    on_param = on_avatar = on_full_sync = on_status = None

    def send_param(self, *a):
        return True

    def refetch(self):
        pass


class StubTunnel:
    state = "off"
    url = None

    def __init__(self):
        self.started: list[int] = []
        self.stops = 0
        self.on_change = lambda st: None

    def provider(self):
        return "cloudflare"

    def start(self, port):
        self.started.append(port)
        self.state = "starting"

    def stop(self):
        self.stops += 1
        self.state = "off"

    def restart(self, port):
        self.start(port)


class StubWeb:
    port = 3080
    requested_port = 3080

    def __init__(self):
        self.rebinds = 0

    def rebind(self, port=None):
        self.rebinds += 1


def check(desc: str, cond: bool) -> None:
    if not cond:
        print(f"FAIL {desc}")
        sys.exit(1)
    print(f"OK   {desc}")


def wait_health(port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=2) as res:
                if res.status == 200:
                    return True
        except OSError:
            time.sleep(0.15)
    return False


def main() -> None:
    # -- what the field accepts ---------------------------------------------
    n = normalize_public_url
    check("bare domain -> https", n("relay.example.com") == "https://relay.example.com")
    check("case and trailing slash cleaned",
          n("  HTTPS://Relay.Example.com/ ") == "https://relay.example.com")
    check("default port dropped", n("https://relay.example.com:443") == "https://relay.example.com")
    check("plain http on purpose keeps http + port",
          n("http://myname.duckdns.org:3080") == "http://myname.duckdns.org:3080")
    check("http default port dropped", n("http://myname.duckdns.org:80") == "http://myname.duckdns.org")
    check("a path is dropped (the page lives at the root)",
          n("https://relay.example.com/board?x=1") == "https://relay.example.com")
    check("credentials are never kept", n("https://me:pw@relay.example.com") == "https://relay.example.com")
    check("IPv6 literal with port", n("[2001:db8::1]:8443") == "https://[2001:db8::1]:8443")
    for bad in ("", "   ", "https://", "ftp://relay.example.com", "relay.example.com:notaport",
                "relay.example.com:99999"):
        check(f"rejected: {bad!r}", n(bad) == "")
    check("one-click link", compose_public_url("relay.example.com", "tok")
          == "https://relay.example.com/?k=tok")
    check("no address -> no link", compose_public_url("", "tok") == "")

    # -- the listener follows the setting -------------------------------------
    store = Store()
    core = AppCore(store, StubLink())
    web = GuestServer(core, 0)
    web.start()
    deadline = time.time() + 10
    while not web.port and time.time() < deadline:
        time.sleep(0.1)
    check("web server up", bool(web.port))
    check("no own address -> loopback only", web.bind_host == "127.0.0.1")
    store.set("public_url", "https://relay.example.com")
    old_port = web.port
    web.rebind(0)
    deadline = time.time() + 10
    while (web.bind_host != "*" or web.port == old_port) and time.time() < deadline:
        time.sleep(0.1)
    check("own address -> every interface (dual-stack), live rebind", web.bind_host == "*")
    check("rebound port is live", wait_health(web.port))
    store.set("public_url", "")
    web.rebind(0)
    deadline = time.time() + 10
    while web.bind_host != "127.0.0.1" and time.time() < deadline:
        time.sleep(0.1)
    check("cleared -> back to loopback", web.bind_host == "127.0.0.1")
    check("loopback port live again", wait_health(web.port))

    # -- who may name a guest's real address (X-Forwarded-For) ---------------
    def req(peer, xff=None):
        return SimpleNamespace(remote=peer,
                               headers={"X-Forwarded-For": xff} if xff else {})
    check("a tunnel on this machine is believed",
          web._client_ip(req("127.0.0.1", "203.0.113.5")) == "203.0.113.5")
    check("a reverse proxy on the LAN is believed",
          web._client_ip(req("192.168.1.10", "203.0.113.5")) == "203.0.113.5")
    check("a public peer (straight through a port forward) is NOT",
          web._client_ip(req("93.184.216.34", "203.0.113.5")) == "93.184.216.34")
    check("the proxy's own last entry wins over one the client typed",
          web._client_ip(req("127.0.0.1", "6.6.6.6, 203.0.113.42")) == "203.0.113.42")
    check("no header -> the peer itself", web._client_ip(req("192.168.1.30")) == "192.168.1.30")
    store.set("trusted_proxy", "93.184.216.2")
    check("a configured public proxy is believed too",
          web._client_ip(req("93.184.216.2", "203.0.113.7")) == "203.0.113.7")
    store.set("trusted_proxy", "")

    # -- the Share window ------------------------------------------------------
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from vrc_parameter_relay.ui.dialogs import LinkSettingsDialog
    from vrc_parameter_relay.ui.main_window import MainWindow

    qapp = QApplication.instance() or QApplication([])
    store.set("share_method", "tunnel")   # this suite is about the tunnel path
    core2 = AppCore(store, StubLink())
    tun, web2 = StubTunnel(), StubWeb()
    win = MainWindow(core2, tun, web2)
    dlg = win.share_dialog
    dlg._refresh()
    check("fresh: field empty, the hint offers the own address",
          dlg.own_edit.text() == "" and "your own domain" in dlg.own_hint.text().lower())
    check("fresh: no password row (tunnel links carry the token)", dlg.pw_box.isHidden())

    win.start_sharing()
    check("sharing without an address starts the tunnel", tun.started == [3080])
    tun.state = "online"
    tun.url = "https://random-words.trycloudflare.com"

    dlg.own_edit.setText("Relay.Example.com/")
    dlg.own_edit.editingFinished.emit()
    token = core2.guest_token()
    check("typed address saved in its cleaned form",
          store.settings["public_url"] == "https://relay.example.com")
    check("field shows the cleaned form", dlg.own_edit.text() == "https://relay.example.com")
    check("listener re-bound for the LAN", web2.rebinds == 1)
    check("the tunnel is stopped — the address replaces it",
          tun.stops >= 1 and tun.state == "off")
    check("the handed-out link is the bare address",
          dlg.url_edit.text() == "https://relay.example.com")
    check("password row and one-click button appear",
          not dlg.pw_box.isHidden() and not dlg.oneclick_btn.isHidden()
          and dlg.pw_label.text() == core2.share_password())
    check("one-click link carries the token",
          dlg._oneclick_url() == f"https://relay.example.com/?k={token}")
    check("tray/browser link is the own address",
          win.current_share_url() == f"https://relay.example.com/?k={token}")
    check("hint says where to point it",
          "relay.example.com at http://" in dlg.own_hint.text()
          and ":3080" in dlg.own_hint.text())
    check("live state reads 'Sharing is live'", "live" in dlg.state_label.text())
    check("board button is the emergency stop", win.pause_btn.text() == "■ STOP")

    win.stop_sharing()
    started_before = list(tun.started)
    win.start_sharing()
    check("restarting with an address never touches the tunnel",
          tun.started == started_before and core2.sharing_enabled)

    dlg.own_edit.setText("ftp://nope")
    dlg.own_edit.editingFinished.emit()
    check("nonsense is refused with a warning, setting kept",
          store.settings["public_url"] == "https://relay.example.com"
          and "isn't a web address" in dlg.own_hint.text())

    dlg.own_edit.setText("http://myname.duckdns.org:3080")
    dlg.own_edit.editingFinished.emit()
    check("plain http is allowed but warned about",
          store.settings["public_url"] == "http://myname.duckdns.org:3080"
          and "unencrypted" in dlg.own_hint.text())

    rebinds = web2.rebinds
    dlg.own_edit.setText("http://myname.duckdns.org:3080/")
    dlg.own_edit.editingFinished.emit()
    check("the same address again changes nothing", web2.rebinds == rebinds)

    # Enter applies the field and must not also press a dialog button
    dlg.show()
    qapp.processEvents()
    dlg.own_edit.setFocus()
    sharing_before = core2.sharing_enabled
    dlg.own_edit.setText("relay.example.org")
    QTest.keyClick(dlg.own_edit, Qt.Key_Return)
    qapp.processEvents()
    check("Enter applies the address",
          store.settings["public_url"] == "https://relay.example.org")
    check("...without pressing Start/Pause or Copy",
          core2.sharing_enabled == sharing_before and dlg.copy_btn.text() == "Copy")
    dlg.hide()

    started_before = len(tun.started)
    dlg.own_edit.setText("")
    dlg.own_edit.editingFinished.emit()
    check("emptied -> setting cleared", store.settings["public_url"] == "")
    check("emptied while sharing -> the tunnel comes back",
          len(tun.started) == started_before + 1)
    check("emptied -> password row gone again", dlg.pw_box.isHidden())

    # -- Link settings no longer carries self-hosting ---------------------------
    links = LinkSettingsDialog(win)
    check("Link settings has no self-host section",
          not hasattr(links, "selfhost_radio") and not hasattr(links, "selfhost_box"))
    links.adv_check.setChecked(True)
    links.ngrok_radio.setChecked(False)
    links._save()
    check("saving Link settings keeps the quick tunnel",
          store.settings["tunnel_provider"] == "cloudflare")
    from vrc_parameter_relay.tunnel import Tunnel
    store.set("tunnel_provider", "selfhost")   # left by a pre-release dev build
    check("anything but ngrok is the quick tunnel",
          Tunnel(store).provider() == "cloudflare")

    win._quitting = True
    win.close()
    print("ALL OWN-ADDRESS TESTS PASSED")


if __name__ == "__main__":
    main()
