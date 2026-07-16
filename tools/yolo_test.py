"""Hermetic YOLO-mode test — loopback WS only, no mDNS/OSC sockets.

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
        self.sent: list[tuple] = []

    def send_param(self, name, ptype, value) -> bool:
        self.sent.append((name, ptype, value))
        return True


def check(desc: str, cond: bool) -> None:
    if not cond:
        print(f"FAIL {desc}")
        sys.exit(1)
    print(f"OK   {desc}")


async def recv_until(ws, predicate, timeout: float = 5.0) -> dict:
    """Read frames (skipping interleaved param broadcasts) until one matches."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = await asyncio.wait_for(ws.receive(), deadline - time.time())
        data = json.loads(msg.data)
        if predicate(data):
            return data
    raise TimeoutError("expected message never arrived")


async def scenario(url: str, core: AppCore, link: StubLink) -> None:
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            hello = json.loads((await asyncio.wait_for(ws.receive(), 5)).data)
            check("hello has yolo=False and no params", hello["t"] == "hello"
                  and hello["yolo"] is False and "params" not in hello)

            # setp must be ignored while yolo is off
            await ws.send_json({"t": "setp", "name": "SecretParam", "value": 1.0})
            await asyncio.sleep(0.4)
            check("setp ignored while yolo off", link.sent == [])

            # enable -> guests get the full list
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, core.set_yolo, True)
            msg = await recv_until(ws, lambda m: m["t"] == "yolo")
            check("yolo broadcast with params",
                  msg["enabled"] and "SecretParam" in msg["params"])

            # setp works now, with clamping
            await ws.send_json({"t": "setp", "name": "SecretParam", "value": 5.0})
            await asyncio.sleep(0.4)
            check("float clamped to 1.0", ("SecretParam", "Float", 1.0) in link.sent)
            await ws.send_json({"t": "setp", "name": "SomeInt", "value": 999})
            await asyncio.sleep(0.4)
            check("int clamped to 255", ("SomeInt", "Int", 255) in link.sent)
            await ws.send_json({"t": "setp", "name": "DoesNotExist", "value": 1})
            await asyncio.sleep(0.4)
            check("unknown param rejected",
                  not any(n == "DoesNotExist" for n, _, _ in link.sent))

            # param stream now includes non-board params
            await loop.run_in_executor(
                None, core._on_osc_param, "SecretParam", "Float", 0.42)
            msg = await recv_until(
                ws, lambda m: m["t"] == "param" and m["name"] == "SecretParam"
                and m["value"] == 0.42)
            check("non-board param streamed to guest", msg["ptype"] == "Float")

            # disable -> guests told, setp dead again
            await loop.run_in_executor(None, core.set_yolo, False)
            msg = await recv_until(ws, lambda m: m["t"] == "yolo")
            check("yolo-off broadcast", not msg["enabled"])
            link.sent.clear()
            await ws.send_json({"t": "setp", "name": "SecretParam", "value": 0.1})
            await asyncio.sleep(0.4)
            check("setp ignored again after disable", link.sent == [])


def main() -> None:
    store = Store()
    link = StubLink()
    core = AppCore(store, link)
    core.set_yolo(False)  # yolo persists in settings — start from a known state
    core._on_avatar_change("avtr_deadbeef-0000-4000-8000-c0ffee000001")
    core._on_osc_param("SecretParam", "Float", 0.0)
    core._on_osc_param("SomeInt", "Int", 0)
    core.set_sharing(True)

    web = GuestServer(core, 0)
    web.start()
    deadline = time.time() + 10
    while not web.port and time.time() < deadline:
        time.sleep(0.1)
    check("web server up", bool(web.port))

    url = f"http://127.0.0.1:{web.port}/ws?k={core.guest_token()}"
    asyncio.new_event_loop().run_until_complete(scenario(url, core, link))

    # persistence: yolo state survives an app restart
    core.set_yolo(True)
    restarted = AppCore(Store(), StubLink())
    check("yolo persists across restart", restarted.yolo_enabled is True)
    core.set_yolo(False)
    restarted = AppCore(Store(), StubLink())
    check("yolo-off persists too", restarted.yolo_enabled is False)

    print("ALL YOLO TESTS PASSED")


if __name__ == "__main__":
    main()
