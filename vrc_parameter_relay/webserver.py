"""Guest-facing web server (aiohttp, runs in its own thread + event loop).

Guests connect through the Cloudflare tunnel with a token in the URL.
They only ever see the current board's controls and the values of the
parameters on it — never the full parameter list, and the server rejects
writes to anything that isn't a board control.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional, Set

from aiohttp import WSMsgType, web

from . import resource_path

log = logging.getLogger(__name__)

MAX_GUESTS = 32
GUEST_MSGS_PER_SEC = 40  # sliders send bursts while dragging


def web_root() -> Path:
    return resource_path("web")


class GuestServer:
    def __init__(self, core, port: int) -> None:
        self.core = core
        self.requested_port = port
        self.port: Optional[int] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._sockets: Set[web.WebSocketResponse] = set()
        self._names: dict[web.WebSocketResponse, str] = {}
        self._thread: Optional[threading.Thread] = None
        self.on_guests = lambda n: None
        core.add_listener(self._core_event)

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="webserver", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/ws", self._ws)
        app.router.add_get("/health", lambda r: web.json_response({"ok": True}))
        app.router.add_get("/favicon.ico",
                           lambda r: web.FileResponse(web_root() / "favicon.png"))
        app.router.add_static("/static/", web_root(), show_index=False)
        runner = web.AppRunner(app, access_log=None)
        self.loop.run_until_complete(runner.setup())
        try:
            site = web.TCPSite(runner, "127.0.0.1", self.requested_port)
            self.loop.run_until_complete(site.start())
        except OSError:
            site = web.TCPSite(runner, "127.0.0.1", 0)  # port taken -> use a random one
            self.loop.run_until_complete(site.start())
        self.port = site._server.sockets[0].getsockname()[1]
        log.info("guest web server on http://127.0.0.1:%d", self.port)
        self.loop.run_forever()

    # -- http -------------------------------------------------------------

    async def _index(self, request: web.Request) -> web.Response:
        return web.FileResponse(web_root() / "index.html")

    def _public_board(self, board: dict) -> dict:
        """What guests see: the avatar's name plus the active preset's board."""
        return {
            "name": self.core.avatar_name,
            "categories": board.get("categories", []),
            "controls": board.get("controls", []),
        }

    def _hello_payload(self) -> dict:
        payload = {
            "t": "hello",
            "board": self._public_board(self.core.board),
            "avatar": self.core.avatar_id,
            "values": self.core.board_values(),
            "yolo": self.core.yolo_enabled,
            "paused": not self.core.sharing_enabled,
        }
        if self.core.yolo_enabled:
            payload["params"] = self.core.param_snapshot()
        return payload

    # -- websocket ----------------------------------------------------------

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=25)
        await ws.prepare(request)

        token = request.query.get("k", "")
        if not _tokens_match(token, self.core.guest_token()) or len(self._sockets) >= MAX_GUESTS:
            await ws.send_json({"t": "denied"})
            await ws.close()
            return ws

        # Guests stay connected even while paused — they just see a "paused"
        # overlay and their inputs are ignored server-side until resume.
        self._sockets.add(ws)
        self._names[ws] = _clean_name(request.query.get("n", ""))
        self._notify_guests()
        await ws.send_json(self._hello_payload())

        allowance, last = GUEST_MSGS_PER_SEC, time.monotonic()
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                now = time.monotonic()
                allowance = min(GUEST_MSGS_PER_SEC, allowance + (now - last) * GUEST_MSGS_PER_SEC)
                last = now
                if allowance < 1:
                    continue
                allowance -= 1
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                if data.get("t") == "name":  # guest set/changed their display name
                    self._names[ws] = _clean_name(data.get("name", ""))
                    self._notify_guests()
                    continue
                if not self.core.sharing_enabled:
                    continue  # paused — ignore guest input, keep the socket open
                if data.get("t") == "set":
                    self.core.set_control_value(str(data.get("id")), data.get("value"), source="guest")
                elif data.get("t") == "setp":  # YOLO mode: arbitrary parameter
                    self.core.set_param_guest(str(data.get("name")), data.get("value"))
        finally:
            self._sockets.discard(ws)
            self._names.pop(ws, None)
            self._notify_guests()
        return ws

    def _notify_guests(self) -> None:
        names = [n for n in self._names.values() if n]
        self.on_guests(len(self._sockets))
        self.core.emit({"t": "guests", "count": len(self._sockets), "names": names})

    # -- pushing core events to guests ------------------------------------------

    def _core_event(self, event: dict) -> None:
        """Called from arbitrary threads; hop onto the aiohttp loop."""
        if not self.loop or event["t"] in ("vrc_status", "tunnel", "guests"):
            return
        if event["t"] == "token":  # link revoked -> drop everyone
            self.loop.call_soon_threadsafe(self._schedule, {"t": "denied"}, True)
            return
        if event["t"] == "sharing":
            # pause = overlay + input block (socket stays open); resume = clear it
            payload = {**self._hello_payload(), "t": "resumed"} if event["enabled"] \
                else {"t": "paused"}
            self.loop.call_soon_threadsafe(self._schedule, payload, False)
            return
        out = None
        if event["t"] == "param":
            names = {c["param"] for c in self.core.board["controls"]}
            if self.core.yolo_enabled or event["name"] in names:
                out = {"t": "param", "name": event["name"],
                       "value": event["value"], "ptype": event["ptype"]}
        elif event["t"] in ("avatar", "board"):
            out = {
                "t": event["t"],
                "board": self._public_board(event["board"]),
                "values": event["values"],
            }
            if self.core.yolo_enabled:
                out["params"] = self.core.param_snapshot()
        elif event["t"] == "params_reset" and self.core.yolo_enabled:
            out = {"t": "params", "params": event["params"]}
        elif event["t"] == "yolo":
            out = {"t": "yolo", "enabled": event["enabled"]}
            if event["enabled"]:
                out["params"] = self.core.param_snapshot()
        if out:
            self.loop.call_soon_threadsafe(self._schedule, out, False)

    def _schedule(self, payload: dict, close: bool) -> None:
        asyncio.ensure_future(self._broadcast(payload, close))

    async def _broadcast(self, payload: dict, close: bool) -> None:
        for ws in list(self._sockets):
            try:
                await ws.send_json(payload)
                if close:
                    await ws.close()
            except Exception:
                self._sockets.discard(ws)


def _clean_name(name: str) -> str:
    return " ".join(str(name).split())[:24]


def _tokens_match(a: str, b: str) -> bool:
    import hmac
    return bool(a) and bool(b) and hmac.compare_digest(str(a), str(b))
