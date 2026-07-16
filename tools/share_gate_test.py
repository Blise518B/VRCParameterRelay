"""Hermetic test of the sharing pause/resume gate — loopback WS only, no mDNS.

Safe to run while VRChat is open. Exits 0 on success.
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
    def send_param(self, *a): return True


def check(desc: str, cond: bool) -> None:
    if not cond:
        print(f"FAIL {desc}")
        sys.exit(1)
    print(f"OK   {desc}")


async def ws_first_message(url: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url, timeout=aiohttp.ClientWSTimeout(ws_close=5)) as ws:
            msg = await asyncio.wait_for(ws.receive(), 5)
            return json.loads(msg.data)


async def ws_hold_until_kicked(url: str, on_open: asyncio.Event) -> list[dict]:
    got: list[dict] = []
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            first = await asyncio.wait_for(ws.receive(), 5)
            got.append(json.loads(first.data))
            on_open.set()
            while True:
                msg = await asyncio.wait_for(ws.receive(), 10)
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE,
                                aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                    break
                got.append(json.loads(msg.data))
    return got


def main() -> None:
    store = Store()
    core = AppCore(store, StubLink())
    web = GuestServer(core, 0)  # random free port
    web.start()
    deadline = time.time() + 10
    while not web.port and time.time() < deadline:
        time.sleep(0.1)
    check("web server up", bool(web.port))

    token = core.guest_token()
    url = f"http://127.0.0.1:{web.port}/ws?k={token}"

    loop = asyncio.new_event_loop()

    # sharing disabled (default) -> valid token gets "paused"
    first = loop.run_until_complete(ws_first_message(url))
    check("guest gets 'paused' while sharing is off", first.get("t") == "paused")

    # enabled -> hello
    core.set_sharing(True)
    first = loop.run_until_complete(ws_first_message(url))
    check("guest gets 'hello' once sharing is on", first.get("t") == "hello")

    # connected guest gets kicked with 'paused' when host pauses
    async def kick_scenario() -> list[dict]:
        opened = asyncio.Event()
        holder = asyncio.create_task(ws_hold_until_kicked(url, opened))
        await asyncio.wait_for(opened.wait(), 5)
        await asyncio.get_event_loop().run_in_executor(None, core.set_sharing, False)
        return await asyncio.wait_for(holder, 10)

    messages = loop.run_until_complete(kick_scenario())
    check("held guest received 'paused' kick",
          any(m.get("t") == "paused" for m in messages[1:]))

    # wrong token still 'denied', not 'paused'
    core.set_sharing(True)
    bad = loop.run_until_complete(
        ws_first_message(f"http://127.0.0.1:{web.port}/ws?k=wrong"))
    check("bad token still gets 'denied'", bad.get("t") == "denied")

    print("ALL SHARE-GATE TESTS PASSED")


if __name__ == "__main__":
    main()
