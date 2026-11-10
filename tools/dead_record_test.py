"""VRChat on this PC with a record a crashed VRChat left behind.

A crashed VRChat's mDNS record can linger for hours next to the live game:
a dead VRChat-Client-305E19 (its OSCQuery never answers) sat next to the
live VRChat-Client-28372B for 1.7 h, announced right after it. Drives
VrcLink's discovery with real OSCQuery HTTP servers on loopback and a closed
port for the dead record — no mDNS, no OSC — and checks, same-PC mode:

  * a dead record announced after the live VRChat doesn't take over: the
    polls keep syncing from the live one, never probe the dead address
  * a restarted VRChat (the old one no longer answers) takes over
  * a second VRChat that answers doesn't take over while ours answers;
    when ours stops answering, it does
  * a dead record seen first doesn't block the live one
  * when ours stops answering, a dead record is never picked
  * the poll doesn't keep probing a dead address while a VRChat we know of
    answers

Safe to run while VRChat is open. Throwaway data dir. Exits 0 on success.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = tempfile.mkdtemp(prefix="vrcpr-dead-")

from vrc_parameter_relay.osc_link import VrcLink  # noqa: E402
from vrc_parameter_relay.store import Store  # noqa: E402

FAILS: list[str] = []
LIVE = "VRChat-Client-28372B._oscjson._tcp.local."
DEAD = "VRChat-Client-305E19._oscjson._tcp.local."
OTHER = "VRChat-Client-AB12CD._oscjson._tcp.local."
LOOPBACK = ["127.0.0.1"]


def check(desc: str, cond: bool) -> None:
    print(("OK   " if cond else "FAIL ") + desc, flush=True)
    if not cond:
        FAILS.append(desc)


def wait_for(cond, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


class FakeOscQuery:
    """VRChat's OSCQuery HTTP server on loopback. crash() is VRChat dying
    without an mDNS goodbye; busy answers 503, like a stalled VRChat."""

    def __init__(self, avatar: str) -> None:
        self.avatar = avatar
        self.busy = False
        handler = type("H", (Handler,), {"fake": self})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def crash(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class Handler(BaseHTTPRequestHandler):
    fake: FakeOscQuery = None

    def do_GET(self) -> None:  # noqa: N802
        if self.fake.busy:
            self.send_response(503)
            self.end_headers()
            return
        if "HOST_INFO" in self.path:
            body = {"NAME": "VRChat-Client", "OSC_IP": "127.0.0.1",
                    "OSC_PORT": 9, "OSC_TRANSPORT": "UDP"}   # discard port
        else:
            body = {"FULL_PATH": "/", "CONTENTS": {"avatar": {"FULL_PATH": "/avatar", "CONTENTS": {
                "change": {"FULL_PATH": "/avatar/change", "TYPE": "s", "VALUE": [self.fake.avatar]},
                "parameters": {"FULL_PATH": "/avatar/parameters", "CONTENTS": {
                    "Hoodie": {"FULL_PATH": "/avatar/parameters/Hoodie", "TYPE": "T",
                               "ACCESS": 3, "VALUE": [True]}}},
            }}}}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a) -> None:
        pass


def dead_port() -> int:
    """A loopback port nothing listens on: a crashed VRChat's old record."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def new_link() -> tuple[VrcLink, list]:
    link = VrcLink(Store())   # not started: no sockets, no mDNS; same-PC mode
    link._schedule_poll = lambda: None   # the test runs the poll itself
    syncs: list = []
    link.on_full_sync = lambda avatar, params: syncs.append(avatar)
    return link, syncs


def settle() -> None:
    """Let the sync a (re)connect starts in the background finish."""
    wait_for(lambda: not any(t.name == "oscq-sync" for t in threading.enumerate()), 20)


def poll(link: VrcLink, times: int = 1) -> None:
    for _ in range(times):
        link._poll_tick()
        settle()


def dead_record_after_live() -> None:
    """The order in the log: live VRChat first, the dead record right after."""
    live = FakeOscQuery("avtr_live")
    link, syncs = new_link()
    link._vrchat_seen(LIVE, LOOPBACK, live.port)
    settle()
    check("the live VRChat is found and synced",
          link.vrchat_http == live.url and syncs[-1:] == ["avtr_live"])
    link._vrchat_seen(DEAD, LOOPBACK, dead_port())
    settle()
    check("a dead record announced after the live VRChat doesn't take over",
          link._vrchat_name == LIVE and link.vrchat_http == live.url)
    before = len(syncs)
    poll(link, 3)
    check("...the polls keep syncing from the live VRChat",
          link.vrchat_http == live.url and syncs[before:] == ["avtr_live"] * 3)
    check("...and never probe the dead record's address",
          link._last_vrchat_http == live.url)
    live.crash()


def restart() -> None:
    """VRChat crashed (no goodbye) and was started again."""
    old = FakeOscQuery("avtr_old")
    link, syncs = new_link()
    link._vrchat_seen(LIVE, LOOPBACK, old.port)
    settle()
    old.crash()
    new = FakeOscQuery("avtr_new")
    link._vrchat_seen(OTHER, LOOPBACK, new.port)
    settle()
    check("restart: the new VRChat takes over once the old one no longer answers",
          link._vrchat_name == OTHER and link.vrchat_http == new.url
          and syncs[-1:] == ["avtr_new"])
    poll(link)
    check("restart: the poll follows the new one",
          link.vrchat_http == new.url and syncs[-1:] == ["avtr_new"])
    new.crash()


def second_live_vrchat() -> None:
    first, second = FakeOscQuery("avtr_first"), FakeOscQuery("avtr_second")
    link, syncs = new_link()
    link._vrchat_seen(LIVE, LOOPBACK, first.port)
    settle()
    link._vrchat_seen(OTHER, LOOPBACK, second.port)
    settle()
    check("a second VRChat that answers doesn't take over while ours answers",
          link._vrchat_name == LIVE and link.vrchat_http == first.url
          and "avtr_second" not in syncs)
    first.crash()
    poll(link, 2)   # two failed polls: ours is gone
    check("...when ours stops answering, the other one that answers takes over",
          link._vrchat_name == OTHER and link.vrchat_http == second.url
          and syncs[-1:] == ["avtr_second"])
    second.crash()


def dead_record_first() -> None:
    """Both records already there when we start, the dead one seen first."""
    live = FakeOscQuery("avtr_live")
    link, syncs = new_link()
    link._vrchat_seen(DEAD, LOOPBACK, dead_port())
    settle()
    link._vrchat_seen(LIVE, LOOPBACK, live.port)
    settle()
    check("a dead record seen first doesn't block the live VRChat",
          link._vrchat_name == LIVE and link.vrchat_http == live.url
          and syncs[-1:] == ["avtr_live"])
    poll(link, 2)
    check("...and the polls stay with the live one",
          link.vrchat_http == live.url and link._last_vrchat_http == live.url)
    live.crash()


def never_back_to_a_dead_record() -> None:
    live = FakeOscQuery("avtr_live")
    link, syncs = new_link()
    link._vrchat_seen(LIVE, LOOPBACK, live.port)
    settle()
    link._vrchat_seen(DEAD, LOOPBACK, dead_port())
    settle()
    live.crash()
    poll(link, 2)   # two failed polls: ours is gone
    check("when ours stops answering, a dead record is never picked",
          link.vrchat_http is None and link._vrchat_name != DEAD
          and link._last_vrchat_http == live.url)


def poll_finds_a_live_one() -> None:
    """Ours died while the other VRChat we know of was busy for a moment."""
    first, second = FakeOscQuery("avtr_first"), FakeOscQuery("avtr_second")
    link, syncs = new_link()
    link._vrchat_seen(LIVE, LOOPBACK, first.port)
    settle()
    link._vrchat_seen(OTHER, LOOPBACK, second.port)
    settle()
    second.busy = True
    first.crash()
    poll(link, 2)
    check("ours gone while the other VRChat is busy: disconnected for now",
          link.vrchat_http is None)
    second.busy = False
    poll(link)
    check("...the next poll connects to the other one once it answers, "
          "instead of probing the dead address",
          link._vrchat_name == OTHER and link.vrchat_http == second.url
          and link._last_vrchat_http == second.url and syncs[-1:] == ["avtr_second"])
    second.crash()


def main() -> int:
    for case in (dead_record_after_live, restart, second_live_vrchat,
                 dead_record_first, never_back_to_a_dead_record, poll_finds_a_live_one):
        print(f"-- {case.__name__}", flush=True)
        case()
    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
