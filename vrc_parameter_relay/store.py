"""Settings and per-avatar board persistence.

Data lives in %APPDATA%/VRCParameterRelay (override with
VRC_PARAMETER_RELAY_DATA_DIR) so a packaged exe and a dev checkout share
the same boards.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any, Optional

from . import DATA_DIR_NAME

DEFAULT_SETTINGS: dict[str, Any] = {
    "web_port": 3080,
    "osc_send_host": "127.0.0.1",   # fallback — real target comes from VRChat's OSCQuery HOST_INFO
    "osc_send_port": 9000,
    "guest_token": None,             # generated on first run
    # separate secret for the admin panel — a guest link must NEVER grant it
    "admin_token": None,             # generated on first run
    "admin_enabled": True,           # serve /admin at all
    # admin panel is LAN/loopback-only by default: a request that arrived
    # through the reverse proxy or tunnel (i.e. from the internet) is refused.
    # Turn on only for deliberate remote administration of a headless relay.
    "admin_public": False,
    # Sharing on your own address (docs/REMOTE-ACCESS.md): the bare address
    # carries NO secret. Guests type this spoken password once and their
    # browser keeps a cookie; the long token stays for one-click links.
    # Generated on first run.
    "share_password": "",
    # Your own address, e.g. "https://relay.example.com" — set in the Share
    # window. Empty = share through a tunnel. Set = no tunnel: the link is
    # this address, and the listener opens to the LAN so whatever routes the
    # address here (port forward, reverse proxy, Cloudflare Tunnel) reaches it.
    "public_url": "",
    # Extra proxy address(es), comma-separated, whose X-Forwarded-For is
    # believed. Connections from this machine and the LAN already are; this
    # is only for a proxy outside both. A peer listed here is also treated
    # as the public path, so it never reaches the admin panel.
    "trusted_proxy": "",
    "last_avatar_id": None,
    "tunnel_provider": "cloudflare",  # or "ngrok" (static domain)
    "ngrok_authtoken": "",
    "ngrok_domain": "",
    "advanced_link": False,     # unlocks the ngrok option in Link settings
    "share_autostart": False,
    "yolo_enabled": False,
    "require_guest_names": False,  # guests must introduce themselves first
    "skip_update_version": "",  # release tag the user chose not to be reminded about
    "window_size": None,        # [w, h] of the main window, restored on start
    "window_maximized": False,
    "theme": "neon",            # UI theme: "neon" (default) or "broker" (Midnight)
    "font": "Segoe UI",         # UI font family
    "close_to_tray": True,      # closing the window keeps the relay in the tray
    "steamvr_autolaunch": False,  # register with SteamVR's auto-launch list
    # how an auto-started instance comes up: "tray" or "window"
    "autostart_mode": "tray",     # start with Windows / the Linux desktop
    "steamvr_mode": "tray",       # launch with SteamVR
}


def data_dir() -> Path:
    override = os.getenv("VRC_PARAMETER_RELAY_DATA_DIR")
    if override:
        return Path(override)
    base = Path(os.getenv("APPDATA") or str(Path.home() / ".config"))
    return base / DATA_DIR_NAME


class Store:
    """Thread-safe access to settings.json and data/avatars/*.json boards."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.dir = data_dir()
        self.avatar_dir = self.dir / "avatars"
        self.avatar_dir.mkdir(parents=True, exist_ok=True)
        self.settings = self._load_settings()

    # -- settings ---------------------------------------------------------

    def _load_settings(self) -> dict[str, Any]:
        settings = dict(DEFAULT_SETTINGS)
        path = self.dir / "settings.json"
        try:
            settings.update(json.loads(path.read_text("utf-8")))
        except (OSError, ValueError):
            pass  # first run or corrupt file
        if not settings.get("guest_token"):
            settings["guest_token"] = new_token()
        if not settings.get("admin_token"):
            settings["admin_token"] = new_token()  # always distinct from the guest one
        if not settings.get("share_password"):
            settings["share_password"] = new_share_password()
        self._write_json(path, settings)
        return settings

    def save_settings(self) -> None:
        with self._lock:
            self._write_json(self.dir / "settings.json", self.settings)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self.settings[key] = value
            self.save_settings()

    def regenerate_guest_token(self) -> str:
        token = new_token()
        self.set("guest_token", token)
        return token

    def regenerate_admin_token(self) -> str:
        token = new_token()
        self.set("admin_token", token)
        return token

    def regenerate_share_password(self) -> str:
        password = new_share_password()
        self.set("share_password", password)
        return password

    # -- avatar profiles ---------------------------------------------------

    def _profile_path(self, avatar_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_\-]", "_", str(avatar_id))[:120]
        return self.avatar_dir / f"{safe}.json"

    def profile_mtime(self, avatar_id: str) -> float:
        """Last-modified time of the avatar's file on disk (0.0 if absent)."""
        try:
            return self._profile_path(avatar_id).stat().st_mtime
        except OSError:
            return 0.0

    def load_profile(self, avatar_id: str) -> dict[str, Any]:
        with self._lock:
            data: dict[str, Any] = {}
            try:
                loaded = json.loads(self._profile_path(avatar_id).read_text("utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, ValueError):
                pass
            return normalize_profile(data, avatar_id)

    def save_profile(self, profile: dict[str, Any]) -> None:
        with self._lock:
            self._write_json(self._profile_path(profile["avatar_id"]), profile)

    def list_profiles(self) -> list[dict[str, Any]]:
        """Saved avatars for the offline library: [{avatar_id, name, named}]."""
        out = []
        with self._lock:
            for path in sorted(self.avatar_dir.glob("*.json")):
                try:
                    data = json.loads(path.read_text("utf-8"))
                except (OSError, ValueError):
                    continue
                avatar_id = data.get("avatar_id")
                if not avatar_id:
                    continue
                name = data.get("name") or short_avatar_name(avatar_id)
                out.append({"avatar_id": avatar_id, "name": name,
                            "named": name != short_avatar_name(avatar_id)})
        return out

    @staticmethod
    def _write_json(path: Path, obj: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, indent=2), "utf-8")
        tmp.replace(path)


DEFAULT_CATEGORY_COUNT = 4  # a 2x2 grid out of the box

# valid per-category accent colours (the UI maps them to actual hues)
CATEGORY_COLORS = ("green", "blue", "cyan", "yellow", "red", "purple")

# a fresh board's 2x2 grid comes pre-coloured
DEFAULT_CATEGORY_COLOR_ORDER = ("red", "green", "yellow", "blue")


def default_categories() -> list[dict[str, Any]]:
    order = DEFAULT_CATEGORY_COLOR_ORDER
    return [
        {"id": new_control_id(), "name": f"Category {i + 1}", "locked": False,
         "color": order[i % len(order)]}
        for i in range(DEFAULT_CATEGORY_COUNT)
    ]


def normalize_preset(preset: dict[str, Any]) -> dict[str, Any]:
    """Ensure a preset has an id, name, categories, and valid control refs."""
    preset.setdefault("id", new_control_id())
    preset.setdefault("name", "Default")

    cats = preset.get("categories")
    if not isinstance(cats, list) or not cats:
        cats = default_categories()
    for cat in cats:
        cat.setdefault("id", new_control_id())
        cat.setdefault("name", "Category")
        cat["locked"] = bool(cat.get("locked"))
        if cat.get("color") not in CATEGORY_COLORS:
            cat["color"] = "green"
    preset["categories"] = cats

    controls = preset.get("controls")
    if not isinstance(controls, list):
        controls = []
    valid_ids = {c["id"] for c in cats}
    for ctrl in controls:
        if ctrl.get("cat") not in valid_ids:
            ctrl["cat"] = cats[0]["id"]
    preset["controls"] = controls
    return preset


def normalize_profile(data: dict[str, Any], avatar_id: str) -> dict[str, Any]:
    """Ensure an avatar profile has name, saved params, and >=1 preset.

    Migrates single-board files (v1.1.0 and earlier: categories/controls at
    the top level) into a profile with one "Default" preset.
    """
    data["avatar_id"] = avatar_id
    data.setdefault("name", short_avatar_name(avatar_id))

    params = data.get("params")
    data["params"] = params if isinstance(params, dict) else {}

    presets = data.get("presets")
    if not isinstance(presets, list) or not presets:
        # old single-board layout (or a fresh avatar)
        presets = [{"id": new_control_id(), "name": "Default",
                    "categories": data.pop("categories", None),
                    "controls": data.pop("controls", None)}]
    data["presets"] = [normalize_preset(p) for p in presets]
    data.pop("categories", None)
    data.pop("controls", None)

    preset_ids = {p["id"] for p in data["presets"]}
    if data.get("active_preset") not in preset_ids:
        data["active_preset"] = data["presets"][0]["id"]
    return data


#: Vocabulary for the spoken share password — the avatar, the board, the
#: room. Every word is short, common and unmistakable read out over a voice
#: call; nothing that could be misheard for another word on the list.
SHARE_WORDS = (
    # the avatar
    "avatar", "toggle", "gesture", "emote", "outfit", "hoodie", "visor",
    "wings", "tail", "ears", "glow", "shader", "mirror", "portal", "spawn",
    "puppet", "clone", "beacon", "tracker", "friend",
    # the board
    "slider", "button", "switch", "preset", "relay", "remote", "signal",
    "pulse", "orbit", "vertex", "pixel", "prism", "glitch", "drift",
    "static", "render", "instance", "lobby", "world", "desktop",
    # colour + light
    "neon", "chrome", "cobalt", "ember", "velvet", "amber", "violet",
    "crimson", "indigo", "silver", "golden", "aurora", "nebula", "comet",
    "lantern", "flicker", "shimmer", "starlight", "moonlit", "twilight",
    # the room
    "disco", "strobe", "laser", "tempo", "groove", "echo", "synth",
    "chorus", "anthem", "encore", "remix", "vinyl", "dancer", "confetti",
    "stage", "crowd", "spotlight", "skyline", "horizon", "sunrise",
    # the stream
    "stream", "capture", "preview", "overlay", "frame", "focus", "zoom",
    "lens", "filter", "gradient", "spectrum", "cascade", "polygon",
    "keyframe", "playback", "footage", "closeup", "highlight", "contrast",
    "luminous",
    # nature
    "river", "meadow", "forest", "summit", "canyon", "harbor", "island",
    "glacier", "thunder", "breeze", "blossom", "pebble", "willow", "maple",
    "cedar", "falcon", "otter", "fox", "raven", "tiger",
)


def new_share_password() -> str:
    """Three words and a number: strong enough to sit on a public address,
    short enough to say out loud without spelling anything.

    120 words cubed x 90 is about 155 million combinations, and the guest
    server blocks an address for 15 minutes after 8 wrong tries, so guessing
    is not a practical attack even though the hostname is public (every
    certificate is published to transparency logs).
    """
    picks = secrets.SystemRandom().sample(SHARE_WORDS, 3)
    return "-".join(picks) + f"-{secrets.randbelow(90) + 10}"


def new_token() -> str:
    return secrets.token_hex(16)


def new_control_id() -> str:
    return secrets.token_hex(6)


def short_avatar_name(avatar_id: str) -> str:
    m = re.match(r"^avtr_([0-9a-fA-F]{8})", str(avatar_id))
    return f"Avatar {m.group(1)}" if m else str(avatar_id)
