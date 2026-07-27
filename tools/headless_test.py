"""Hermetic test of headless mode — no Qt, no mDNS, no tunnels.

Covers: the headless module staying Qt-free, startup with sharing enabled
(self-host branch, no tunnel spawn), the profile hot-reload poll, and a
clean shutdown. Safe to run while VRChat is open. Exits 0 on success.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def check(desc: str, cond: bool) -> None:
    if not cond:
        print(f"FAIL {desc}")
        sys.exit(1)
    print(f"OK   {desc}")


def main() -> None:
    from vrc_parameter_relay.headless import HeadlessApp
    check("importing headless pulls in no Qt", "PySide6" not in sys.modules)

    from vrc_parameter_relay.store import Store
    store = Store()
    # self-host mode so start() never spawns a tunnel binary
    store.set("advanced_link", True)
    store.set("tunnel_provider", "selfhost")
    store.set("selfhost_host", "test.example.org")
    store.set("selfhost_upnp", False)

    app = HeadlessApp(store)
    check("app builds without Qt", "PySide6" not in sys.modules)

    # start everything except the OSC link (that would do real mDNS)
    app.start(start_link=False)
    check("web server up", bool(app.web.port))
    check("sharing auto-enabled", app.core.sharing_enabled is True)
    check("selfhost bind on all interfaces (dual-stack)", app.web.bind_host == "*")
    check("no tunnel started", app.tunnel.state == "off")
    with urllib.request.urlopen(
            f"http://127.0.0.1:{app.web.port}/health", timeout=3) as res:
        check("health endpoint answers", res.status == 200)

    # -- profile hot-reload -----------------------------------------------
    core = app.core
    core._on_avatar_change("avtr_headless_test")
    core._on_osc_param("TestToggle", "Bool", True)
    core.rename_avatar("Headless Demo")
    cat = core.board["categories"][0]["id"]
    core.add_control("TestToggle", "toggle", "Test", category=cat)
    check("own save does not trigger reload", core.poll_profile_change() is False)

    # simulate a parallel UI instance editing the file on disk
    path = store._profile_path("avtr_headless_test")
    data = json.loads(path.read_text("utf-8"))
    data["name"] = "Edited Elsewhere"
    for preset in data["presets"]:
        for control in preset["controls"]:
            control["label"] = "Renamed"
    path.write_text(json.dumps(data), "utf-8")
    os.utime(path, (time.time() + 2, time.time() + 2))  # ensure mtime moves

    events: list = []
    core.add_listener(lambda e: events.append(e["t"]))
    check("external edit detected", core.poll_profile_change() is True)
    check("reload emitted an avatar event", "avatar" in events)
    check("avatar name reloaded", core.avatar_name == "Edited Elsewhere")
    check("control label reloaded",
          core.board["controls"][0]["label"] == "Renamed")
    check("second poll is quiet", core.poll_profile_change() is False)

    # edits after the reload still save fine (mtime bookkeeping intact)
    core.rename_avatar("After Reload")
    check("post-reload save does not self-trigger",
          core.poll_profile_change() is False)

    app.stop_event.set()
    app.shutdown()
    check("shutdown clean (no tunnel, no link)", True)

    print("ALL HEADLESS TESTS PASSED")


if __name__ == "__main__":
    main()
