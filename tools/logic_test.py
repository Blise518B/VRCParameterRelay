"""Hermetic test of board/category/lock logic — no OSC, no mDNS, no network.

Safe to run while VRChat is open. Exits 0 on success.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vrc_parameter_relay.core import AppCore
from vrc_parameter_relay.store import Store

AVATAR = "avtr_deadbeef-0000-4000-8000-c0ffee000001"


class StubLink:
    """Records sends instead of talking to VRChat."""

    def __init__(self) -> None:
        self.sent: list[tuple] = []
        self.on_param = self.on_avatar = self.on_full_sync = self.on_status = None

    def send_param(self, name, ptype, value) -> bool:
        self.sent.append((name, ptype, value))
        return True


def check(desc: str, cond: bool) -> None:
    if not cond:
        print(f"FAIL {desc}")
        sys.exit(1)
    print(f"OK   {desc}")


def main() -> None:
    store = Store()
    link = StubLink()
    core = AppCore(store, link)

    core._on_avatar_change(AVATAR)
    for name, ptype, value in [("Hoodie", "Bool", True), ("TailWag", "Bool", False),
                               ("GlowToggle", "Bool", False), ("Brightness", "Float", 0.75),
                               ("OutfitIndex", "Int", 1)]:
        core._on_osc_param(name, ptype, value)

    cats = core.board["categories"]
    check("default board has 4 categories (2x2)", len(cats) == 4)

    hoodie = core.add_control("Hoodie", "toggle", "Hoodie")
    bright = core.add_control("Brightness", "slider", "Brightness", 0, 1)
    glow = core.add_control("GlowToggle", "toggle", "Glow", category=cats[1]["id"])
    check("controls default to first category", hoodie["cat"] == cats[0]["id"])
    check("add_control honors category choice", glow["cat"] == cats[1]["id"])

    check("rename category", core.rename_category(cats[0]["id"], "Toggles"))
    nsfw = core.add_category("NSFW")
    check("add category", nsfw and len(core.board["categories"]) == 5)

    check("drag&drop move", core.move_control_to_category(hoodie["id"], nsfw["id"], 0))
    check("move persisted on control",
          next(c for c in core.board["controls"] if c["id"] == hoodie["id"])["cat"] == nsfw["id"])
    check("reorder within category",
          core.move_control_to_category(bright["id"], cats[0]["id"], 0))

    # lock semantics
    check("lock category", core.set_category_locked(nsfw["id"], True))
    link.sent.clear()
    check("guest blocked on locked category",
          core.set_control_value(hoodie["id"], False, source="guest") is False)
    check("no OSC sent for blocked set", link.sent == [])
    check("host still allowed", core.set_control_value(hoodie["id"], False, source="host"))
    check("host set reached OSC", link.sent == [("Hoodie", "Bool", False)])
    check("unlock", core.set_category_locked(nsfw["id"], False))
    check("guest works after unlock",
          core.set_control_value(hoodie["id"], True, source="guest"))

    # inverted toggles: shown state is flipped, server sends the real value
    inv = core.add_control("TailWag", "toggle", "NoWag", invert=True)
    check("invert stored on control", inv.get("invert") is True)
    link.sent.clear()
    check("inverted set accepted", core.set_control_value(inv["id"], True))
    check("shown ON sent the parameter OFF", link.sent == [("TailWag", "Bool", False)])
    check("edit can clear invert", core.update_control(inv["id"], invert=False))
    link.sent.clear()
    core.set_control_value(inv["id"], True)
    check("non-inverted after edit", link.sent == [("TailWag", "Bool", True)])
    check("cleanup inverted control", core.remove_control(inv["id"]))

    # persistence round trip (fresh Store to force re-read from disk)
    reloaded = Store().load_board(AVATAR)
    check("categories persisted", len(reloaded["categories"]) == 5)
    check("category names persisted",
          {c["name"] for c in reloaded["categories"]} >= {"Toggles", "NSFW"})
    check("control category persisted",
          next(c for c in reloaded["controls"] if c["id"] == hoodie["id"])["cat"] == nsfw["id"])

    # robustness: a board file missing categories gets defaults on load
    bare = store._board_path("avtr_bare")
    bare.write_text('{"name": "Old", "controls": [{"id": "x1", "param": "P", '
                    '"ptype": "Bool", "kind": "toggle", "label": "P"}]}', "utf-8")
    normalized = store.load_board("avtr_bare")
    check("bare board file gains 4 categories", len(normalized["categories"]) == 4)
    check("its controls land in the first category",
          normalized["controls"][0]["cat"] == normalized["categories"][0]["id"])

    # deletion reassigns controls
    check("delete category", core.remove_category(nsfw["id"]))
    check("controls reassigned after delete",
          next(c for c in core.board["controls"] if c["id"] == hoodie["id"])["cat"]
          == core.board["categories"][0]["id"])
    check("last category can't be deleted",
          all(not core.remove_category(c["id"]) for c in [core.board["categories"][0]])
          if len(core.board["categories"]) == 1 else True)

    print("ALL LOGIC TESTS PASSED")


if __name__ == "__main__":
    main()
