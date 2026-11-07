"""The admin panel's Settings dialog, end to end on a headless relay.

Validation of every field, the set_settings / new_password operations over a
real admin WebSocket, and headless mode applying the changes live: share
method, own address (listener opens to the LAN), ngrok, guest names, web
port (the listener moves), VRChat connection (the link restarts).

Throwaway data dir, loopback only, stub tunnel / fixed link / VRChat link —
no network, no binaries. Exits 0 on success.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = tempfile.mkdtemp(prefix="vrcpr-adminset-")

import aiohttp  # noqa: E402

from vrc_parameter_relay.headless import HeadlessApp  # noqa: E402
from vrc_parameter_relay.store import Store  # noqa: E402
from vrc_parameter_relay.webserver import check_admin_settings  # noqa: E402

FAILS: list[str] = []


def check(desc: str, cond: bool) -> None:
    print(("OK   " if cond else "FAIL ") + desc, flush=True)
    if not cond:
        FAILS.append(desc)


def wait_for(cond, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return bool(cond())


class StubTunnel:
    def __init__(self):
        self.state, self.url, self.starts, self.stops = "off", None, 0, 0
        self.ports: list = []   # the web port each start forwarded to
        self.on_change = lambda st: None

    def provider(self):
        return "cloudflare"

    def start(self, port):
        self.starts += 1
        self.ports.append(port)
        self.state, self.url = "online", "https://quick.trycloudflare.example"

    def stop(self):
        if self.state != "off":
            self.stops += 1
        self.state, self.url = "off", None


class StubFixed:
    def __init__(self, store):
        self.store, self.state, self.starts, self.stops = store, "off", 0, 0
        self.on_change = lambda st: None

    def url(self):
        return f"https://pages.example/remote/#{'ab' * 32}.{self.store.settings['guest_token']}"

    def start(self):
        if self.state != "online":
            self.starts += 1
        self.state = "online"

    def stop(self):
        if self.state != "off":
            self.stops += 1
        self.state = "off"


class StubLink:
    """VrcLink's surface: status + restart, no sockets."""
    on_param = on_avatar = on_full_sync = on_status = None

    def __init__(self, store):
        self.store, self.restarts = store, 0

    def send_param(self, *a):
        return True

    def status(self):
        remote = bool(self.store.settings.get("osc_remote"))
        return {"osc_port": self.store.settings.get("osc_in_port") or 50001,
                "oscquery_port": self.store.settings.get("oscquery_port") or 50002,
                "remote": remote, "bind_host": "0.0.0.0" if remote else "127.0.0.1",
                "port_warnings": []}

    def restart(self):
        self.restarts += 1

    def stop(self):
        pass


class Admin:
    def __init__(self, url: str) -> None:
        self.url = url

    async def __aenter__(self):
        self._s = aiohttp.ClientSession()
        self.ws = await self._s.ws_connect(self.url)
        self.state = json.loads((await asyncio.wait_for(self.ws.receive(), 5)).data)
        return self

    async def __aexit__(self, *exc):
        await self.ws.close()
        await self._s.close()

    async def op(self, op: str, **kw) -> dict:
        await self.ws.send_json({"op": op, **kw})
        self.last_error = None
        while True:
            msg = json.loads((await asyncio.wait_for(self.ws.receive(), 5)).data)
            if msg["t"] == "error":
                self.last_error = msg["msg"]
            elif msg["t"] == "admin_state" and msg.get("for") == op:
                self.state = msg
                return msg

    async def settings(self, **values) -> dict:
        return await self.op("set_settings", settings=values)


def free_tcp_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def validation() -> None:
    cur = {"share_method": "fixed", "tunnel_provider": "cloudflare", "ngrok_authtoken": "",
           "ngrok_domain": "", "web_port": 3080, "osc_in_port": 0, "oscquery_port": 0}
    ok = lambda raw: check_admin_settings(raw, cur)  # noqa: E731
    check("valid: a port as a number", ok({"web_port": 8080}) == ({"web_port": 8080}, None))
    check("valid: unchanged values are dropped", ok({"web_port": 3080}) == ({}, None))
    check("valid: an address without https:// gets it",
          ok({"public_url": "relay.example.com"})[0].get("public_url") == "https://relay.example.com")
    check("valid: OSC port 0 = automatic", ok({"osc_in_port": 0}) == ({}, None))
    for raw, why in (({"web_port": 0}, "web port 0"), ({"web_port": 70000}, "port above 65535"),
                     ({"web_port": 80}, "a port below 1024"),
                     ({"osc_in_port": 9000}, "OSC port 9000 (VRChat's own)"),
                     ({"osc_in_port": 9001}, "OSC port 9001 (VRChat's own)"),
                     ({"oscquery_port": 3080}, "the OSCQuery port = the web port"),
                     ({"web_lan": "true"}, "a LAN switch that isn't true/false"),
                     ({"web_port": "abc"}, "a port that isn't a number"),
                     ({"web_port": True}, "true as a port"),
                     ({"osc_remote": "yes"}, "a non-boolean switch"),
                     ({"share_method": "carrier-pigeon"}, "an unknown share method"),
                     ({"public_url": "not a url at all"}, "an address that isn't one"),
                     ({"admin_public": True}, "admin_public (stays file-only)"),
                     ({"guest_token": "x"}, "the guest token"),
                     ({"ngrok_domain": "x" * 300}, "an overlong domain"),
                     ({"share_method": "tunnel", "tunnel_provider": "ngrok"},
                      "ngrok without authtoken/domain"),
                     ("nope", "a non-object")):
        changes, error = ok(raw)
        check(f"rejected: {why}", bool(error) and changes == {})
    for raw in ({"web_port": 1e400}, {"web_port": "inf"}, {"osc_in_port": "1e999"},
                {"web_port": "abc"}, {"web_port": "nan"}, {"web_port": "8080.9"}):
        changes, error = ok(raw)
        check(f"rejected with a plain message: {raw}",
              bool(error) and "port number" in error and "Overflow" not in error
              and "convert" not in error)
    changes, error = ok({"share_method": "tunnel", "tunnel_provider": "ngrok",
                         "ngrok_authtoken": "tok", "ngrok_domain": "me.ngrok-free.app"})
    check("valid: ngrok with authtoken + domain", error is None and len(changes) == 4)


async def live(app: HeadlessApp, store: Store) -> None:
    url = f"ws://127.0.0.1:{app.web.port}/ws?admin=1&k={app.core.admin_token()}"
    async with Admin(url) as a:
        st = a.state["settings"]
        check("state carries the settings", st["share_method"] == "fixed" and st["web_port"] == app.web.port)
        check("...and the current link (the fixed link)", st["share_link"] == app.fixed.url())
        check("...and the VRChat connection in effect", st["osc_port_now"] == 50001 and not st["remote_now"])
        check("the ngrok authtoken never leaves the server",
              "secret-ngrok-token" not in json.dumps(a.state) and st["ngrok_token_set"] is True)

        before = dict(store.settings)
        await a.settings(web_port=0)
        check("a bad value is refused with a message", bool(a.last_error) and "web port" in a.last_error.lower())
        await a.settings(osc_remote=True, admin_public=True)
        check("one bad key refuses the whole submission",
              bool(a.last_error) and store.settings["osc_remote"] is False
              and store.settings["admin_public"] is False)
        check("...nothing changed", {k: store.settings[k] for k in before} == before)

        # Cloudflare quick tunnel
        await a.settings(share_method="tunnel", tunnel_provider="cloudflare")
        check("switch to the tunnel: no error", a.last_error is None)
        check("headless applies it: fixed link off, tunnel on",
              wait_for(lambda: app.tunnel.starts == 1 and app.fixed.state == "off"))
        check("the panel shows the tunnel link",
              (await a.op("resync"))["settings"]["share_link"].startswith(
                  "https://quick.trycloudflare.example/?k="))

        # own address: listener opens to the LAN, tunnel and fixed link stop
        await a.settings(public_url="relay.example.com")
        check("own address stored with https://", store.settings["public_url"] == "https://relay.example.com")
        check("headless applies it: tunnel off", wait_for(lambda: app.tunnel.state == "off"))
        check("...and the listener opens to every interface", wait_for(lambda: app.web.bind_host == "*"))
        check("the panel shows the own address",
              (await a.op("resync"))["settings"]["share_link"].startswith("https://relay.example.com/?k="))

        # back to the fixed link
        await a.settings(public_url="", share_method="fixed")
        check("back to the fixed link", wait_for(lambda: app.fixed.state == "online"))
        check("...and the listener back on loopback", wait_for(lambda: app.web.bind_host == "127.0.0.1"))

        # guest names: stored and announced to guests
        events: list = []
        app.core.add_listener(lambda e: events.append(e))
        await a.settings(require_guest_names=True)
        check("guest names: stored", store.settings["require_guest_names"] is True)
        check("...and announced to connected guests",
              any(e["t"] == "require_names" and e["enabled"] for e in events))

        # VRChat connection: the link restarts once
        await a.settings(osc_remote=True, osc_in_port=9101, oscquery_port=9102)
        check("VRChat connection stored",
              store.settings["osc_remote"] is True and store.settings["osc_in_port"] == 9101
              and store.settings["oscquery_port"] == 9102)
        check("...and the VRChat link restarts once", wait_for(lambda: app.link.restarts == 1))

        # new password
        old_pw = a.state["settings"]["share_password"]
        await a.op("new_password")
        check("new password", a.state["settings"]["share_password"] not in ("", old_pw))

        # web port: the listener moves, this admin socket stays connected
        new_port = free_tcp_port()
        await a.settings(web_port=new_port)
        check("web port stored", store.settings["web_port"] == new_port)
        check("the reply already names the new port (moved before answering)",
              a.state["settings"]["web_port_now"] == new_port and app.web.port == new_port)
        st = (await a.op("resync"))["settings"]
        check("the open admin socket keeps working", st["web_port_now"] == new_port)

        # a web port change while a tunnel is up: the tunnel follows it
        await a.settings(share_method="tunnel", tunnel_provider="cloudflare")
        check("(tunnel up)", wait_for(lambda: app.tunnel.state == "online"))
        starts = app.tunnel.starts
        port2 = free_tcp_port()
        await a.settings(web_port=port2)
        check("tunnel restarted after the web port changed",
              wait_for(lambda: app.tunnel.starts == starts + 1))
        check("...and forwards to the new port", app.tunnel.ports[-1] == port2 == app.web.port)

        # a port that's taken: the server falls back, and the reply says where
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        taken = blocker.getsockname()[1]
        await a.settings(web_port=taken)
        actual = a.state["settings"]["web_port_now"]
        check("a taken web port: the reply names the port really in use",
              actual not in (taken, port2) and actual == app.web.port)
        check("...and the tunnel forwards there, not to the taken port",
              wait_for(lambda: app.tunnel.ports[-1] == actual))
        blocker.close()

        # after that fallback the port is still "wrong": the next rebind (a
        # LAN toggle) moves it back to the asked-for one — the tunnel follows
        tunnel_ports = len(app.tunnel.ports)
        await a.settings(web_lan=True)
        check("a LAN toggle that moves the port back: the tunnel follows it",
              wait_for(lambda: len(app.tunnel.ports) == tunnel_ports + 1)
              and app.tunnel.ports[-1] == app.web.port == taken)
        await a.settings(web_lan=False)

        # a rebind that fails completely: a message, and the rest still applies
        events.clear()
        real_start = app.web._start_site

        async def broken():
            raise OSError(98, "Address already in use")
        app.web._start_site = broken
        await a.settings(web_lan=True, require_guest_names=False)
        app.web._start_site = real_start
        check("a failed rebind is reported", bool(a.last_error) and "couldn't listen" in a.last_error)
        check("...and the other changes still apply",
              store.settings["require_guest_names"] is False
              and any(e["t"] == "settings_changed" for e in events))
        await a.settings(web_lan=False)   # listening again
        check("...and a later save recovers the listener", app.web.bind_host == "127.0.0.1")

        # two rebinds at the same moment (an admin Save and a desktop Save):
        # no orphaned listener left answering on the LAN afterwards
        store.set("web_lan", True)
        first, second = app.web.rebind(), app.web.rebind()
        check("overlapping rebinds both finish", wait_for(lambda: app.web.bound_seq >= second))
        await a.settings(web_lan=False)
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        lan = probe.getsockname()[0]
        probe.close()
        reachable = socket.socket()
        reachable.settimeout(2)
        try:
            reachable.connect((lan, app.web.port))
            on_lan = True
        except OSError:
            on_lan = False
        finally:
            reachable.close()
        check("...and after LAN off nothing still answers on the LAN address",
              app.web.bind_host == "127.0.0.1" and not on_lan)

        # the LAN switch opens the listener to every interface
        await a.settings(web_lan=True)
        check("web_lan on: the listener opens to every interface (in the reply)",
              a.state["settings"]["web_bind"] == "*" and a.state["settings"]["web_lan"] is True)
        await a.settings(web_lan=False)
        check("web_lan off: back on loopback", a.state["settings"]["web_bind"] == "127.0.0.1")


def main() -> int:
    validation()

    store = Store()
    store.set("ngrok_authtoken", "secret-ngrok-token")
    store.set("web_port", free_tcp_port())
    app = HeadlessApp(store)
    app.tunnel = StubTunnel()
    app.fixed = StubFixed(store)
    app.link = app.core.link = StubLink(store)
    app.start(start_link=False)
    check("headless starts on the fixed link", app.fixed.state == "online" and app.tunnel.starts == 0)
    try:
        asyncio.run(live(app, store))
    finally:
        app.stop_event.set()

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
