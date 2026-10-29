"""Hermetic test: Int parameters as toggles, and the desktop stepper fixes.

No mDNS, no OSC sockets — safe to run while VRChat is open. A stub link
plays VRChat: every send is echoed back as the avatar's real type, the way
VRChat re-broadcasts a changed parameter. Exits 0 on success.

Covers:
  * Int toggles / hold buttons send their ON / OFF values (VRChat-menu style)
    and several toggles on one Int light up one at a time
  * a Bool toggle whose parameter became an Int follows the avatar
  * the desktop stepper keeps following VRChat after it was clicked
  * the mouse wheel scrolls the board instead of changing steppers/sliders
  * leaving a name field without an edit no longer rebuilds the board
"""
from __future__ import annotations

import os
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = tempfile.mkdtemp(prefix="vrcpr-int-")
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSpinBox

from vrc_parameter_relay.core import AppCore
from vrc_parameter_relay.store import Store
from vrc_parameter_relay.tunnel import Tunnel
from vrc_parameter_relay.ui.dialogs import ControlDialog
from vrc_parameter_relay.ui.main_window import MainWindow
from vrc_parameter_relay.ui.theme import build_qss
from vrc_parameter_relay.webserver import GuestServer

AVATAR = "avtr_deadbeef-0000-4000-8000-c0ffee000001"
REAL_TYPES = {"Outfit": "Int", "Hat": "Int", "Gloves": "Int", "Emote": "Int",
              "Brightness": "Float"}


class EchoLink:
    on_param = on_avatar = on_full_sync = on_status = None

    def __init__(self) -> None:
        self.sent: list[tuple] = []
        self._echoes: queue.Queue = queue.Queue()
        threading.Thread(target=self._echo_loop, daemon=True).start()

    def send_param(self, name, ptype, value) -> bool:
        self.sent.append((name, ptype, value))
        real = REAL_TYPES.get(name, ptype)
        self._echoes.put((name, real, int(value) if real == "Int" else value))
        return True

    def _echo_loop(self) -> None:
        while True:  # one thread keeps echoes in send order, like loopback UDP
            item = self._echoes.get()
            time.sleep(0.03)
            self.on_param(*item)

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


def click_spin_up(spin) -> None:
    opt = QStyleOptionSpinBox()
    spin.initStyleOption(opt)
    rect = spin.style().subControlRect(QStyle.CC_SpinBox, opt, QStyle.SC_SpinBoxUp, spin)
    QTest.mouseClick(spin, Qt.LeftButton, Qt.NoModifier, rect.center())


def wheel(widget, ticks: int) -> None:
    for _ in range(ticks):
        ev = QWheelEvent(QPointF(10, 10), QPointF(widget.mapToGlobal(QPoint(10, 10))),
                         QPoint(0, 0), QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                         Qt.NoScrollPhase, False)
        QApplication.sendEvent(widget, ev)
        pump(20)


def full_sync(core: AppCore) -> None:
    core._on_full_sync(AVATAR, [(n, p["ptype"], p["value"])
                                for n, p in core.param_snapshot().items()])


def main() -> None:
    store = Store()
    link = EchoLink()
    core = AppCore(store, link)
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(build_qss("neon"))

    core._on_avatar_change(AVATAR)
    core._on_full_sync(AVATAR, [("Outfit", "Int", 0), ("Hat", "Int", 0),
                                ("Gloves", "Int", 0), ("Emote", "Int", 0),
                                ("Brightness", "Float", 0.5)])

    # -- core: Int toggles and hold buttons --------------------------------------
    t1 = core.add_control("Outfit", "toggle", "Outfit 1")
    t2 = core.add_control("Outfit", "toggle", "Outfit 2")
    t3 = core.add_control("Outfit", "toggle", "Outfit 3")
    check("adding the same Int as toggles numbers them 1, 2, 3",
          [t1.get("on"), t2.get("on"), t3.get("on")] == [1, 2, 3]
          and t1["ptype"] == "Int" and t1.get("off") == 0,
          f"{[t1.get('on'), t2.get('on'), t3.get('on')]}")
    link.sent.clear()
    core.set_control_value(t2["id"], True)
    core.set_control_value(t2["id"], False, source="guest")
    check("Int toggle sends its ON value, then its OFF value",
          link.sent == [("Outfit", "Int", 2), ("Outfit", "Int", 0)], f"{link.sent}")
    emote = core.add_control("Emote", "button", "Wave", on=3)
    link.sent.clear()
    core.set_control_value(emote["id"], True)
    core.set_control_value(emote["id"], False)
    check("Int hold button sends its value while held, 0 on release",
          link.sent == [("Emote", "Int", 3), ("Emote", "Int", 0)], f"{link.sent}")
    big = core.add_control("Gloves", "toggle", "Gloves", on=999, off=-5)
    check("ON/OFF values are clamped to VRChat's 0..255",
          (big["on"], big["off"]) == (255, 0), f"{big['on']}, {big['off']}")
    stepper_on_bool = core.add_control("Brightness", "toggle")
    check("a kind that doesn't fit the parameter falls back to its default",
          stepper_on_bool["kind"] == "slider", stepper_on_bool["kind"])
    core.remove_control(stepper_on_bool["id"])
    core.remove_control(big["id"])

    # -- core: a Bool board meets an avatar whose toggles became Ints -------------
    hat = core.add_control("Hat", "toggle", "Hat")
    gloves = core.add_control("Gloves", "toggle", "Gloves")
    for ctrl, inv in ((hat, False), (gloves, True)):  # as stored by the old avatar
        ctrl["ptype"] = "Bool"
        ctrl.pop("on", None)
        ctrl.pop("off", None)
        if inv:
            ctrl["invert"] = True
    events: list[str] = []
    core.add_listener(lambda e: events.append(e["t"]))
    full_sync(core)
    check("OSCQuery sync turns the old Bool toggles into Int toggles",
          hat["ptype"] == "Int" and (hat["on"], hat["off"]) == (1, 0)
          and gloves["ptype"] == "Int" and (gloves["on"], gloves["off"]) == (0, 1)
          and "invert" not in gloves and "board" in events,
          f"hat={hat}, gloves={gloves}")
    events.clear()
    full_sync(core)
    check("the next sync finds nothing to change (no rebuild loop)",
          "board" not in events, f"{events}")
    link.sent.clear()
    core.set_control_value(hat["id"], True)
    check("the converted toggle sends an Int", link.sent == [("Hat", "Int", 1)], f"{link.sent}")
    core.update_control(t1["id"], kind="toggle", on=7, off=1)
    check("editing an Int toggle stores its ON / OFF values",
          (t1["on"], t1["off"]) == (7, 1), f"{t1}")
    core.update_control(t1["id"], on=1, off=0)

    # -- desktop: the edit dialog --------------------------------------------------
    dlg = ControlDialog(None, "Outfit", "Int", existing=t3, suggested_on=4)
    kinds = [dlg.kind_combo.itemData(i) for i in range(dlg.kind_combo.count())]
    check("edit dialog offers toggle + hold button for an Int",
          {"toggle", "button"} <= set(kinds), f"{kinds}")
    check("edit dialog shows the toggle's values", dlg.on_spin.value() == 3
          and not dlg.on_spin.isHidden(), f"on={dlg.on_spin.value()}")
    dlg.on_spin.setValue(5)
    res = dlg.result_dict()
    check("edit dialog returns the ON / OFF values", res.get("on") == 5 and res.get("off") == 0,
          f"{res}")
    dlg.kind_combo.setCurrentIndex(kinds.index("int"))
    check("value fields hide for a stepper", dlg.on_spin.isHidden()
          and "on" not in dlg.result_dict())
    dlg.deleteLater()

    # -- desktop: the board ---------------------------------------------------------
    stepper = core.add_control("Outfit", "int", "Outfit #")
    slider = core.add_control("Brightness", "slider", "Brightness")
    web = GuestServer(core, 0)
    window = MainWindow(core, Tunnel(store), web)
    window.resize(1100, 700)
    window.show()
    pump(300)

    card1, card2 = window.cards[t1["id"]], window.cards[t2["id"]]
    link.sent.clear()
    card2.switch.click()
    pump(200)
    check("clicking toggle 'Outfit 2' sends Outfit = 2",
          link.sent == [("Outfit", "Int", 2)], f"{link.sent}")
    check("only 'Outfit 2' shows ON", card2.switch.isChecked()
          and not card1.switch.isChecked())
    card1.switch.click()
    pump(200)
    check("switching to 'Outfit 1' turns 'Outfit 2' off",
          card1.switch.isChecked() and not card2.switch.isChecked())

    rebuilds: list[str] = []
    core.add_listener(lambda e: rebuilds.append(e["t"]) if e["t"] == "board" else None)
    next(iter(window.boxes.values())).name_edit.setFocus()
    pump(50)
    spin = window.cards[stepper["id"]].spin
    link.sent.clear()
    click_spin_up(spin)
    pump(400)
    check("leaving a category name unedited doesn't rebuild the board",
          not rebuilds, f"{len(rebuilds)} rebuilds")
    window.board_name.setFocus()
    pump(50)
    click_spin_up(spin)
    pump(400)
    check("leaving the avatar name unedited doesn't rebuild either", not rebuilds,
          f"{len(rebuilds)} rebuilds")
    # Outfit was 1 (toggle above) — the stepper counts on from there
    check("both stepper clicks went out",
          link.sent == [("Outfit", "Int", 2), ("Outfit", "Int", 3)], f"{link.sent}")

    core._on_osc_param("Outfit", "Int", 5)  # changed in the radial menu
    pump(100)
    check("a clicked (focused) stepper still follows VRChat", spin.value() == 5,
          f"shows {spin.value()}, focus={spin.hasFocus()}")
    link.sent.clear()
    click_spin_up(spin)
    pump(400)
    check("the next + counts from the real value", link.sent == [("Outfit", "Int", 6)],
          f"{link.sent}")

    link.sent.clear()
    before = spin.value()
    wheel(spin, 4)
    wheel(window.cards[slider["id"]].slider, 4)
    pump(400)
    check("the mouse wheel over a stepper / slider changes nothing",
          spin.value() == before and not link.sent, f"sent={link.sent}")

    print("\nall Int toggle checks passed")


if __name__ == "__main__":
    main()
