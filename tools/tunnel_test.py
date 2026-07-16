"""Smoke test for the share feature: web server + real cloudflared quick tunnel.

Prints the public URL, then keeps serving until killed. Uses the same
download/spawn code path as the Share button in the app.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

from vrc_parameter_relay.core import AppCore
from vrc_parameter_relay.osc_link import VrcLink
from vrc_parameter_relay.store import Store
from vrc_parameter_relay.tunnel import Tunnel
from vrc_parameter_relay.webserver import GuestServer


def main() -> None:
    store = Store()
    link = VrcLink(store)
    core = AppCore(store, link)
    web = GuestServer(core, store.settings["web_port"])
    tunnel = Tunnel(store)
    core.set_sharing(True)
    tunnel.on_change = lambda st: print(f"[tunnel] {st}", flush=True)

    link.start()
    web.start()
    deadline = time.time() + 10
    while not web.port and time.time() < deadline:
        time.sleep(0.2)

    tunnel.start(web.port)
    deadline = time.time() + 180
    while tunnel.state not in ("online", "error") and time.time() < deadline:
        time.sleep(0.5)

    if tunnel.state != "online":
        print(f"TUNNEL FAILED: {tunnel.status()}", flush=True)
        sys.exit(1)

    print(f"PUBLIC-URL: {tunnel.url}/?k={core.guest_token()}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        tunnel.stop()


if __name__ == "__main__":
    main()
