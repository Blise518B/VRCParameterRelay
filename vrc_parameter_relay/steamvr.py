"""Launch-with-SteamVR via OpenVR's application manifest mechanism.

SteamVR keeps a registry of app manifests; apps flagged auto-launch are
started together with SteamVR (the same mechanism VRCFT & co. use).
Registration needs SteamVR to be RUNNING, so apply() is retried on every
app start while the setting is on — that also keeps the manifest pointing
at the current version-named exe after updates.

The openvr import is strictly optional: without it (or on Linux servers)
everything degrades to a clear message instead of an error.
"""
from __future__ import annotations

import json
import logging
import os
import sys

from .store import data_dir

log = logging.getLogger(__name__)

APP_KEY = "blise518b.vrcparameterrelay"


def manifest_path():
    return data_dir() / "vrcparameterrelay.vrmanifest"


def launch_target(mode: str = "tray") -> tuple[str, str]:
    """(binary, arguments) that start this install.

    mode "tray" starts minimized to the tray, "window" opens the normal
    window. Inside an AppImage sys.executable points at the temporary
    mount, which changes every run — $APPIMAGE is the stable path.
    """
    flag = "" if mode == "window" else "--tray"
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return appimage, flag
    if getattr(sys, "frozen", False):
        return sys.executable, flag
    return sys.executable, f"-m vrc_parameter_relay {flag}".strip()  # dev run


def build_manifest(mode: str = "tray") -> dict:
    """The OpenVR application manifest for the current install location."""
    binary, arguments = launch_target(mode)
    # SteamVR reads the key for the platform it runs on
    key = "binary_path_linux" if sys.platform.startswith("linux") \
        else "binary_path_windows"
    return {
        "source": "builtin",
        "applications": [{
            "app_key": APP_KEY,
            "launch_type": "binary",
            key: binary,
            "arguments": arguments,
            "is_dashboard_overlay": False,
            "strings": {"en_us": {
                "name": "VRC Parameter Relay",
                "description": "Remote control panel for VRChat avatar parameters",
            }},
        }],
    }


def write_manifest(mode: str = "tray") -> str:
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_manifest(mode), indent=2), "utf-8")
    return str(path)


def apply(enabled: bool, mode: str = "tray") -> str:
    """(Un)register auto-launch with SteamVR. Returns "" or an error text."""
    try:
        import openvr
    except ImportError:
        return "openvr module not available in this build"
    vr_started = False
    try:
        openvr.init(openvr.VRApplication_Utility)
        vr_started = True
        apps = openvr.VRApplications()
        if enabled:
            path = write_manifest(mode)
            apps.addApplicationManifest(path)
            apps.setApplicationAutoLaunch(APP_KEY, True)
            log.info("SteamVR auto-launch registered (%s)", path)
        else:
            try:
                apps.setApplicationAutoLaunch(APP_KEY, False)
                apps.removeApplicationManifest(str(manifest_path()))
            except Exception:  # noqa: BLE001 — best effort on unregister
                pass
            log.info("SteamVR auto-launch removed")
        return ""
    except Exception as exc:  # noqa: BLE001 — openvr raises plain Exceptions
        return (f"couldn't talk to SteamVR ({exc}) — is it running? "
                "I'll retry on the next app start.")
    finally:
        if vr_started:
            try:
                import openvr
                openvr.shutdown()
            except Exception:  # noqa: BLE001
                pass


def refresh(settings: dict) -> None:
    """Re-apply on app start while enabled (silently — SteamVR may be off)."""
    if settings.get("steamvr_autolaunch"):
        error = apply(True, settings.get("steamvr_mode", "tray"))
        if error:
            log.info("SteamVR auto-launch refresh skipped: %s", error)
