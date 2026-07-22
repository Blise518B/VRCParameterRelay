"""Hermetic test of avatar-switch sync logic — local HTTP only, no mDNS/OSC.

Safe to run while VRChat is open. Covers:
  * stale OSCQuery tree after /avatar/change (no flip-back, retries until fresh)
  * poll fallback adopting the tree's avatar when /avatar/change was missed
  * core only broadcasting params_reset when the parameter set changed
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vrc_parameter_relay.core import AppCore
from vrc_parameter_relay.osc_link import VrcLink
from vrc_parameter_relay.store import Store


class FakeTree:
    """Controllable OSCQuery HTTP endpoint."""

    def __init__(self) -> None:
        self.avatar = "avtr_A"
        self.params = {"Alpha": ("T", True)}
        handler = type("H", (Handler,), {"holder": self})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tree(self) -> dict:
        nodes = {
            n: {"FULL_PATH": f"/avatar/parameters/{n}", "TYPE": t, "ACCESS": 3, "VALUE": [v]}
            for n, (t, v) in self.params.items()
        }
        return {"FULL_PATH": "/", "CONTENTS": {"avatar": {"FULL_PATH": "/avatar", "CONTENTS": {
            "change": {"FULL_PATH": "/avatar/change", "TYPE": "s", "VALUE": [self.avatar]},
            "parameters": {"FULL_PATH": "/avatar/parameters", "CONTENTS": nodes},
        }}}}


class Handler(BaseHTTPRequestHandler):
    holder: FakeTree = None

    def do_GET(self) -> None:  # noqa: N802
        if "HOST_INFO" in self.path:
            body = {"NAME": "VRChat-Client-TEST", "OSC_IP": "127.0.0.1",
                    "OSC_PORT": 9, "OSC_TRANSPORT": "UDP"}  # discard port
        else:
            body = self.holder.tree()
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a) -> None:
        pass


def check(desc: str, cond: bool) -> None:
    if not cond:
        print(f"FAIL {desc}")
        sys.exit(1)
    print(f"OK   {desc}")


def wait_for(desc: str, fn, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            print(f"OK   {desc}")
            return
        time.sleep(0.1)
    print(f"FAIL {desc} (timeout)")
    sys.exit(1)


def main() -> None:
    fake = FakeTree()
    store = Store()

    # --- stale-tree guard -------------------------------------------------
    link = VrcLink(store)  # NOT started: no sockets, no mDNS
    syncs: list = []
    avatars: list = []
    link.on_full_sync = lambda aid, params: syncs.append((aid, params))
    link.on_avatar = avatars.append
    link.vrchat_http = fake.url

    link._sync_from_vrchat()
    check("initial sync adopts tree avatar", syncs and syncs[-1][0] == "avtr_A")

    baseline = len(syncs)
    link._handle_avatar_change("/avatar/change", "avtr_B")  # tree still shows A
    check("OSC avatar change fires immediately", avatars == ["avtr_B"])
    time.sleep(2.2)  # refetch at 1.5s hits the stale tree
    check("stale tree did NOT flip sync back to old avatar", len(syncs) == baseline)

    fake.avatar = "avtr_B"
    fake.params = {"Beta": ("f", 0.5)}
    wait_for("retry adopts fresh tree (avtr_B)",
             lambda: len(syncs) > baseline and syncs[-1][0] == "avtr_B")
    link.stop()

    # --- poll fallback (missed /avatar/change) ------------------------------
    fake.avatar = "avtr_C"
    fake.params = {"Gamma": ("i", 3)}
    link2 = VrcLink(store)
    syncs2: list = []
    link2.on_full_sync = lambda aid, params: syncs2.append(aid)
    link2.vrchat_http = fake.url
    link2._poll_tick()  # what the 10s timer does
    wait_for("poll adopts tree avatar with no OSC message",
             lambda: syncs2 and syncs2[-1] == "avtr_C", 5)
    link2.stop()

    # --- disconnect detection + reconnect probing -----------------------------
    fake2 = FakeTree()
    port2 = fake2.server.server_address[1]
    link3 = VrcLink(store)
    statuses: list = []
    link3.on_full_sync = lambda *a: None
    link3.on_status = statuses.append
    link3.vrchat_http = fake2.url
    link3._last_vrchat_http = fake2.url

    link3._sync_from_vrchat()
    check("connected while fake is up", link3.status()["vrchat_found"] is True)

    fake2.server.shutdown()  # VRChat "quits" without an mDNS goodbye
    fake2.server.server_close()  # actually release the port
    time.sleep(0.3)
    link3._sync_from_vrchat()  # failure 1
    check("still connected after one failed fetch", link3.vrchat_http is not None)
    link3._sync_from_vrchat()  # failure 2 -> disconnected
    check("marked disconnected after two failures", link3.vrchat_http is None)
    check("status broadcast says not found",
          statuses and statuses[-1]["vrchat_found"] is False)

    # VRChat "restarts" on the same port -> the poll probe reconnects
    fake3 = FakeTree.__new__(FakeTree)
    fake3.avatar = "avtr_D"
    fake3.params = {"Delta": ("T", False)}
    handler3 = type("H3", (Handler,), {"holder": fake3})
    fake3.server = ThreadingHTTPServer(("127.0.0.1", port2), handler3)
    threading.Thread(target=fake3.server.serve_forever, daemon=True).start()
    link3._poll_tick()  # probe runs synchronously inside the tick
    check("probe reconnects when the service returns", link3.vrchat_http is not None)
    link3.stop()
    fake3.server.shutdown()
    fake3.server.server_close()

    # --- core event gating ---------------------------------------------------
    class StubLink:
        on_param = on_avatar = on_full_sync = on_status = None
        def send_param(self, *a): return True

    core = AppCore(store, StubLink())
    events: list = []
    core.add_listener(lambda e: events.append(e["t"]))
    params = [("Alpha", "Bool", True), ("Beta", "Float", 0.1)]
    core._on_full_sync("avtr_X", params)
    first = events.count("params_reset")
    check("first sync broadcasts params_reset", first == 1)
    core._on_full_sync("avtr_X", params)  # same set again (periodic poll)
    check("unchanged re-sync stays quiet", events.count("params_reset") == first)
    core._on_full_sync("avtr_X", params + [("New", "Int", 1)])
    check("changed set broadcasts again", events.count("params_reset") == first + 1)

    fake.server.shutdown()
    print("ALL SYNC TESTS PASSED")


if __name__ == "__main__":
    main()
