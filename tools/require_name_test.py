"""Hermetic test of the guests-must-name gate — loopback WebSockets, no mDNS.

Covers: hello carries need_name only for nameless guests while the setting is
on, nameless input is ignored server-side, naming mid-session unlocks input,
and toggling the setting notifies connected guests both ways.
Safe to run while VRChat is open. Exits 0 on success.
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

# isolated settings/profiles so the real app's data is never touched
os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = tempfile.mkdtemp(prefix="vrcpr_nametest_")

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


async def recv_type(ws, wanted: str, timeout: float = 5.0) -> dict:
    """Next message of the wanted type; live param/board traffic is skipped."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"no {wanted!r} message within {timeout}s")
        msg = json.loads((await asyncio.wait_for(ws.receive(), remaining)).data)
        if msg.get("t") == wanted:
            return msg


async def run(core: AppCore, link: StubLink, base: str, token: str) -> None:
    async with aiohttp.ClientSession() as s:
        # -- setting off: no gate ------------------------------------------
        ws = await s.ws_connect(f"{base}/ws?k={token}")
        hello = await recv_type(ws, "hello")
        check("setting off: hello has no need_name", "need_name" not in hello)

        # -- host turns the requirement on ---------------------------------
        core.set_require_names(True)
        check("setting persists", bool(core.store.settings.get("require_guest_names")))
        gate = await recv_type(ws, "need_name")
        check("connected nameless guest is told to name up", gate.get("enabled") is True)

        control_id = core.board["controls"][0]["id"]
        before = len(link.sent)
        await ws.send_json({"t": "set", "id": control_id, "value": True})
        await asyncio.sleep(0.4)
        check("nameless input is ignored server-side", len(link.sent) == before)

        await ws.send_json({"t": "name", "name": "Tester"})
        await asyncio.sleep(0.2)
        await ws.send_json({"t": "set", "id": control_id, "value": True})
        deadline = time.monotonic() + 5
        while len(link.sent) == before and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        check("input works after naming mid-session", len(link.sent) > before)
        await ws.close()

        # -- fresh connections while the setting is on ----------------------
        ws2 = await s.ws_connect(f"{base}/ws?k={token}&n=Alice")
        hello2 = await recv_type(ws2, "hello")
        check("guest arriving with a name gets no gate", "need_name" not in hello2)
        await ws2.close()

        ws3 = await s.ws_connect(f"{base}/ws?k={token}")
        hello3 = await recv_type(ws3, "hello")
        check("nameless guest gets the gate in hello", hello3.get("need_name") is True)

        # -- host turns it off again ----------------------------------------
        core.set_require_names(False)
        ungate = await recv_type(ws3, "need_name")
        check("guests are told when the gate lifts", ungate.get("enabled") is False)
        before = len(link.sent)
        await ws3.send_json({"t": "set", "id": control_id, "value": False})
        deadline = time.monotonic() + 5
        while len(link.sent) == before and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        check("nameless input works again with the setting off", len(link.sent) > before)
        await ws3.close()


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

    # a live avatar with one control so guest input has a target
    core._on_avatar_change("avtr_name_gate_test")
    core.add_control("TestToggle", "toggle")
    core.set_sharing(True)

    asyncio.new_event_loop().run_until_complete(
        run(core, link, f"http://127.0.0.1:{web.port}", core.guest_token()))
    print("ALL REQUIRE-NAME TESTS PASSED")


if __name__ == "__main__":
    main()
