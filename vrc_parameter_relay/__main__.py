"""App entry point: wires store, OSC link, core, web server, tunnel and UI."""
from __future__ import annotations

import argparse
import logging
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="vrc_parameter_relay")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    parser.add_argument("--headless", action="store_true",
                        help="run without a window; sharing starts automatically")
    parser.add_argument("--tray", action="store_true",
                        help="start minimized to the system tray")
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

    # one relay per data folder — autostart + a double-click must not give
    # two instances fighting over the same boards and OSC stream
    from .single_instance import acquire
    from .store import data_dir as _data_dir
    guard = acquire(_data_dir())
    if guard is None:
        logging.getLogger(__name__).info(
            "already running for this data folder — showing that window instead")
        return 0

    if args.headless:
        from .headless import run as run_headless  # never touches Qt
        return run_headless(guard)

    if args.screenshot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from .core import AppCore
    from .osc_link import VrcLink
    from .store import Store
    from .tunnel import Tunnel
    from .ui.main_window import MainWindow
    from .ui.theme import build_qss, load_bundled_fonts
    from .webserver import GuestServer

    store = Store()
    link = VrcLink(store)
    core = AppCore(store, link)
    web = GuestServer(core, store.settings["web_port"])
    tunnel = Tunnel(store)

    from . import APP_NAME

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    load_bundled_fonts()  # register Orbitron & co. before styling
    app.setStyleSheet(build_qss(store.settings.get("theme"), store.settings.get("font")))
    app.setApplicationName(APP_NAME)

    from PySide6.QtGui import QIcon

    from . import resource_path
    icon_file = resource_path("assets/icon.ico")
    if icon_file.exists():
        app.setWindowIcon(QIcon(str(icon_file)))

    window = MainWindow(core, tunnel, web)
    # a second start (e.g. double-click while it sits in the tray) reveals
    # this window instead of launching a rival relay
    guard.on_activate = window.activate_requested.emit

    link.start()
    web.start()
    if args.tray and window.tray is not None:
        logging.getLogger(__name__).info("started minimized to the tray")
    else:
        window.show()

    # keep the autostart registrations pointing at the current (version-named)
    # exe; both are no-ops unless the user enabled them
    def refresh_autostarts() -> None:
        try:
            from . import autostart, steamvr
            autostart.refresh(store.settings)
            steamvr.refresh(store.settings)
        except Exception:
            logging.getLogger(__name__).exception("autostart refresh failed")

    import threading
    threading.Thread(target=refresh_autostarts, daemon=True).start()

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
                settings = store.settings
                if (settings.get("advanced_link")
                        and settings.get("tunnel_provider") == "selfhost"):
                    if settings.get("selfhost_upnp"):
                        from .upnp import map_async
                        map_async(settings, web.port, core.emit)
                else:
                    tunnel.start(web.port)

        threading.Thread(target=autostart, daemon=True).start()

    def shutdown() -> None:
        tunnel.stop()
        link.stop()
        from .upnp import cleanup_mappings
        cleanup_mappings()  # close any router port we opened
        guard.release()

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
