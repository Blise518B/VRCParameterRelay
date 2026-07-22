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


async def ws_first_message(url: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url, timeout=aiohttp.ClientWSTimeout(ws_close=5)) as ws:
            msg = await asyncio.wait_for(ws.receive(), 5)
            return json.loads(msg.data)


async def pause_scenario(url: str, core, ready: asyncio.Event) -> list[dict]:
    """Connect, wait to be paused-then-resumed while holding the socket open."""
    got: list[dict] = []
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            got.append(json.loads((await asyncio.wait_for(ws.receive(), 5)).data))
            ready.set()
            # try to send while paused — must be ignored server-side
            await ws.send_json({"t": "set", "id": "x", "value": True})
            for _ in range(2):  # expect: paused, then resumed
                msg = await asyncio.wait_for(ws.receive(), 10)
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                got.append(json.loads(msg.data))
    return got


def main() -> None:
    store = Store()
    link = StubLink()
    core = AppCore(store, link)
    web = GuestServer(core, 0)  # random free port
    web.start()
    deadline = time.time() + 10
    while not web.port and time.time() < deadline:
        time.sleep(0.1)
    check("web server up", bool(web.port))

    token = core.guest_token()
    url = f"http://127.0.0.1:{web.port}/ws?k={token}"
    loop = asyncio.new_event_loop()

    # sharing off (default): guest still connects, hello says paused=True
    first = loop.run_until_complete(ws_first_message(url))
    check("guest connects while off, hello says paused", first.get("t") == "hello"
          and first.get("paused") is True)

    # enabled -> hello says paused=False
    core.set_sharing(True)
    first = loop.run_until_complete(ws_first_message(url))
    check("hello says not paused once sharing is on", first.get("paused") is False)

    # a connected guest stays connected across pause + resume
    async def run_pause() -> list[dict]:
        ready = asyncio.Event()
        holder = asyncio.create_task(pause_scenario(url, core, ready))
        await asyncio.wait_for(ready.wait(), 5)
        loop_ = asyncio.get_event_loop()
        await asyncio.sleep(0.2)
        link.sent.clear()
        await loop_.run_in_executor(None, core.set_sharing, False)  # pause
        await asyncio.sleep(0.3)
        await loop_.run_in_executor(None, core.set_sharing, True)   # resume
        return await asyncio.wait_for(holder, 10)

    msgs = loop.run_until_complete(run_pause())
    kinds = [m.get("t") for m in msgs[1:]]
    check("held guest got 'paused' then 'resumed'", kinds[:2] == ["paused", "resumed"])
    check("guest input while paused was ignored", link.sent == [])

    # wrong token still 'denied'
    bad = loop.run_until_complete(
        ws_first_message(f"http://127.0.0.1:{web.port}/ws?k=wrong"))
    check("bad token still gets 'denied'", bad.get("t") == "denied")

    print("ALL SHARE-GATE TESTS PASSED")


if __name__ == "__main__":
    main()
