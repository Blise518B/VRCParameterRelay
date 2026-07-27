"""Start with the system, minimized to the tray.

Windows: the per-user Run registry key.
Linux:   an XDG autostart .desktop file in ~/.config/autostart.

Both paths go stale after an update (the exe is version-named) or after an
AppImage is moved, so refresh() rewrites the entry on every app start while
the feature is enabled.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "VRCParameterRelay"
DESKTOP_FILE = "vrcparameterrelay.desktop"

IS_WINDOWS = sys.platform == "win32"


def _target(mode: str = "tray") -> tuple[str, str]:
    """(binary, arguments) that start this install.

    mode "tray" starts minimized to the tray, "window" opens the normal
    window (at its remembered size). Inside an AppImage sys.executable
    points at the temporary mount, which changes every run — $APPIMAGE is
    the stable path to the .AppImage file.
    """
    flag = "" if mode == "window" else "--tray"
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return appimage, flag
    if getattr(sys, "frozen", False):
        return sys.executable, flag
    return sys.executable, f"-m vrc_parameter_relay {flag}".strip()  # dev run


def command(mode: str = "tray") -> str:
    """The full autostart command line."""
    binary, arguments = _target(mode)
    return f'"{binary}" {arguments}'.strip()


def _desktop_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / DESKTOP_FILE


def _desktop_entry(mode: str = "tray") -> str:
    binary, arguments = _target(mode)
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=VRC Parameter Relay\n"
        "Comment=Remote control panel for VRChat avatar parameters\n"
        f"Exec={binary} {arguments}".rstrip() + "\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def is_enabled() -> bool:
    if not IS_WINDOWS:
        return _desktop_path().exists()
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except (ImportError, OSError):
        return False


def set_enabled(enabled: bool, mode: str = "tray") -> str:
    """Enable/disable; returns "" on success or a human-readable error."""
    if not IS_WINDOWS:
        path = _desktop_path()
        try:
            if enabled:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(_desktop_entry(mode), "utf-8")
                log.info("autostart .desktop written: %s", path)
            else:
                path.unlink(missing_ok=True)
                log.info("autostart .desktop removed")
            return ""
        except OSError as exc:
            return f"could not update {path} ({exc})"
    try:
        import winreg
    except ImportError:  # pragma: no cover — win32 without winreg
        return "start-with-Windows is not available here"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command(mode))
                log.info("autostart registered: %s", command(mode))
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
                log.info("autostart removed")
        return ""
    except OSError as exc:
        return f"could not update the autostart entry ({exc})"


def refresh(settings: dict) -> None:
    """Keep the registered command pointing at the current exe/AppImage."""
    if is_enabled():
        set_enabled(True, settings.get("autostart_mode", "tray"))
