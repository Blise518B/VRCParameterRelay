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

    # -- boards -----------------------------------------------------------

    def _board_path(self, avatar_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_\-]", "_", str(avatar_id))[:120]
        return self.avatar_dir / f"{safe}.json"

    def load_board(self, avatar_id: str) -> dict[str, Any]:
        with self._lock:
            board: dict[str, Any] = {}
            try:
                loaded = json.loads(self._board_path(avatar_id).read_text("utf-8"))
                if isinstance(loaded, dict):
                    board = loaded
            except (OSError, ValueError):
                pass
            return normalize_board(board, avatar_id)

    def save_board(self, board: dict[str, Any]) -> None:
        with self._lock:
            self._write_json(self._board_path(board["avatar_id"]), board)

    @staticmethod
    def _write_json(path: Path, obj: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, indent=2), "utf-8")
        tmp.replace(path)


DEFAULT_CATEGORY_COUNT = 4  # a 2x2 grid out of the box


def normalize_board(board: dict[str, Any], avatar_id: str) -> dict[str, Any]:
    """Ensure a board has a name, categories, and valid control->category refs.

    Also migrates pre-category boards: their controls land in the first
    default category.
    """
    board["avatar_id"] = avatar_id
    board.setdefault("name", short_avatar_name(avatar_id))

    cats = board.get("categories")
    if not isinstance(cats, list) or not cats:
        cats = [
            {"id": new_control_id(), "name": f"Category {i + 1}", "locked": False}
            for i in range(DEFAULT_CATEGORY_COUNT)
        ]
    for cat in cats:
        cat.setdefault("id", new_control_id())
        cat.setdefault("name", "Category")
        cat["locked"] = bool(cat.get("locked"))
    board["categories"] = cats

    controls = board.get("controls")
    if not isinstance(controls, list):
        controls = []
    valid_ids = {c["id"] for c in cats}
    for ctrl in controls:
        if ctrl.get("cat") not in valid_ids:
            ctrl["cat"] = cats[0]["id"]
    board["controls"] = controls
    return board


def new_token() -> str:
    return secrets.token_hex(16)


def new_control_id() -> str:
    return secrets.token_hex(6)


def short_avatar_name(avatar_id: str) -> str:
    m = re.match(r"^avtr_([0-9a-fA-F]{8})", str(avatar_id))
    return f"Avatar {m.group(1)}" if m else str(avatar_id)
