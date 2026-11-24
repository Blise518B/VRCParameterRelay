"""Debug log: one file that shows what the app saw, to send to whoever helps.

⚙ Settings → Export debug log saves it to the Downloads folder. It holds

  * the worn avatar's parameters — as the app holds them, and as VRChat's
    OSCQuery reports them at that moment, with the names only one side has
  * the board state (viewed / live avatar, cards whose parameter is missing)
  * settings.json, every saved board (avatars/*.json), a listing of the
    data folder and the end of the log file

Secrets never leave: the guest and admin tokens, the share password and the
ngrok token are blanked in the settings and scrubbed from the whole file
(the log can carry old share links), and the fixed link's key file is never
read. Qt-free, so headless can use it too.
"""
from __future__ import annotations

import json
import platform
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import APP_NAME, __version__, oscquery
from .store import SHARE_WORDS, data_dir

LOG_FILE = "vrc_parameter_relay.log"
LOG_TAIL_BYTES = 1_000_000          # the end of the log is what matters
LISTING_MAX = 500                   # files named in the data-folder listing
AVATARS_MAX_BYTES = 8_000_000       # past this only the worn / viewed boards
SECRET_KEYS = ("guest_token", "admin_token", "share_password", "ngrok_authtoken")
HIDDEN = "<hidden>"

# a guest / admin token (also inside old links in the log) ...
_TOKEN = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")
# ... and a share password, current or one from before a reset
_WORD = "(?:" + "|".join(sorted(SHARE_WORDS, key=len, reverse=True)) + ")"
_PASSWORD = re.compile(rf"\b{_WORD}-{_WORD}-{_WORD}-\d{{2}}\b")


def file_name(now: Optional[datetime] = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    return f"VRCParameterRelay-debug-{stamp}.json"


def export(core, path: Path) -> Path:
    """Write the debug log for `core` to `path`. Returns the path."""
    report = build_report(core)
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    text = scrub(text, _secret_values(core))
    path = Path(path)
    path.write_text(text + "\n", "utf-8")
    return path


def build_report(core, fetch_tree: bool = True) -> dict[str, Any]:
    folder = getattr(core.store, "dir", None) or data_dir()
    params = core.param_snapshot()
    report: dict[str, Any] = {
        "report": f"{APP_NAME} debug log",
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "app": _app_info(),
        "vrchat": _vrchat_info(core),
        "avatar": _avatar_info(core),
        "parameters": {
            "count": len(params),
            "list": {name: {"type": p.get("ptype"), "value": p.get("value")}
                     for name, p in sorted(params.items())},
        },
    }
    if fetch_tree:
        report["vrchat_tree"] = _vrchat_tree(core, params)
    report["settings"] = _settings(core, folder)
    report["avatars"] = _avatar_files(core, folder)
    report["data_folder"] = _listing(folder)
    report["log"] = _log_tail(folder)
    return report


def scrub(text: str, secrets: list[str]) -> str:
    """Blank every secret in the finished text: the current values, plus
    anything shaped like a token or a share password (old links in the log
    carry tokens that were reset since)."""
    for value in sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True):
        text = text.replace(value, HIDDEN)
    text = _TOKEN.sub(HIDDEN, text)
    return _PASSWORD.sub(HIDDEN, text)


# -- sections -------------------------------------------------------------------

def _app_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "version": __version__,
        "build": "exe" if getattr(sys, "frozen", False) else "source",
        "executable": sys.executable,
        "os": platform.platform(),
        "python": platform.python_version(),
    }
    try:
        import PySide6
        info["pyside6"] = PySide6.__version__
    except Exception:  # noqa: BLE001 — headless without Qt
        pass
    return info


def _vrchat_info(core) -> dict[str, Any]:
    link = core.link
    out: dict[str, Any] = {"oscquery_url": getattr(link, "vrchat_http", None)}
    status = getattr(link, "status", None)
    if callable(status):
        try:
            out.update(status())
        except Exception as exc:  # noqa: BLE001
            out["status_error"] = repr(exc)
    clients = getattr(link, "_vrchat_clients", None)
    if isinstance(clients, dict):
        out["clients_seen"] = {name: {"addresses": addrs, "port": port}
                               for name, (addrs, port) in clients.items()}
    return out


def _avatar_info(core) -> dict[str, Any]:
    board = core.board or {}
    try:
        missing = sorted(core.missing_params())
    except Exception as exc:  # noqa: BLE001
        missing = [f"error: {exc!r}"]
    return {
        "live_avatar_id": core.live_avatar_id,
        "viewed_avatar_id": core.avatar_id,
        "viewed_name": core.avatar_name,
        "is_live": core.is_live,
        # VRChat's full list arrived for the live avatar (cards may be grey)
        "full_list_received": bool(getattr(core, "_live_synced", False)),
        "active_preset": {"id": board.get("id"), "name": board.get("name")},
        "cards": len(board.get("controls") or []),
        "cards_with_missing_parameter": missing,
        "sharing": bool(getattr(core, "sharing_enabled", False)),
        "yolo": bool(getattr(core, "yolo_enabled", False)),
    }


def _vrchat_tree(core, params: dict) -> dict[str, Any]:
    """What VRChat's OSCQuery says right now, raw, and how it differs from
    the list the app holds."""
    base = getattr(core.link, "vrchat_http", None)
    if not base:
        return {"fetched": False, "why": "VRChat isn't connected"}
    out: dict[str, Any] = {"fetched": False, "from": base}
    try:
        out["host_info"] = _get_json(base + "/?HOST_INFO")
        tree = _get_json(base + "/")
    except Exception as exc:  # noqa: BLE001
        out["why"] = f"{type(exc).__name__}: {exc}"
        return out
    out["fetched"] = True
    avatar_id, reported = oscquery.parse_vrc_tree(tree)
    names = {name for name, _, _ in reported}
    out["avatar_id"] = avatar_id
    out["count"] = len(reported)
    out["only_in_app"] = sorted(set(params) - names)
    out["only_in_vrchat"] = sorted(names - set(params))
    out["avatar"] = (tree.get("CONTENTS") or {}).get("avatar")
    return out


def _get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=4) as res:
        return json.loads(res.read().decode("utf-8"))


def _settings(core, folder: Path) -> dict[str, Any]:
    path = folder / "settings.json"
    out: dict[str, Any] = {"file": str(path)}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        out["file_error"] = f"{type(exc).__name__}: {exc}"
        data = dict(core.store.settings)
        out["source"] = "in memory"
    if isinstance(data, dict):
        for key in data:
            if key in SECRET_KEYS or "token" in key or "password" in key:
                data[key] = HIDDEN if data[key] else data[key]
    out["content"] = data
    return out


def _avatar_files(core, folder: Path) -> dict[str, Any]:
    avatar_dir = folder / "avatars"
    files = sorted(avatar_dir.glob("*.json")) if avatar_dir.is_dir() else []
    # the worn and the viewed avatar first; they always go in
    wanted = {core.store._profile_path(a).name
              for a in (core.live_avatar_id, core.avatar_id) if a}
    files.sort(key=lambda p: p.name not in wanted)
    out: dict[str, Any] = {}
    total = 0
    for path in files:
        try:
            size = path.stat().st_size
            if path.name not in wanted and total + size > AVATARS_MAX_BYTES:
                out[path.name] = f"left out ({size} bytes; the file got too big)"
                continue
            raw = path.read_text("utf-8")
            total += size
        except OSError as exc:
            out[path.name] = f"unreadable: {exc}"
            continue
        try:
            out[path.name] = json.loads(raw)
        except ValueError as exc:
            out[path.name] = {"invalid_json": str(exc), "raw": raw[:200_000]}
    return out


def _listing(folder: Path) -> list[dict[str, Any]]:
    out = []
    try:
        paths = sorted(folder.rglob("*"))
    except OSError:
        return out
    for path in paths[:LISTING_MAX]:
        try:
            st = path.stat()
        except OSError:
            continue
        out.append({
            "path": path.relative_to(folder).as_posix() + ("/" if path.is_dir() else ""),
            "size": None if path.is_dir() else st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        })
    return out


def _log_tail(folder: Path) -> dict[str, Any]:
    path = folder / LOG_FILE
    if not path.is_file():
        return {"file": None,
                "note": "no log file — a run from source logs to the console"}
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > LOG_TAIL_BYTES:
                fh.seek(size - LOG_TAIL_BYTES)
            data = fh.read()
    except OSError as exc:
        return {"file": str(path), "error": str(exc)}
    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > LOG_TAIL_BYTES and lines:
        lines = lines[1:]  # the first line was cut in half
    return {"file": str(path), "size": size,
            "showing": "the end" if size > LOG_TAIL_BYTES else "all of it",
            "lines": lines}


def _secret_values(core) -> list[str]:
    settings = getattr(core.store, "settings", {}) or {}
    values = [str(settings.get(k) or "") for k in SECRET_KEYS]
    try:  # the file can hold others than memory (another instance wrote it)
        saved = json.loads((core.store.dir / "settings.json").read_text("utf-8"))
        values += [str(saved.get(k) or "") for k in SECRET_KEYS]
    except (OSError, ValueError, AttributeError):
        pass
    return values
