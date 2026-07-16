"""App entry point: wires store, OSC link, core, web server, tunnel and UI."""
from __future__ import annotations

import argparse
import logging
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="vrc_parameter_relay")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    parser.add_argument("--screenshot", metavar="PATH",
                        help="dev: render the window offscreen, save a PNG, exit")
    args = parser.parse_args()

    log_kwargs: dict = {
        "level": logging.DEBUG if args.verbose else logging.INFO,
        "format": "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    }
    if sys.stderr is None:  # windowed (no-console) PyInstaller build
        from .store import data_dir
        data_dir().mkdir(parents=True, exist_ok=True)
        log_kwargs["filename"] = str(data_dir() / "vrc_parameter_relay.log")
    logging.basicConfig(**log_kwargs)

    if args.screenshot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from .core import AppCore
    from .osc_link import VrcLink
    from .store import Store
    from .tunnel import Tunnel
    from .ui.main_window import MainWindow
    from .ui.theme import QSS
    from .webserver import GuestServer

    store = Store()
    link = VrcLink(store)
    core = AppCore(store, link)
    web = GuestServer(core, store.settings["web_port"])
    tunnel = Tunnel(store)

    from . import APP_NAME

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    app.setApplicationName(APP_NAME)

    from PySide6.QtGui import QIcon

    from . import resource_path
    icon_file = resource_path("assets/icon.ico")
    if icon_file.exists():
        app.setWindowIcon(QIcon(str(icon_file)))

    window = MainWindow(core, tunnel, web)

    link.start()
    web.start()
    window.show()

    if store.settings.get("share_autostart"):
        import threading
        import time

        def autostart() -> None:
            for _ in range(100):  # wait for the web server to bind
                if web.port:
                    break
                time.sleep(0.1)
            if web.port:
                core.set_sharing(True)
                tunnel.start(web.port)

        threading.Thread(target=autostart, daemon=True).start()

    def shutdown() -> None:
        tunnel.stop()
        link.stop()

    app.aboutToQuit.connect(shutdown)

    if args.screenshot:
        from PySide6.QtCore import QTimer

        def grab() -> None:
            window.resize(1060, 640)
            pixmap = window.grab()
            pixmap.save(args.screenshot)
            print(f"screenshot saved: {args.screenshot}")
            app.quit()

        QTimer.singleShot(6000, grab)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
