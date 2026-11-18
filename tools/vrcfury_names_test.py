"""Hermetic test of following VRCFury renumbering — no OSC, no network.

VRCFury names its parameters VF<number>_Name and the number changes between
avatar builds. Controls follow the renumbered parameter in the background:
tagged to tagged only, and never by guessing between copies that can't be
told apart. Safe to run while VRChat is open. Exits 0 on success.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = tempfile.mkdtemp(prefix="vrcpr-vrcfury-")

from vrc_parameter_relay.core import (  # noqa: E402
    AppCore, follow_vrcfury_rename, strip_vrcfury, vrcfury_tag,
)
from vrc_parameter_relay.store import Store  # noqa: E402

AVATAR = "avtr_deadbeef-0000-4000-8000-c0ffee000002"
BUILTINS = [("GestureLeft", "Int", 0), ("VelocityX", "Float", 0.0)]


class StubLink:
    def __init__(self) -> None:
        self.on_param = self.on_avatar = self.on_full_sync = self.on_status = None

    def send_param(self, name, ptype, value) -> bool:
        return True


def check(desc: str, cond: bool) -> None:
    if not cond:
        print(f"FAIL {desc}")
        sys.exit(1)
    print(f"OK   {desc}")


def sync(core: AppCore, params: list) -> None:
    """What VRChat's OSCQuery tree reports for the worn avatar."""
    core._on_full_sync(AVATAR, BUILTINS + params)


def controls(core: AppCore) -> dict:
    return {c["id"]: c for c in core.board["controls"]}


def main() -> None:
    # -- the name rules ---------------------------------------------------------
    check("tag found", vrcfury_tag("VF12_Hoodie") == "VF12_"
          and vrcfury_tag("VF3_VF12_Hoodie") == "VF3_VF12_")
    check("tag stripped", strip_vrcfury("VF12_Hoodie") == "Hoodie"
          and strip_vrcfury("VF7_Toggles/Hat") == "Toggles/Hat")
    check("untagged and look-alikes have no tag",
          not any(vrcfury_tag(n) for n in ("Hoodie", "VFX_Glow", "MyVF12_Thing",
                                           "VFH/Zone/Head", "VF12_")))

    # -- following, as a pure function ----------------------------------------------
    check("one candidate is taken",
          follow_vrcfury_rename("VF12_Hoodie", ["Tail", "VF37_Hoodie"]) == "VF37_Hoodie")
    check("tagged follows tagged past a plain twin",
          follow_vrcfury_rename("VF39_AudioLinkOff", ["AudioLinkOff", "VF41_AudioLinkOff"])
          == "VF41_AudioLinkOff")
    check("tagged never lands on the plain name",
          follow_vrcfury_rename("VF39_AudioLinkOff", ["AudioLinkOff"]) is None)
    check("plain names never move",
          follow_vrcfury_rename("Hoodie", ["VF3_Hoodie"]) is None)
    before = ["VF220_Brush", "VF220_BrushColor", "VF220_BrushSize",
              "VF39_Brush", "VF39_Tail", "VF39_Ears", "VF39_Wings"]
    after = ["VF225_Brush", "VF225_BrushColor", "VF225_BrushSize",
             "VF40_Brush", "VF40_Tail", "VF40_Ears", "VF40_Wings"]
    check("multiples told apart by their group (VF220 -> VF225)",
          follow_vrcfury_rename("VF220_Brush", after, before) == "VF225_Brush")
    check("multiples told apart by their group (VF39 -> VF40)",
          follow_vrcfury_rename("VF39_Brush", after, before) == "VF40_Brush")
    check("multiples without the old list: not assigned",
          follow_vrcfury_rename("VF220_Brush", after) is None)
    copies_before = ["VF156_Clap", "VF156_In", "VF158_Clap", "VF158_In"]
    copies_after = ["VF256_Clap", "VF256_In", "VF258_Clap", "VF258_In"]
    check("identical copies: not assigned",
          follow_vrcfury_rename("VF156_Clap", copies_after, copies_before) is None)

    # -- controls follow an avatar update -----------------------------------------
    store = Store()
    core = AppCore(store, StubLink())
    core._on_avatar_change(AVATAR)
    sync(core, [("VF12_Hoodie", "Bool", False), ("VF20_Outfit", "Int", 0),
                ("Tail", "Bool", False), ("VF30_Hat", "Bool", False),
                ("AudioLinkOff", "Bool", False), ("VF39_AudioLinkOff", "Bool", False),
                ("VF220_Brush", "Bool", False), ("VF220_BrushColor", "Float", 0.0),
                ("VF39_Brush", "Bool", False),
                ("VF156_Clap", "Bool", False), ("VF158_Clap", "Bool", False)])

    hoodie = core.add_control("VF12_Hoodie", "toggle")
    check("new control keeps the full name as label", hoodie["label"] == "VF12_Hoodie")
    out1 = core.add_control("VF20_Outfit", "toggle")
    out2 = core.add_control("VF20_Outfit", "toggle")
    named = core.add_control("VF30_Hat", "toggle", label="My hat")
    tail = core.add_control("Tail", "toggle")
    plain_al = core.add_control("AudioLinkOff", "toggle")
    tagged_al = core.add_control("VF39_AudioLinkOff", "toggle")
    brush = core.add_control("VF220_Brush", "toggle")
    clap = core.add_control("VF156_Clap", "toggle")

    # re-upload: same avatar ID, VRCFury handed out new numbers
    sync(core, [("VF37_Hoodie", "Bool", True), ("VF4_Outfit", "Int", 2),
                ("Tail", "Bool", False), ("VF31_Hat", "Bool", False),
                ("AudioLinkOff", "Bool", False), ("VF41_AudioLinkOff", "Bool", True),
                ("VF225_Brush", "Bool", False), ("VF225_BrushColor", "Float", 0.0),
                ("VF41_Brush", "Bool", False),
                ("VF256_Clap", "Bool", False), ("VF258_Clap", "Bool", False)])
    c = controls(core)
    check("toggle followed VF12 -> VF37", c[hoodie["id"]]["param"] == "VF37_Hoodie")
    check("its default label shows the new number", c[hoodie["id"]]["label"] == "VF37_Hoodie")
    check("both Int toggles followed together",
          c[out1["id"]]["param"] == c[out2["id"]]["param"] == "VF4_Outfit")
    check("Int toggle values kept", (c[out1["id"]]["on"], c[out2["id"]]["on"]) == (1, 2))
    check("custom label kept", c[named["id"]]["label"] == "My hat"
          and c[named["id"]]["param"] == "VF31_Hat")
    check("plain control untouched", c[tail["id"]]["param"] == "Tail")
    check("plain AudioLinkOff stays plain", c[plain_al["id"]]["param"] == "AudioLinkOff")
    check("VF39_AudioLinkOff followed to VF41, not to the plain one",
          c[tagged_al["id"]]["param"] == "VF41_AudioLinkOff")
    check("Brush followed its group VF220 -> VF225", c[brush["id"]]["param"] == "VF225_Brush")
    check("Clap copies: left as is", c[clap["id"]]["param"] == "VF156_Clap")
    check("old names are gone from the list",
          "VF12_Hoodie" not in core.params and "VF37_Hoodie" in core.params)
    check("the parameter list keeps the real names",
          "VF41_AudioLinkOff" in core.params and "AudioLinkOff" in core.params)
    check("the move is saved",
          any(x["param"] == "VF37_Hoodie"
              for x in store.load_profile(AVATAR)["presets"][0]["controls"]))
    check("values reach the board under the new name",
          core.board_values().get("VF37_Hoodie") is True)

    core._on_full_sync(AVATAR, [])   # VRChat mid-load
    check("empty tree drops nothing", "VF37_Hoodie" in core.params)

    # -- after a restart: the saved list is what the old numbers meant --------------
    core2 = AppCore(Store(), StubLink())
    core2._on_avatar_change(AVATAR)
    sync(core2, [("VF37_Hoodie", "Bool", True), ("VF4_Outfit", "Int", 2),
                 ("Tail", "Bool", False), ("VF31_Hat", "Bool", False),
                 ("AudioLinkOff", "Bool", False), ("VF41_AudioLinkOff", "Bool", True),
                 ("VF300_Brush", "Bool", False), ("VF300_BrushColor", "Float", 0.0),
                 ("VF42_Brush", "Bool", False)])
    check("multiples told apart from the saved list after a restart",
          controls(core2)[brush["id"]]["param"] == "VF300_Brush")

    print("\nall VRCFury follow checks passed")


if __name__ == "__main__":
    main()
