"""The fixed link end to end, over real iroh (needs internet: n0's relays).

A Python iroh client plays the browser (docs/remote/ + app.js): it dials
the relay by its endpoint ID, opens the stream, joins with the token and
drives the board — against the real GuestServer and FixedLink. Also checks
the identity survives a restart, the settings default (new installs get the
fixed link, updates keep their tunnel), pause/resume, a wrong token, Reset
link and a full board.

Throwaway data dir, a stub OSC link (nothing reaches VRChat, no mDNS).
Exits 0 on success.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
DATA = tempfile.mkdtemp(prefix="vrcpr-fixed-")
os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = DATA

import iroh  # noqa: E402

from vrc_parameter_relay import __version__  # noqa: E402
from vrc_parameter_relay import fixed_link as fl  # noqa: E402
from vrc_parameter_relay import webserver  # noqa: E402
from vrc_parameter_relay.core import AppCore  # noqa: E402
from vrc_parameter_relay.store import Store  # noqa: E402

FAILS: list[str] = []


def check(desc: str, cond: bool) -> None:
    print(("OK   " if cond else "FAIL ") + desc, flush=True)
    if not cond:
        FAILS.append(desc)


class StubLink:
    on_param = on_avatar = on_full_sync = on_status = None

    def __init__(self) -> None:
        self.sent: list = []

    def send_param(self, name, ptype, value) -> bool:
        self.sent.append((name, ptype, value))
        return True


def settings_defaults() -> None:
    fresh = Store()
    check("a new install shares with the fixed link",
          fresh.settings["share_method"] == "fixed" and fresh.fresh_install)
    check("a new install has nothing new to show",
          fresh.settings["last_seen_version"] == __version__)
    old = Path(tempfile.mkdtemp(prefix="vrcpr-upgrade-"))
    (old / "settings.json").write_text(json.dumps(
        {"guest_token": "ab" * 16, "tunnel_provider": "cloudflare"}), "utf-8")
    os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = str(old)
    upgraded = Store()
    os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = DATA
    check("an update keeps the Cloudflare tunnel it had",
          upgraded.settings["share_method"] == "tunnel" and not upgraded.fresh_install)
    check("an update shows What's new once", upgraded.settings["last_seen_version"] == "")
    check("the kept choice is written back",
          json.loads((old / "settings.json").read_text("utf-8"))["share_method"] == "tunnel")


async def write_frame(send, obj) -> None:
    data = json.dumps(obj).encode()
    await send.write_all(len(data).to_bytes(4, "big") + data)


async def expect(recv, kind: str, timeout: float = 10.0) -> dict:
    """The next frame of `kind`, skipping the chatter in between."""
    async def read():
        while True:
            raw = await fl.read_frame(recv, 4 * 1024 * 1024)
            msg = json.loads(raw)
            if msg.get("t") == kind:
                return msg
    return await asyncio.wait_for(read(), timeout)


class Browser:
    """What the guest page does, minus the page."""

    def __init__(self, target: str) -> None:
        self.target = target

    async def join(self, token: str, name: str = "") -> None:
        self.ep = await iroh.Endpoint.bind(iroh.EndpointOptions(preset=iroh.preset_n0()))
        addr = iroh.EndpointAddr(iroh.EndpointId.from_string(self.target), None, [])
        self.conn = await asyncio.wait_for(self.ep.connect(addr, fl.ALPN), 20)
        bi = await self.conn.open_bi()
        self.send, self.recv = bi.send(), bi.recv()
        await write_frame(self.send, {"t": "join", "k": token, "n": name, "v": 1})

    async def closed_reason(self, timeout: float = 5.0) -> str:
        return str(await asyncio.wait_for(self.conn.closed(), timeout))

    async def leave(self) -> None:
        try:
            self.conn.close(0, b"bye")
            await asyncio.wait_for(self.ep.close(), 2)
        except Exception:
            pass


def main() -> int:
    settings_defaults()

    store = Store()
    link = StubLink()
    core = AppCore(store, link)
    web = webserver.GuestServer(core, 0)
    web.start()
    deadline = time.time() + 10
    while not web.port and time.time() < deadline:
        time.sleep(0.05)
    check("web server bound", bool(web.port))
    core._on_avatar_change("avtr_fixed_test")
    core._on_osc_param("Hoodie", "Bool", False)
    control = core.add_control("Hoodie", "toggle")
    core.set_sharing(True)

    states: list[str] = []
    fixed = fl.FixedLink(store, web)
    fixed.on_change = lambda st: states.append(st["state"])
    before = fixed.endpoint_id()
    check("the endpoint ID is known before going online", len(before) == 64)
    check("the link is page#id.token",
          fixed.url() == f"{fl.PAGE_URL}#{before}.{core.guest_token()}")
    check("the key lives in its own file, not settings.json",
          (store.dir / fl.KEY_FILE).stat().st_size == 32
          and before not in (store.dir / "settings.json").read_text("utf-8"))
    check("a restart keeps the same link", fl.FixedLink(store, web).endpoint_id() == before)

    fixed.start()
    deadline = time.time() + 45
    while fixed.state != "online" and time.time() < deadline:
        time.sleep(0.2)
    check(f"online through iroh's relays (states {states})", fixed.state == "online")
    check("online under the same ID", fixed.endpoint_id() == before)

    def run(coro, timeout: float = 30.0):
        return asyncio.run_coroutine_threadsafe(coro, web.loop).result(timeout)

    # -- a guest joins and drives the board --------------------------------------
    guest = Browser(before)
    run(guest.join(core.guest_token(), "Tester"))
    hello = run(expect(guest.recv, "hello"))
    check("the guest gets the board's hello",
          [c["id"] for c in hello["board"]["controls"]] == [control["id"]])
    time.sleep(0.3)
    check("the guest is counted and named", list(web._names.values()) == ["Tester"])
    run(write_frame(guest.send, {"t": "set", "id": control["id"], "value": True}))
    deadline = time.time() + 5
    while not link.sent and time.time() < deadline:
        time.sleep(0.05)
    check("a guest's toggle reaches VRChat", link.sent == [("Hoodie", "Bool", True)])
    echo = run(expect(guest.recv, "param_batch"))
    check("the change echoes back", echo["params"].get("Hoodie", [None])[0] is True)

    core.set_sharing(False)
    run(expect(guest.recv, "paused"))
    run(write_frame(guest.send, {"t": "set", "id": control["id"], "value": False}))
    time.sleep(0.5)
    check("paused: the guest stays, its input is ignored", len(link.sent) == 1
          and len(web._sockets) == 1)
    core.set_sharing(True)
    check("resumed", run(expect(guest.recv, "resumed"))["paused"] is False)

    # -- a wrong token ------------------------------------------------------------
    stranger = Browser(before)
    run(stranger.join("00" * 16))
    denied = run(expect(stranger.recv, "denied"))
    check("a wrong token is denied (why: link)", denied.get("why") == "link")
    check("...and the close says so too", "denied link" in run(stranger.closed_reason()))
    check("...and never counts as a guest", len(web._sockets) == 1)
    run(stranger.leave())

    # -- Reset link -----------------------------------------------------------------
    old_url = fixed.url()
    old_token = core.guest_token()
    core.regenerate_token()
    run(expect(guest.recv, "denied"))
    check("Reset link kicks the guest, with the reason",
          "denied link" in run(guest.closed_reason()))
    check("Reset link gives a new link, same ID",
          fixed.url() != old_url and fixed.endpoint_id() == before)
    run(guest.leave())
    late = Browser(before)
    run(late.join(old_token))
    check("the old link is dead", run(expect(late.recv, "denied")).get("why") == "link")
    run(late.leave())
    fresh = Browser(before)
    run(fresh.join(core.guest_token()))
    check("the new link works", "board" in run(expect(fresh.recv, "hello")))

    # -- a full board ----------------------------------------------------------------
    real_max = webserver.MAX_GUESTS
    webserver.MAX_GUESTS = 1
    crowd = Browser(before)
    run(crowd.join(core.guest_token()))
    check("a full board says so (why: full)",
          run(expect(crowd.recv, "denied")).get("why") == "full")
    webserver.MAX_GUESTS = real_max
    run(crowd.leave())
    run(fresh.leave())

    fixed.stop()
    check("stops cleanly", fixed.state == "off")
    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
