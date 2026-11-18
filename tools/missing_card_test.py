"""Hermetic test: cards whose parameter the avatar doesn't have, and
pointing a card at another parameter — desktop window and admin panel ops.

No mDNS, no OSC sockets — safe to run while VRChat is open. Exits 0 on success.

Covers:
  * which cards count as missing (live: only once VRChat's full list is in)
  * re-pointing keeps label, category and Int values, follows the new type
  * grey cards in the desktop window (control disabled), the ⋯ → Change
    parameter… picker, and a parameter dropped onto a grey card re-pointing it
  * the status-bar note after VRCFury renumbering was followed
  * the admin panel's snapshot and its repoint_control op
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = tempfile.mkdtemp(prefix="vrcpr-missing-")
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtCore import QMimeData, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QDragMoveEvent, QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from vrc_parameter_relay.core import AppCore  # noqa: E402
from vrc_parameter_relay.store import Store  # noqa: E402
from vrc_parameter_relay.tunnel import Tunnel  # noqa: E402
from vrc_parameter_relay.ui.dialogs import ParamPickerDialog  # noqa: E402
from vrc_parameter_relay.ui.main_window import MainWindow  # noqa: E402
from vrc_parameter_relay.ui.theme import build_qss  # noqa: E402
from vrc_parameter_relay.ui.widgets import MIME_PARAM  # noqa: E402
from vrc_parameter_relay.webserver import GuestServer  # noqa: E402

AVATAR = "avtr_deadbeef-0000-4000-8000-c0ffee000005"
BUILTINS = [("GestureLeft", "Int", 0), ("VelocityX", "Float", 0.0)]


class StubLink:
    on_param = on_avatar = on_full_sync = on_status = None

    def send_param(self, name, ptype, value) -> bool:
        return True

    def refetch(self) -> None:
        pass


def check(desc: str, cond: bool, detail: str = "") -> None:
    if not cond:
        print(f"FAIL {desc}" + (f"  [{detail}]" if detail else ""))
        sys.exit(1)
    print(f"OK   {desc}")


def pump(ms: int) -> None:
    end = time.time() + ms / 1000
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def control(core: AppCore, control_id: str) -> dict:
    return next(c for c in core.board["controls"] if c["id"] == control_id)


def main() -> None:
    store = Store()
    core = AppCore(store, StubLink())
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(build_qss("neon"))

    # -- core ----------------------------------------------------------------------
    core._on_avatar_change(AVATAR)
    check("nothing is missing before VRChat's list arrives", not core.missing_params())
    first = BUILTINS + [("VF156_Clap", "Bool", False), ("VF158_Clap", "Bool", False),
                        ("Hoodie", "Bool", False), ("Outfit", "Int", 0),
                        ("Glow", "Float", 0.5)]
    core._on_full_sync(AVATAR, first)
    clap = core.add_control("VF156_Clap", "toggle", "Clap")
    hoodie = core.add_control("Hoodie", "toggle")
    outfit = core.add_control("Outfit", "toggle", "Party outfit", on=3, off=0)
    cat2 = core.board["categories"][1]["id"]
    core.move_control_to_category(outfit["id"], cat2)
    check("all present: nothing missing", not core.missing_params())

    # rebuild: the two copies are renumbered (can't be told apart), Hoodie renamed by hand
    second = BUILTINS + [("VF256_Clap", "Bool", False), ("VF258_Clap", "Bool", False),
                         ("Hoodie_v2", "Bool", False), ("Outfit", "Int", 0),
                         ("Glow", "Float", 0.5), ("Costume", "Int", 0)]
    core._on_full_sync(AVATAR, second)
    check("copies and a hand rename are missing",
          core.missing_params() == {"VF156_Clap", "Hoodie"}, str(core.missing_params()))

    check("re-pointing to a parameter the avatar lacks is refused",
          not core.repoint_control(clap["id"], "NoSuchParam"))
    check("re-point the copy", core.repoint_control(clap["id"], "VF258_Clap"))
    c = control(core, clap["id"])
    check("custom label kept", c["param"] == "VF258_Clap" and c["label"] == "Clap")
    check("the card is no longer missing", "VF156_Clap" not in core.missing_params())
    check("re-pointing is saved", any(
        x["param"] == "VF258_Clap" for x in store.load_profile(AVATAR)["presets"][0]["controls"]))
    core.repoint_control(outfit["id"], "Costume")
    c = control(core, outfit["id"])
    check("Int toggle keeps its category and values",
          c["cat"] == cat2 and (c["on"], c["off"]) == (3, 0) and c["param"] == "Costume")
    core.repoint_control(outfit["id"], "Glow")
    c = control(core, outfit["id"])
    check("control type follows the new parameter (Int toggle -> Float slider)",
          c["ptype"] == "Float" and c["kind"] == "slider")

    # -- desktop window ------------------------------------------------------------
    web = GuestServer(core, 0)
    window = MainWindow(core, Tunnel(store), web)
    window.resize(1150, 700)
    window.show()
    pump(300)
    card = window.cards[hoodie["id"]]
    check("grey card drawn for the missing parameter",
          card.missing and card.property("missing") is True)
    check("its control is disabled", not card.findChildren(QPushButton)[0].isEnabled())
    check("a present card is normal", not window.cards[clap["id"]].missing
          and window.cards[clap["id"]].findChildren(QPushButton)[0].isEnabled())

    picker = ParamPickerDialog(window, core.param_snapshot(), "VF156_Clap", missing=True)
    check("picker starts filtered on the name without the VRCFury tag",
          picker.filter.text() == "Clap" and picker.chosen() == "VF256_Clap")
    picker.filter.setText("hoodie")
    check("picker filters", picker.chosen() == "Hoodie_v2")
    picker.filter.setText("zzz-nothing")
    check("no match: nothing to choose", picker.chosen() is None)
    picker.deleteLater()

    # drag a parameter from the list onto the grey card
    box = next(b for b in window.boxes.values() if card in b.findChildren(type(card)))
    mime = QMimeData()
    mime.setData(MIME_PARAM, b"Hoodie_v2\nBool")
    at = QPointF(card.geometry().center() + box.cards_host.pos())
    box.dragMoveEvent(QDragMoveEvent(at.toPoint(), Qt.CopyAction, mime,
                                     Qt.LeftButton, Qt.NoModifier))
    check("the grey card is outlined while a parameter hovers it",
          card.property("droptarget") is True)
    before = len(core.board["controls"])
    box.dropEvent(QDropEvent(at, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
    pump(200)
    check("dropping re-points the grey card (no new card)",
          control(core, hoodie["id"])["param"] == "Hoodie_v2"
          and len(core.board["controls"]) == before)
    check("label that was the old name follows",
          control(core, hoodie["id"])["label"] == "Hoodie_v2")
    check("the card is drawn normal again", not window.cards[hoodie["id"]].missing)

    # dropping onto a normal card still adds a new card there
    card = window.cards[clap["id"]]
    box = next(b for b in window.boxes.values() if card in b.findChildren(type(card)))
    at = QPointF(card.geometry().center() + box.cards_host.pos())
    box.dragMoveEvent(QDragMoveEvent(at.toPoint(), Qt.CopyAction, mime,
                                     Qt.LeftButton, Qt.NoModifier))
    box.dropEvent(QDropEvent(at, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
    pump(200)
    check("onto a normal card: a new card, the old one untouched",
          len(core.board["controls"]) == before + 1
          and control(core, clap["id"])["param"] == "VF258_Clap")

    # VRCFury renumbering followed -> status bar note
    core.add_control("VF258_Clap", "button", "Clap hold")
    core._on_full_sync(AVATAR, BUILTINS + [("VF259_Clap", "Bool", False),
                                           ("Hoodie_v2", "Bool", False), ("Outfit", "Int", 0),
                                           ("Glow", "Float", 0.5), ("Costume", "Int", 0)])
    pump(200)
    msg = window.statusBar().currentMessage()
    check("status bar says the controls followed",
          msg == "VRCFury renumbered 1 parameter — 2 controls followed", msg)

    # -- admin panel ---------------------------------------------------------------
    core._on_full_sync(AVATAR, BUILTINS + [("Hoodie_v3", "Bool", False), ("Outfit", "Int", 0),
                                           ("Glow", "Float", 0.5), ("Costume", "Int", 0),
                                           ("VF259_Clap", "Bool", False)])
    state = web._admin_state()
    check("admin snapshot lists the missing parameter", state["missing"] == ["Hoodie_v2"],
          str(state["missing"]))
    error = web._admin_op({"op": "repoint_control", "id": hoodie["id"], "param": "Hoodie_v3"})
    check("admin repoint_control works", error is None
          and control(core, hoodie["id"])["param"] == "Hoodie_v3")
    error = web._admin_op({"op": "repoint_control", "id": hoodie["id"], "param": "Nope"})
    check("admin repoint_control refuses an unknown parameter", bool(error))
    extra = next(c for c in core.board["controls"]
                 if c["param"] == "Hoodie_v2" and c["id"] != hoodie["id"])
    check("only the other card on the gone name stays grey",
          web._admin_state()["missing"] == ["Hoodie_v2"])
    web._admin_op({"op": "repoint_control", "id": extra["id"], "param": "Hoodie_v3"})
    check("admin snapshot: nothing missing now", web._admin_state()["missing"] == [])

    window.close()
    print("\nall missing-card checks passed")


if __name__ == "__main__":
    main()
