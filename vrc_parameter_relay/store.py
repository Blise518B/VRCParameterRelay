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
    "last_avatar_id": None,
    "tunnel_provider": "cloudflare",  # or "ngrok" (static domain)
    "ngrok_authtoken": "",
    "ngrok_domain": "",
    "share_autostart": False,
    "yolo_enabled": False,
    "skip_update_version": "",  # release tag the user chose not to be reminded about
    "window_size": None,        # [w, h] of the main window, restored on start
    "window_maximized": False,
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

    # -- avatar profiles ---------------------------------------------------

    def _profile_path(self, avatar_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_\-]", "_", str(avatar_id))[:120]
        return self.avatar_dir / f"{safe}.json"

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


def default_categories() -> list[dict[str, Any]]:
    return [
        {"id": new_control_id(), "name": f"Category {i + 1}", "locked": False}
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


def new_token() -> str:
    return secrets.token_hex(16)


def new_control_id() -> str:
    return secrets.token_hex(6)


def short_avatar_name(avatar_id: str) -> str:
    m = re.match(r"^avtr_([0-9a-fA-F]{8})", str(avatar_id))
    return f"Avatar {m.group(1)}" if m else str(avatar_id)
