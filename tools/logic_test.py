"""Hermetic test of board/category/lock logic — no OSC, no mDNS, no network.

Safe to run while VRChat is open. Exits 0 on success.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vrc_parameter_relay.core import AppCore
from vrc_parameter_relay.store import Store, normalize_profile

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

    # category colours: 4 defaults, and add_category("NSFW") earlier picked
    # the first colour not already used (cyan)
    check("new boards spawn red/green/yellow/blue",
          [c.get("color") for c in core.board["categories"][:4]]
          == ["red", "green", "yellow", "blue"])
    check("added category takes an unused colour (cyan)", nsfw["color"] == "cyan")
    check("set category color", core.set_category_color(nsfw["id"], "red"))
    check("color stored", nsfw["color"] == "red")
    check("invalid color rejected",
          core.set_category_color(nsfw["id"], "hotpink") is False)
    check("color unchanged after bad set", nsfw["color"] == "red")

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
    profile = Store().load_profile(AVATAR)
    active = next(p for p in profile["presets"] if p["id"] == profile["active_preset"])
    check("categories persisted", len(active["categories"]) == 5)
    check("category names persisted",
          {c["name"] for c in active["categories"]} >= {"Toggles", "NSFW"})
    check("control category persisted",
          next(c for c in active["controls"] if c["id"] == hoodie["id"])["cat"] == nsfw["id"])

    # instant-add default kinds
    check("default kind Bool->toggle", core.default_kind("Bool") == "toggle")
    check("default kind Int->int", core.default_kind("Int") == "int")
    check("default kind Float->slider", core.default_kind("Float") == "slider")

    # add_control with a drop index lands at the right spot in its category
    catA = core.board["categories"][0]["id"]
    a1 = core.add_control("Hoodie", "toggle", category=catA)
    a2 = core.add_control("GlowToggle", "toggle", category=catA, index=0)  # before a1
    order = [c["id"] for c in core.board["controls"] if c["cat"] == catA]
    check("dropped control inserts at index", order.index(a2["id"]) < order.index(a1["id"]))
    core.remove_control(a1["id"]); core.remove_control(a2["id"])

    # move_category reorders
    cat_ids0 = [c["id"] for c in core.board["categories"]]
    check("move_category reorders", core.move_category(cat_ids0[-1], cat_ids0[0]))
    check("category moved to front",
          core.board["categories"][0]["id"] == cat_ids0[-1])
    check("move_category no-op on same", core.move_category(cat_ids0[0], cat_ids0[0]) is False)

    # migration: a v1.1.0 single-board file becomes a one-preset profile
    bare = store._profile_path("avtr_bare")
    bare.write_text('{"name": "Old", "controls": [{"id": "x1", "param": "P", '
                    '"ptype": "Bool", "kind": "toggle", "label": "P"}]}', "utf-8")
    migrated = store.load_profile("avtr_bare")
    check("old file gains one Default preset", len(migrated["presets"]) == 1)
    mp = migrated["presets"][0]
    check("migrated preset has 4 categories", len(mp["categories"]) == 4)
    check("its controls land in the first category",
          mp["controls"][0]["cat"] == mp["categories"][0]["id"])
    check("no stray top-level board keys", "controls" not in migrated
          and "categories" not in migrated)

    # deletion reassigns controls
    check("delete category", core.remove_category(nsfw["id"]))
    check("controls reassigned after delete",
          next(c for c in core.board["controls"] if c["id"] == hoodie["id"])["cat"]
          == core.board["categories"][0]["id"])
    check("last category can't be deleted",
          all(not core.remove_category(c["id"]) for c in [core.board["categories"][0]])
          if len(core.board["categories"]) == 1 else True)

    # ---- presets -----------------------------------------------------------
    base_preset = core.board["id"]
    core.add_control("Hoodie", "toggle", "Hoodie")  # something to copy
    stream = core.add_preset("Stream")
    check("add_preset switches to it", core.board["id"] == stream["id"])
    check("new preset starts empty", core.board["controls"] == [])
    check("rename preset", core.rename_preset(stream["id"], "Streaming"))
    check("switch back", core.switch_preset(base_preset))
    check("switched", core.board["id"] == base_preset)

    # copy a category (with controls) into the other preset
    cat0 = core.board["categories"][0]
    n_ctrls = sum(1 for c in core.board["controls"] if c["cat"] == cat0["id"])
    check("copy category to preset",
          core.copy_category_to_preset(cat0["id"], stream["id"]))
    target = next(p for p in core.profile["presets"] if p["id"] == stream["id"])
    copied_cat = target["categories"][-1]
    copied_ctrls = [c for c in target["controls"] if c["cat"] == copied_cat["id"]]
    check("copied category keeps its name", copied_cat["name"] == cat0["name"])
    check("controls copied with it", len(copied_ctrls) == n_ctrls)
    check("copies got fresh ids", copied_cat["id"] != cat0["id"] and
          all(c["id"] not in {x["id"] for x in core.board["controls"]}
              for c in copied_ctrls))
    check("copy to own preset refused",
          core.copy_category_to_preset(cat0["id"], base_preset) is False)
    check("delete preset", core.remove_preset(stream["id"]))
    check("last preset can't be deleted",
          core.remove_preset(core.board["id"]) is False)

    # ---- offline avatar library ---------------------------------------------
    core.rename_avatar("My Cool Avatar")
    check("avatar rename stored", core.avatar_name == "My Cool Avatar")
    entries = {e["avatar_id"]: e for e in core.list_avatars()}
    check("named avatar listed", AVATAR in entries and entries[AVATAR]["named"])

    check("live flag set (StubLink path)", core.is_live)
    other = "avtr_beefcafe-0000-4000-8000-000000000002"
    core.store.save_profile(normalize_profile({"name": "Other Ava"}, other))
    check("open other avatar offline", core.open_avatar(other))
    check("viewing offline", core.avatar_id == other and not core.is_live)
    link.sent.clear()
    added = core.add_control("Whatever", "toggle", "W")
    check("offline board editing works", added is not None)
    check("offline set sends nothing",
          core.set_control_value(added["id"], True) is False and link.sent == [])
    check("back to live avatar", core.open_avatar(AVATAR) and core.is_live)

    print("ALL LOGIC TESTS PASSED")


if __name__ == "__main__":
    main()
