"""Hermetic test of the admin panel — auth boundary and every operation.

Loopback WebSockets only, no mDNS. Safe while VRChat is open. Exits 0 on success.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import aiohttp

from vrc_parameter_relay.core import AppCore
from vrc_parameter_relay.store import Store
from vrc_parameter_relay.webserver import GuestServer


class StubLink:
    on_param = on_avatar = on_full_sync = on_status = None

    def __init__(self) -> None:
        self.sent: list = []

    def send_param(self, name, ptype, value) -> bool:
        self.sent.append((name, ptype, value))
        return True


def check(desc: str, cond: bool) -> None:
    if not cond:
        print(f"FAIL {desc}")
        sys.exit(1)
    print(f"OK   {desc}")


async def first_msg(url: str) -> dict:
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url, timeout=aiohttp.ClientWSTimeout(ws_close=5)) as ws:
            return json.loads((await asyncio.wait_for(ws.receive(), 5)).data)


async def first_msg_h(url: str, headers: dict) -> dict:
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url, headers=headers) as ws:
            return json.loads((await asyncio.wait_for(ws.receive(), 5)).data)


class Admin:
    """One admin session: send an op, get the resulting snapshot."""

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
        error = None
        while True:
            msg = json.loads((await asyncio.wait_for(self.ws.receive(), 5)).data)
            if msg["t"] == "error":
                error = msg["msg"]
            elif msg["t"] == "admin_state" and msg.get("for") == op:
                self.state = msg          # the reply to *this* op, not a
                self.last_error = error   # broadcast queued by an earlier one
                return msg


def main() -> None:
    store = Store()
    link = StubLink()
    core = AppCore(store, link)
    web = GuestServer(core, 0)
    web.start()
    deadline = time.time() + 10
    while not web.port and time.time() < deadline:
        time.sleep(0.1)
    check("web server up", bool(web.port))

    base = f"http://127.0.0.1:{web.port}"
    guest_token = core.guest_token()
    admin_token = core.admin_token()
    check("admin token differs from the guest token", admin_token != guest_token)

    loop = asyncio.new_event_loop()

    # -- the security boundary -------------------------------------------
    denied = loop.run_until_complete(first_msg(f"{base}/ws?admin=1&k={guest_token}"))
    check("a GUEST token cannot open an admin socket", denied.get("t") == "denied")
    denied = loop.run_until_complete(first_msg(f"{base}/ws?admin=1&k=wrong"))
    check("a bogus token cannot open an admin socket", denied.get("t") == "denied")
    hello = loop.run_until_complete(first_msg(f"{base}/ws?k={admin_token}"))
    check("the admin token is not accepted as a guest token", hello.get("t") == "denied")
    admin_hello = loop.run_until_complete(first_msg(f"{base}/ws?admin=1&k={admin_token}"))
    check("the admin token opens an admin socket", admin_hello.get("t") == "admin_state")

    async def page(url: str) -> int:
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                return r.status
    check("/admin refuses a guest token",
          loop.run_until_complete(page(f"{base}/admin?k={guest_token}")) == 403)
    check("/admin serves the panel with the admin token",
          loop.run_until_complete(page(f"{base}/admin?k={admin_token}")) == 200)

    # -- operations --------------------------------------------------------
    core._on_avatar_change("avtr_admin_test")
    for name, ptype, value in [("Hoodie", "Bool", False), ("Bright", "Float", 0.5),
                               ("Outfit", "Int", 2)]:
        core._on_osc_param(name, ptype, value)

    async def run_ops() -> None:
        async with Admin(f"{base}/ws?admin=1&k={admin_token}") as a:
            check("snapshot carries the parameter list", "Hoodie" in a.state["params"])
            check("snapshot carries presets and colours",
                  a.state["presets"] and "green" in a.state["colors"])

            cat = a.state["board"]["categories"][0]["id"]
            st = await a.op("add_control", param="Hoodie", cat=cat)
            check("add_control works", len(st["board"]["controls"]) == 1)
            ctrl = st["board"]["controls"][0]
            check("kind was auto-picked for a Bool", ctrl["kind"] == "toggle")

            st = await a.op("update_control", id=ctrl["id"],
                            changes={"label": "Hood", "invert": True})
            c = st["board"]["controls"][0]
            check("update_control renames and inverts",
                  c["label"] == "Hood" and c["invert"] is True)

            st = await a.op("update_control", id=ctrl["id"],
                            changes={"id": "hax", "param": "Other"})
            check("update_control ignores non-editable fields",
                  st["board"]["controls"][0]["id"] == ctrl["id"]
                  and st["board"]["controls"][0]["param"] == "Hoodie")

            # set_value fires at slider-drag rate: on success it must answer
            # with the cheap param delta ONLY, never a full snapshot
            try:  # the edits above queued broadcasts for every panel — drain them
                while True:
                    await asyncio.wait_for(a.ws.receive(), 0.6)
            except asyncio.TimeoutError:
                pass
            link.sent.clear()
            await a.ws.send_json({"op": "set_value", "id": ctrl["id"], "value": True})
            msg = json.loads((await asyncio.wait_for(a.ws.receive(), 5)).data)
            check("set_value streams the result back as a param batch",
                  msg["t"] == "param_batch"
                  and msg["params"].get("Hoodie", [None])[0] is False)
            check("set_value reaches OSC (inverted -> False)",
                  link.sent == [("Hoodie", "Bool", False)])
            try:
                extra = await asyncio.wait_for(a.ws.receive(), 0.5)
                check(f"a successful set_value sends no snapshot (got {extra.data[:60]!r})",
                      False)
            except asyncio.TimeoutError:
                check("a successful set_value sends no snapshot", True)

            await a.op("set_value", id="no-such-control", value=True)
            check("a failed set_value reports an error and a corrective snapshot",
                  a.last_error is not None)

            st = await a.op("add_category", name="Extras")
            check("add_category works", len(st["board"]["categories"]) == 5)
            new_cat = [c for c in st["board"]["categories"] if c["name"] == "Extras"][0]
            check("new category took an unused colour", new_cat["color"] != "green")

            st = await a.op("move_control", id=ctrl["id"], cat=new_cat["id"], index=0)
            check("move_control moves between categories",
                  st["board"]["controls"][0]["cat"] == new_cat["id"])

            st = await a.op("category_color", cat=new_cat["id"], color="purple")
            check("category_color applies",
                  [c for c in st["board"]["categories"] if c["id"] == new_cat["id"]][0]["color"] == "purple")
            st = await a.op("category_locked", cat=new_cat["id"], locked=True)
            check("category_locked applies",
                  [c for c in st["board"]["categories"] if c["id"] == new_cat["id"]][0]["locked"] is True)
            st = await a.op("rename_category", cat=new_cat["id"], name="Renamed")
            check("rename_category applies",
                  any(c["name"] == "Renamed" for c in st["board"]["categories"]))

            st = await a.op("add_preset", name="Alt")
            check("add_preset switches to the new preset",
                  len(st["presets"]) == 2 and not st["board"]["controls"])
            alt = st["preset_id"]
            first = [p["id"] for p in st["presets"] if p["id"] != alt][0]
            st = await a.op("switch_preset", preset=first)
            check("switch_preset returns to the populated board",
                  st["preset_id"] == first and len(st["board"]["controls"]) == 1)

            st = await a.op("rename_avatar", name="Panel Avatar")
            check("rename_avatar applies", st["avatar_name"] == "Panel Avatar")

            st = await a.op("set_yolo", enabled=True)
            check("set_yolo applies", st["yolo"] is True)
            await a.op("set_yolo", enabled=False)
            st = await a.op("set_sharing", enabled=True)
            check("set_sharing applies", st["sharing"] is True)

            old = core.guest_token()
            await a.op("reset_guest_link")
            check("reset_guest_link rotates the GUEST token", core.guest_token() != old)
            check("…and leaves the admin token alone", core.admin_token() == admin_token)

            st = await a.op("remove_control", id=ctrl["id"])
            check("remove_control works", not st["board"]["controls"])

            # guard rails
            await a.op("remove_preset", preset=st["preset_id"])
            await a.op("remove_preset", preset=core.board["id"])
            check("the last preset cannot be deleted", len(core.list_presets()) == 1)
            for _ in range(6):
                await a.op("remove_category", cat=core.board["categories"][0]["id"])
            check("the last category cannot be deleted", len(core.board["categories"]) == 1)
            st = await a.op("nonsense_op")
            check("unknown ops are reported, not fatal", a.last_error is not None)

    loop.run_until_complete(run_ops())

    # -- a second panel sees another one's edits live ----------------------
    async def two_panels() -> bool:
        async with Admin(f"{base}/ws?admin=1&k={admin_token}") as a:
            async with Admin(f"{base}/ws?admin=1&k={admin_token}") as b:
                await a.op("add_category", name="FromA")
                msg = json.loads((await asyncio.wait_for(b.ws.receive(), 5)).data)
                return any(c["name"] == "FromA" for c in msg["board"]["categories"])
    check("a second panel is updated live", loop.run_until_complete(two_panels()))

    # -- disabling the panel shuts the door --------------------------------
    store.set("admin_enabled", False)
    denied = loop.run_until_complete(first_msg(f"{base}/ws?admin=1&k={admin_token}"))
    check("admin_enabled=False refuses even the right token",
          denied.get("t") == "denied")
    check("…and /admin stops serving",
          loop.run_until_complete(page(f"{base}/admin?k={admin_token}")) == 403)
    store.set("admin_enabled", True)

    # -- LAN-only admin: a proxied (public) request cannot reach admin ------
    # Caddy/cloudflared add X-Forwarded-For; the loopback test client does not,
    # so a forwarding header is what marks a request as "came from the internet".
    async def get_with(url: str, headers: dict) -> int:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers) as r:
                return r.status

    async def ws_denied_with(url: str, headers: dict) -> bool:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(url, headers=headers) as ws:
                msg = json.loads((await asyncio.wait_for(ws.receive(), 5)).data)
                return msg.get("t") == "denied"

    fwd = {"X-Forwarded-For": "203.0.113.7"}  # a request relayed in from the internet
    check("default: a direct local request still opens /admin",
          loop.run_until_complete(page(f"{base}/admin?k={admin_token}")) == 200)
    check("default: a PROXIED request is refused /admin (403)",
          loop.run_until_complete(get_with(f"{base}/admin?k={admin_token}", fwd)) == 403)
    check("default: a PROXIED admin socket is denied even with the right token",
          loop.run_until_complete(
              ws_denied_with(f"{base}/ws?admin=1&k={admin_token}", fwd)))

    store.set("admin_public", True)
    check("admin_public=True: a proxied request reaches /admin again",
          loop.run_until_complete(get_with(f"{base}/admin?k={admin_token}", fwd)) == 200)
    check("admin_public=True: a proxied admin socket connects",
          not loop.run_until_complete(
              ws_denied_with(f"{base}/ws?admin=1&k={admin_token}", fwd)))
    store.set("admin_public", False)

    # the guest board must stay public regardless — only admin is gated.
    # (the guest token was rotated by reset_guest_link earlier, so read it now)
    hello = loop.run_until_complete(
        first_msg_h(f"{base}/ws?k={core.guest_token()}", fwd))
    check("the guest board is still reachable through the proxy",
          hello.get("t") == "hello")

    print("ALL ADMIN PANEL TESTS PASSED")


if __name__ == "__main__":
    main()
