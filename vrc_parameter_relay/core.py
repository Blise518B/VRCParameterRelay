"""AppCore — thread-safe application state and event hub.

Everything (OSC threads, the Qt UI, the guest web server, the tunnel)
talks through this object. Listeners receive plain-dict events:

  {"t": "param", "name", "ptype", "value"}
  {"t": "params_reset", "params": {name: {"ptype", "value"}}}
  {"t": "avatar", "id", "board", "values"}
  {"t": "board", "board", "values"}        (after edits)
  {"t": "vrc_status", ...}
  {"t": "tunnel", ...}
  {"t": "guests", "count"}
  {"t": "token", "token"}
  {"t": "sharing", "enabled"}
  {"t": "yolo", "enabled"}
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from .store import Store, new_control_id, normalize_board, short_avatar_name

log = logging.getLogger(__name__)

CONTROL_KINDS = ("toggle", "button", "slider", "int")


class AppCore:
    def __init__(self, store: Store, link) -> None:
        self.store = store
        self.link = link
        self._lock = threading.RLock()
        self._listeners: list[Callable[[dict], None]] = []

        self.params: dict[str, dict[str, Any]] = {}  # name -> {"ptype", "value"}
        self.avatar_id: Optional[str] = store.settings.get("last_avatar_id")
        self.board: dict[str, Any] = (
            store.load_board(self.avatar_id) if self.avatar_id
            else normalize_board({"name": "No avatar yet"}, None)
        )
        self.sharing_enabled = False  # runtime gate; guests are rejected while off
        self.yolo_enabled = bool(store.settings.get("yolo_enabled"))

        link.on_param = self._on_osc_param
        link.on_avatar = self._on_avatar_change
        link.on_full_sync = self._on_full_sync
        link.on_status = lambda st: self.emit({"t": "vrc_status", **st})

    # -- listeners -----------------------------------------------------------

    def add_listener(self, fn: Callable[[dict], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def emit(self, event: dict) -> None:
        """Publish an event to all listeners (UI bridge, web hub, ...)."""
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(event)
            except Exception:
                log.exception("listener failed for %s", event.get("t"))

    # -- OSC-side events -------------------------------------------------------

    def _on_osc_param(self, name: str, ptype: str, value: Any) -> None:
        with self._lock:
            prev = self.params.get(name)
            self.params[name] = {"ptype": ptype, "value": value}
        if not prev or prev["value"] != value or prev["ptype"] != ptype:
            self.emit({"t": "param", "name": name, "ptype": ptype, "value": value})

    def _on_avatar_change(self, avatar_id: str) -> None:
        with self._lock:
            if avatar_id == self.avatar_id:
                return
            self.avatar_id = avatar_id
            self.params = {}
            self.board = self.store.load_board(avatar_id)
            self.store.set("last_avatar_id", avatar_id)
        log.info("avatar changed: %s", avatar_id)
        self.emit({"t": "avatar", "id": avatar_id, "board": self.board, "values": self.board_values()})

    def _on_full_sync(self, avatar_id: Optional[str], params: list) -> None:
        """Complete parameter list from VRChat's OSCQuery tree.

        Runs on every avatar change AND on the periodic fallback poll, so it
        only broadcasts a rebuild when the parameter set actually changed —
        values alone stream live via 'param' events.
        """
        if avatar_id:
            self._on_avatar_change(avatar_id)
        with self._lock:
            before = {(n, p["ptype"]) for n, p in self.params.items()}
            for name, ptype, value in params:
                cur = self.params.get(name)
                self.params[name] = {
                    "ptype": ptype,
                    "value": value if value is not None else (cur or {}).get("value"),
                }
            after = {(n, p["ptype"]) for n, p in self.params.items()}
            changed = before != after
            snapshot = {k: dict(v) for k, v in self.params.items()} if changed else None
        if changed:
            log.info("OSCQuery sync: %d parameters", len(params))
            self.emit({"t": "params_reset", "params": snapshot})
            self._emit_board()  # push fresh values to guests too

    # -- values ---------------------------------------------------------------

    def board_values(self) -> dict[str, Any]:
        """Current values for parameters used on the board (what guests may see)."""
        with self._lock:
            names = {c["param"] for c in self.board["controls"]}
            return {n: self.params[n]["value"] for n in names if n in self.params}

    def param_snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self.params.items()}

    # -- control actions (host UI and guests) -----------------------------------

    def set_control_value(self, control_id: str, value: Any, source: str = "host") -> bool:
        with self._lock:
            control = next((c for c in self.board["controls"] if c["id"] == control_id), None)
            category = None
            if control:
                category = next(
                    (c for c in self.board["categories"] if c["id"] == control.get("cat")), None)
        if not control:
            return False
        if source == "guest" and category and category.get("locked"):
            return False  # locked categories are read-only for guests
        ptype = control["ptype"]
        try:
            if ptype == "Bool":
                value = bool(value)
                if control.get("invert"):
                    value = not value  # control shows/receives the flipped state
            elif ptype in ("Int", "Float"):
                value = float(value)
                lo, hi = control.get("min"), control.get("max")
                if lo is not None:
                    value = max(float(lo), value)
                if hi is not None:
                    value = min(float(hi), value)
                if ptype == "Int":
                    value = int(round(value))
        except (TypeError, ValueError):
            return False
        ok = self.link.send_param(control["param"], ptype, value)
        if ok:
            self._on_osc_param(control["param"], ptype, value)  # optimistic echo
        return ok

    def set_param_direct(self, name: str, ptype: str, value: Any) -> bool:
        """Host-only: set any parameter (from the parameter browser)."""
        ok = self.link.send_param(name, ptype, value)
        if ok:
            self._on_osc_param(name, ptype, value)
        return ok

    # -- board edits (host only) --------------------------------------------------

    def add_control(self, param: str, kind: str, label: Optional[str] = None,
                    vmin: Optional[float] = None, vmax: Optional[float] = None,
                    category: Optional[str] = None, invert: bool = False) -> Optional[dict]:
        if kind not in CONTROL_KINDS:
            return None
        with self._lock:
            if not self.avatar_id:
                return None
            info = self.params.get(param, {})
            ptype = {"toggle": "Bool", "button": "Bool", "slider": "Float", "int": "Int"}[kind]
            if info.get("ptype"):
                ptype = info["ptype"]
            cat_ids = {c["id"] for c in self.board["categories"]}
            control = {
                "id": new_control_id(),
                "param": param,
                "ptype": ptype,
                "kind": kind,
                "label": label or param,
                "cat": category if category in cat_ids else self.board["categories"][0]["id"],
            }
            if kind in ("slider", "int"):
                control["min"] = vmin if vmin is not None else 0
                control["max"] = vmax if vmax is not None else (1 if kind == "slider" else 255)
            if ptype == "Bool" and invert:
                control["invert"] = True
            self.board["controls"].append(control)
            self.store.save_board(self.board)
        self._emit_board()
        return control

    def update_control(self, control_id: str, **changes: Any) -> bool:
        with self._lock:
            control = next((c for c in self.board["controls"] if c["id"] == control_id), None)
            if not control:
                return False
            for key in ("label", "min", "max", "kind", "invert"):
                if key in changes and changes[key] is not None:
                    control[key] = changes[key]
            self.store.save_board(self.board)
        self._emit_board()
        return True

    def remove_control(self, control_id: str) -> bool:
        with self._lock:
            before = len(self.board["controls"])
            self.board["controls"] = [c for c in self.board["controls"] if c["id"] != control_id]
            changed = len(self.board["controls"]) != before
            if changed:
                self.store.save_board(self.board)
        if changed:
            self._emit_board()
        return changed

    def move_control_to_category(self, control_id: str, cat_id: str,
                                 index: Optional[int] = None) -> bool:
        """Drag & drop: place a control into a category at the given position."""
        with self._lock:
            controls = self.board["controls"]
            control = next((c for c in controls if c["id"] == control_id), None)
            if not control or cat_id not in {c["id"] for c in self.board["categories"]}:
                return False
            controls.remove(control)
            control["cat"] = cat_id
            siblings = [i for i, c in enumerate(controls) if c["cat"] == cat_id]
            if index is None or index >= len(siblings):
                insert_at = (siblings[-1] + 1) if siblings else len(controls)
            else:
                insert_at = siblings[max(0, index)]
            controls.insert(insert_at, control)
            self.store.save_board(self.board)
        self._emit_board()
        return True

    # -- categories (host only) ------------------------------------------------

    def add_category(self, name: Optional[str] = None) -> Optional[dict]:
        with self._lock:
            cats = self.board["categories"]
            cat = {"id": new_control_id(),
                   "name": (name or f"Category {len(cats) + 1}")[:60],
                   "locked": False}
            cats.append(cat)
            if self.avatar_id:
                self.store.save_board(self.board)
        self._emit_board()
        return cat

    def rename_category(self, cat_id: str, name: str) -> bool:
        return self._edit_category(cat_id, lambda c: c.update(name=str(name)[:60] or c["name"]))

    def set_category_locked(self, cat_id: str, locked: bool) -> bool:
        return self._edit_category(cat_id, lambda c: c.update(locked=bool(locked)))

    def _edit_category(self, cat_id: str, mutate) -> bool:
        with self._lock:
            cat = next((c for c in self.board["categories"] if c["id"] == cat_id), None)
            if not cat:
                return False
            mutate(cat)
            if self.avatar_id:
                self.store.save_board(self.board)
        self._emit_board()
        return True

    def remove_category(self, cat_id: str) -> bool:
        """Delete a category; its controls move to the first remaining one."""
        with self._lock:
            cats = self.board["categories"]
            if len(cats) <= 1:
                return False  # always keep at least one
            idx = next((i for i, c in enumerate(cats) if c["id"] == cat_id), None)
            if idx is None:
                return False
            cats.pop(idx)
            fallback = cats[0]["id"]
            for ctrl in self.board["controls"]:
                if ctrl.get("cat") == cat_id:
                    ctrl["cat"] = fallback
            if self.avatar_id:
                self.store.save_board(self.board)
        self._emit_board()
        return True

    def rename_board(self, name: str) -> None:
        with self._lock:
            self.board["name"] = str(name)[:80] or short_avatar_name(self.avatar_id or "?")
            if self.avatar_id:
                self.store.save_board(self.board)
        self._emit_board()

    def _emit_board(self) -> None:
        self.emit({"t": "board", "board": self.board, "values": self.board_values()})

    # -- sharing -------------------------------------------------------------------

    def set_sharing(self, enabled: bool) -> None:
        with self._lock:
            if self.sharing_enabled == bool(enabled):
                return
            self.sharing_enabled = bool(enabled)
        self.emit({"t": "sharing", "enabled": self.sharing_enabled})

    def set_yolo(self, enabled: bool) -> None:
        with self._lock:
            if self.yolo_enabled == bool(enabled):
                return
            self.yolo_enabled = bool(enabled)
        self.store.set("yolo_enabled", self.yolo_enabled)
        log.info("YOLO mode %s", "ENABLED" if enabled else "disabled")
        self.emit({"t": "yolo", "enabled": self.yolo_enabled})

    def set_param_guest(self, name: str, value: Any) -> bool:
        """YOLO mode: guests may set any known parameter, clamped to VRChat's
        OSC ranges (Float -1..1, Int 0..255). Bypasses category locks by design."""
        if not self.yolo_enabled:
            return False
        with self._lock:
            info = self.params.get(name)
        if not info:
            return False
        ptype = info["ptype"]
        try:
            if ptype == "Bool":
                value = bool(value)
            elif ptype == "Int":
                value = max(0, min(255, int(round(float(value)))))
            elif ptype == "Float":
                value = max(-1.0, min(1.0, float(value)))
            else:
                return False
        except (TypeError, ValueError):
            return False
        ok = self.link.send_param(name, ptype, value)
        if ok:
            self._on_osc_param(name, ptype, value)
        return ok

    def guest_token(self) -> str:
        return self.store.settings["guest_token"]

    def regenerate_token(self) -> str:
        token = self.store.regenerate_guest_token()
        self.emit({"t": "token", "token": token})
        return token
