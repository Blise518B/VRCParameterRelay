"""Dev preview harness: full UI + guest web server with stubbed data.

No mDNS, no OSC sockets — safe to run while VRChat is open.

  python tools/ui_preview.py --screenshot out.png   render offscreen and exit
  python tools/ui_preview.py --serve                keep running (guest page test)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

AVATAR = "avtr_deadbeef-0000-4000-8000-c0ffee000001"


class StubLink:
    def __init__(self) -> None:
        self.on_param = self.on_avatar = self.on_full_sync = self.on_status = None

    def send_param(self, name, ptype, value) -> bool:
        print(f"[stub] send {name} = {value!r}")
        return True

    def refetch(self) -> None:
        print("[stub] refetch requested")


def seed(core) -> None:
    core._on_avatar_change(AVATAR)
    for name, ptype, value in [
        ("Hoodie", "Bool", False), ("TailWag", "Bool", False), ("Hat", "Bool", True),
        ("GlowToggle", "Bool", False), ("Brightness", "Float", 0.75),
        ("TailSpeed", "Float", 0.2), ("OutfitIndex", "Int", 1), ("EmoteIndex", "Int", 0),
    ]:
        core._on_osc_param(name, ptype, value)

    if core.board["controls"]:
        return  # board persisted from a previous preview run
    cats = core.board["categories"]
    core.rename_category(cats[0]["id"], "Toggles")
    core.rename_category(cats[1]["id"], "NSFW")
    core.rename_category(cats[2]["id"], "Sliders")
    core.add_control("Hoodie", "toggle", "Shirt", category=cats[0]["id"])
    core.add_control("Hat", "toggle", "Hat", category=cats[0]["id"])
    core.add_control("TailWag", "toggle", "Gloves (inv)", category=cats[0]["id"], invert=True)
    core.add_control("GlowToggle", "toggle", "Nude", category=cats[1]["id"])
    core.add_control("Brightness", "slider", "Brightness", 0, 1, category=cats[2]["id"])
    core.add_control("OutfitIndex", "int", "Outfit", 0, 5, category=cats[2]["id"])
    core.set_category_locked(cats[1]["id"], True)
    core.rename_avatar("Demo Avatar")
    base = core.board["id"]
    core.add_preset("Alt layout")
    core.switch_preset(base)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--yolo", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    if args.screenshot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from vrc_parameter_relay.core import AppCore
    from vrc_parameter_relay.store import Store
    from vrc_parameter_relay.tunnel import Tunnel
    from vrc_parameter_relay.ui.main_window import MainWindow
    from vrc_parameter_relay.ui.theme import QSS
    from vrc_parameter_relay.webserver import GuestServer

    store = Store()
    link = StubLink()
    core = AppCore(store, link)
    web = GuestServer(core, store.settings["web_port"])
    tunnel = Tunnel(store)

    seed(core)
    core.set_sharing(True)  # preview the guest page without extra clicks
    if args.yolo:
        core.set_yolo(True)
    web.start()

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    window = MainWindow(core, tunnel, web)
    window.show()
    import time
    for _ in range(50):  # wait for the web thread to bind
        if web.port:
            break
        time.sleep(0.1)
    print(f"guest-url: http://127.0.0.1:{web.port}/?k={core.guest_token()}", flush=True)

    if args.screenshot:
        from PySide6.QtCore import QTimer

        def grab() -> None:
            window.resize(1150, 700)
            window.grab().save(args.screenshot)
            share_path = str(Path(args.screenshot).with_stem(
                Path(args.screenshot).stem + "-share"))
            window.share_dialog.resize(540, 360)
            window.share_dialog.grab().save(share_path)

            def grab_narrow() -> None:
                narrow_path = str(Path(args.screenshot).with_stem(
                    Path(args.screenshot).stem + "-narrow"))
                window.grab().save(narrow_path)
                print(f"screenshot saved: {args.screenshot} + {share_path} + {narrow_path}")
                app.quit()

            window.resize(760, 700)  # trigger the one-column re-layout
            QTimer.singleShot(400, grab_narrow)

        QTimer.singleShot(1500, grab)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
