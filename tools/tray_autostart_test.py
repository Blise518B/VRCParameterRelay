"""Hermetic test of tray/autostart plumbing — no registry writes, no SteamVR.

Covers the pure parts: the autostart command line, the SteamVR manifest,
settings defaults, and that the tray-less offscreen path still closes the
window normally. Safe anywhere. Exits 0 on success.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def check(desc: str, cond: bool) -> None:
    if not cond:
        print(f"FAIL {desc}")
        sys.exit(1)
    print(f"OK   {desc}")


def main() -> None:
    from vrc_parameter_relay import autostart, steamvr
    from vrc_parameter_relay.store import DEFAULT_SETTINGS

    # settings defaults
    check("close_to_tray defaults ON", DEFAULT_SETTINGS["close_to_tray"] is True)
    check("steamvr autostart defaults OFF",
          DEFAULT_SETTINGS["steamvr_autolaunch"] is False)
    check("both start modes default to tray",
          DEFAULT_SETTINGS["autostart_mode"] == "tray"
          and DEFAULT_SETTINGS["steamvr_mode"] == "tray")

    # windows autostart command — both start modes
    cmd = autostart.command()
    check("autostart command is quoted", cmd.startswith('"'))
    check("autostart defaults to minimized", "--tray" in cmd)
    check("autostart points at this interpreter/exe", sys.executable in cmd)
    win_cmd = autostart.command("window")
    check("window mode drops the --tray flag", "--tray" not in win_cmd)
    check("window mode still points at the app", sys.executable in win_cmd)
    check("no trailing space in window-mode command", win_cmd == win_cmd.strip())
    check("window-mode desktop entry has a clean Exec",
          "Exec=" in autostart._desktop_entry("window")
          and not any(line.endswith(" ") for line
                      in autostart._desktop_entry("window").splitlines()))

    # steamvr manifest — both start modes
    manifest = steamvr.build_manifest()
    app = manifest["applications"][0]
    check("manifest app key stable", app["app_key"] == steamvr.APP_KEY)
    check("manifest defaults to minimized", "--tray" in app["arguments"])
    check("manifest binary is this interpreter/exe",
          app["binary_path_windows"] == sys.executable)
    check("manifest window mode drops --tray",
          "--tray" not in steamvr.build_manifest("window")["applications"][0]["arguments"])
    path = steamvr.write_manifest()
    on_disk = json.loads(Path(path).read_text("utf-8"))
    check("manifest written & valid json",
          on_disk["applications"][0]["app_key"] == steamvr.APP_KEY)

    # steamvr apply() fails soft without SteamVR running (never raises)
    result = steamvr.apply(False)
    check("steamvr apply degrades to a message, no exception",
          isinstance(result, str))

    # -- cross-platform behaviour (simulated Linux / AppImage) ------------
    import importlib
    from unittest import mock

    with mock.patch.object(sys, "platform", "linux"):
        importlib.reload(steamvr)
        importlib.reload(autostart)
        keys = [k for k in steamvr.build_manifest()["applications"][0]
                if k.startswith("binary_path")]
        check("linux manifest uses binary_path_linux", keys == ["binary_path_linux"])
        check("linux autostart uses an XDG .desktop file",
              autostart._desktop_path().name.endswith(".desktop")
              and "autostart" in autostart._desktop_path().parts)
        entry = autostart._desktop_entry()
        check("desktop entry is a valid autostart stanza",
              entry.startswith("[Desktop Entry]") and "Exec=" in entry
              and "--tray" in entry)

        # inside an AppImage the temp mount path is useless — $APPIMAGE wins
        with mock.patch.dict(os.environ, {"APPIMAGE": "/opt/VRCPR.AppImage"}):
            importlib.reload(steamvr)
            importlib.reload(autostart)
            check("AppImage: steamvr points at the .AppImage",
                  steamvr.launch_target()[0] == "/opt/VRCPR.AppImage")
            check("AppImage: autostart points at the .AppImage",
                  "/opt/VRCPR.AppImage" in autostart.command())

    importlib.reload(steamvr)  # restore real-platform modules
    importlib.reload(autostart)

    # the AppImage launcher must forward argv or Linux users can't pick a mode
    apprun = (Path(__file__).parent.parent / "vrc_parameter_relay" / "appimage"
              / "AppRun").read_text(encoding="utf-8")
    check("AppRun forwards arguments", '"$@"' in apprun)

    # the LAN address the UI/headless show for self-hosting
    from vrc_parameter_relay.webserver import lan_ip
    ip = lan_ip()
    check("lan_ip returns a non-loopback address or nothing",
          ip == "" or not ip.startswith("127."))

    # tray-less close path: offscreen has no system tray -> window must close
    # normally (no hidden zombie), which update_test's win.close() also relies on
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from vrc_parameter_relay.core import AppCore
    from vrc_parameter_relay.store import Store
    from vrc_parameter_relay.tunnel import Tunnel
    from vrc_parameter_relay.ui.main_window import MainWindow

    class StubLink:
        on_param = on_avatar = on_full_sync = on_status = None

        def send_param(self, *a):
            return True

        def refetch(self):
            pass

    qapp = QApplication([])
    store = Store()
    core = AppCore(store, StubLink())
    web = type("Web", (), {"port": 0, "requested_port": 3080})()
    win = MainWindow(core, Tunnel(store), web)
    if QSystemTrayIcon.isSystemTrayAvailable():
        check("tray built where available", win.tray is not None)
        win.quit_app()
        check("quit_app really closes despite close-to-tray", win.isHidden())
    else:
        check("no tray offscreen -> tray is None", win.tray is None)
        win.close()
        check("close works normally without a tray", win.isHidden())

    # -- sharing gate: the board STOP/RESUME button must always work ----------
    # A tray-copied link used to land guests on a paused board while this
    # button sat disabled in a third "off" state with no way to resume.
    class StubTunnel:
        state = "off"
        url = None

        def __init__(self):
            self.started = []
            self.on_change = lambda st: None

        def provider(self):
            return "cloudflare"

        def start(self, port):
            self.started.append(port)
            self.state = "starting"

        def stop(self):
            self.state = "off"

        def restart(self, port):
            self.start(port)

    core2 = AppCore(Store(), StubLink())
    web2 = type("Web", (), {"port": 3081, "requested_port": 3081})()
    tun = StubTunnel()
    win2 = MainWindow(core2, tun, web2)
    check("fresh start shows the paused state (START), not a dead button",
          win2.pause_btn.isEnabled() and win2.pause_btn.text() == "▶ START"
          and win2.pause_btn.property("state") == "paused")
    win2._toggle_pause()
    check("board button starts sharing for real (tunnel included)",
          core2.sharing_enabled and tun.started == [3081])
    check("…and flips to the emergency stop", win2.pause_btn.text() == "■ STOP")
    win2._toggle_pause()
    check("board button pauses again and cancels the young tunnel",
          not core2.sharing_enabled and tun.state == "off")
    tun.state = "online"
    win2._refresh_pause_btn()
    check("paused with a live link reads RESUME",
          win2.pause_btn.text() == "▶ RESUME" and win2.pause_btn.isEnabled())
    win2.share_dialog._toggle()
    check("Share dialog start goes through the same path",
          core2.sharing_enabled and win2.pause_btn.text() == "■ STOP")
    win2._quitting = True
    win2.close()

    print("ALL TRAY/AUTOSTART TESTS PASSED")


if __name__ == "__main__":
    main()
