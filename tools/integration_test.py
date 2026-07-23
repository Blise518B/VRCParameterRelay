"""Headless end-to-end test against tools/fake_vrchat.py (start that first).

Verifies: mDNS discovery both ways, OSCQuery full sync, avatar detection,
board creation/persistence, control -> OSC send -> fake echo round trip.
Exits 0 on success.
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
from vrc_parameter_relay.webserver import GuestServer

FAKE_AVATAR = "avtr_deadbeef-0000-4000-8000-c0ffee000001"


def wait_for(desc: str, fn, timeout: float = 15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = fn()
        if result:
            print(f"OK   {desc}")
            return result
        time.sleep(0.25)
    print(f"FAIL {desc} (timeout after {timeout}s)")
    sys.exit(1)


def main() -> None:
    store = Store()
    link = VrcLink(store)
    core = AppCore(store, link)
    web = GuestServer(core, store.settings["web_port"])

    events: list[dict] = []
    core.add_listener(events.append)

    link.start()
    web.start()

    wait_for("web server up", lambda: web.port)
    wait_for("VRChat OSCQuery service discovered", lambda: link.vrchat_http, 20)
    wait_for("avatar id learned", lambda: core.avatar_id == FAKE_AVATAR, 20)
    wait_for("full param sync (>= 9 params)", lambda: len(core.params) >= 9, 20)
    wait_for("live OSC updates received (VelocityX wiggle)",
             lambda: any(e["t"] == "param" and e["name"] == "VelocityX" for e in events), 20)

    ptypes = {n: p["ptype"] for n, p in core.param_snapshot().items()}
    assert ptypes["Hoodie"] == "Bool", ptypes
    assert ptypes["Brightness"] == "Float", ptypes
    assert ptypes["OutfitIndex"] == "Int", ptypes
    print("OK   parameter types inferred correctly")

    # Build a board like the user would
    hoodie = core.add_control("Hoodie", "toggle", "Hoodie")
    bright = core.add_control("Brightness", "slider", "Brightness", 0, 1)
    outfit = core.add_control("OutfitIndex", "int", "Outfit", 0, 5)
    assert hoodie and bright and outfit
    print("OK   controls added")

    board_file = store.avatar_dir / (FAKE_AVATAR.replace("-", "_") + ".json")
    boards = list(store.avatar_dir.glob("*.json"))
    assert boards, "board file not persisted"
    print(f"OK   board persisted ({boards[0].name})")

    # Round trip: control -> OSC -> fake echoes back
    assert core.set_control_value(hoodie["id"], False, source="guest")
    assert core.set_control_value(bright["id"], 0.33, source="guest")
    assert core.set_control_value(outfit["id"], 99, source="guest")  # must clamp to 5
    time.sleep(1.0)
    snap = core.param_snapshot()
    assert snap["Hoodie"]["value"] is False, snap["Hoodie"]
    assert abs(snap["Brightness"]["value"] - 0.33) < 0.001, snap["Brightness"]
    assert snap["OutfitIndex"]["value"] == 5, snap["OutfitIndex"]
    print("OK   set round trip + clamping")

    # Guests only see board params
    values = core.board_values()
    assert set(values) == {"Hoodie", "Brightness", "OutfitIndex"}, values
    print("OK   guest value scoping")

    # Categories: default 2x2 grid, controls land in the first one
    cats = core.board["categories"]
    assert len(cats) == 4, cats
    assert all(c["cat"] == cats[0]["id"] for c in core.board["controls"])
    print("OK   default categories + control assignment")

    # Move a control into a new category via drag&drop path, then lock it
    nsfw = core.add_category("NSFW")
    assert core.move_control_to_category(hoodie["id"], nsfw["id"], 0)
    assert core.rename_category(cats[1]["id"], "Outfits")
    assert core.set_category_locked(nsfw["id"], True)
    hoodie_before = core.param_snapshot()["Hoodie"]["value"]
    assert core.set_control_value(hoodie["id"], not hoodie_before, source="guest") is False
    time.sleep(0.3)
    assert core.param_snapshot()["Hoodie"]["value"] == hoodie_before
    print("OK   locked category blocks guests")
    assert core.set_control_value(hoodie["id"], not hoodie_before, source="host")
    print("OK   host can still use locked category")
    assert core.set_category_locked(nsfw["id"], False)
    assert core.set_control_value(hoodie["id"], hoodie_before, source="guest")
    print("OK   unlock restores guest control")

    # Category persistence round trip
    profile = store.load_profile(FAKE_AVATAR)
    active = next(p for p in profile["presets"] if p["id"] == profile["active_preset"])
    assert len(active["categories"]) == 5
    assert any(c["name"] == "NSFW" for c in active["categories"])
    hoodie_ctrl = next(c for c in active["controls"] if c["id"] == hoodie["id"])
    assert hoodie_ctrl["cat"] == nsfw["id"]
    print("OK   categories persisted")

    # Deleting a category moves its controls to the first one
    assert core.remove_category(nsfw["id"])
    hoodie_ctrl = next(c for c in core.board["controls"] if c["id"] == hoodie["id"])
    assert hoodie_ctrl["cat"] == core.board["categories"][0]["id"]
    print("OK   category deletion reassigns controls")

    token = core.guest_token()
    print(f"guest-url: http://127.0.0.1:{web.port}/?k={token}")
    print("ALL TESTS PASSED")
    link.stop()


if __name__ == "__main__":
    main()
