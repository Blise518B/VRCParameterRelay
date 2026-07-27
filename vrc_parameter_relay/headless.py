"""Headless mode: the full relay without a window.

Same program, same data, same settings — just no Qt. Start the exe (or
``python -m vrc_parameter_relay``) with ``--headless`` and it connects to
VRChat, loads the saved boards, starts sharing with the configured link
provider (Cloudflare / ngrok / self-hosted + UPnP) and keeps running until
killed. Board edits made by a parallel UI instance are picked up live via
the profile hot-reload poll.

This module must never import Qt — that's the point.
"""
from __future__ import annotations

import logging
import signal
import threading
import time
from typing import Optional

from . import APP_NAME, __version__
from .core import AppCore
from .osc_link import VrcLink
from .store import Store
from .tunnel import Tunnel
from .webserver import GuestServer, compose_selfhost_url, lan_ip

log = logging.getLogger(__name__)

RELOAD_POLL_S = 2.0


class HeadlessApp:
    def __init__(self, store: Optional[Store] = None) -> None:
        self.store = store or Store()
        self.link = VrcLink(self.store)
        self.core = AppCore(self.store, self.link)
        self.web = GuestServer(self.core, self.store.settings["web_port"])
        self.tunnel = Tunnel(self.store)
        self.stop_event = threading.Event()
        self.guard = None  # set by run(); tests construct without one
        self.tunnel.on_change = self._tunnel_changed
        self.core.add_listener(self._log_event)

    # -- logging hooks (the log file is the headless "UI") ---------------------

    def _tunnel_changed(self, status: dict) -> None:
        self.core.emit({"t": "tunnel", **status})
        state = status.get("state")
        if state == "online":
            log.info("SHARE LINK: %s/?k=%s", status.get("url"),
                     self.core.guest_token())
        elif state == "error":
            log.error("tunnel error: %s", status.get("error"))

    def _log_event(self, event: dict) -> None:
        kind = event.get("t")
        if kind == "guests":
            log.info("guests: %d %s", event["count"],
                     ", ".join(event.get("names") or []))
        elif kind == "upnp":
            if event.get("ok"):
                log.info("UPnP: port %s opened (public IP %s)",
                         event.get("port"), event.get("info") or "?")
            else:
                log.warning("UPnP failed: %s — forward the port manually",
                            event.get("info"))
        elif kind == "avatar":
            log.info("avatar: %s", self.core.avatar_name)

    # -- lifecycle --------------------------------------------------------------

    def _selfhost(self) -> bool:
        settings = self.store.settings
        return bool(settings.get("advanced_link")
                    and settings.get("tunnel_provider") == "selfhost")

    def start(self, *, start_link: bool = True) -> None:
        """Bring everything up and begin sharing (there's no button to press)."""
        if start_link:
            self.link.start()
        self.web.start()
        deadline = time.time() + 10
        while not self.web.port and time.time() < deadline:
            time.sleep(0.1)
        if not self.web.port:
            raise RuntimeError("guest web server failed to start")

        self.core.set_sharing(True)
        settings = self.store.settings
        if self._selfhost():
            url = compose_selfhost_url(
                settings.get("selfhost_host", ""),
                settings.get("selfhost_port") or settings.get("web_port"),
                self.core.guest_token())
            log.info("SHARE LINK (self-hosted): %s",
                     url or "NOT CONFIGURED — set a public address in the UI first")
            if settings.get("selfhost_upnp"):
                from .upnp import map_async
                map_async(settings, self.web.port, self.core.emit)
        else:
            self.tunnel.start(self.web.port)
        log.info("local test link: http://127.0.0.1:%d/?k=%s",
                 self.web.port, self.core.guest_token())
        ip = lan_ip()
        if ip:  # how a server/Linux user reaches the board from another device
            log.info("on this network:  http://%s:%d/?k=%s",
                     ip, self.web.port, self.core.guest_token())
        if self.core.yolo_enabled:
            log.warning("YOLO mode is ON (persisted setting) — guests control "
                        "every parameter")

    def run_forever(self) -> None:
        try:
            while not self.stop_event.wait(RELOAD_POLL_S):
                try:
                    self.core.poll_profile_change()
                except Exception:
                    log.exception("profile reload failed")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        log.info("shutting down…")
        self.tunnel.stop()
        self.link.stop()
        from .upnp import cleanup_mappings
        cleanup_mappings()
        if self.guard is not None:
            self.guard.release()


def run(guard=None) -> int:
    log.info("%s v%s — headless mode (no window); sharing starts automatically",
             APP_NAME, __version__)
    app = HeadlessApp()
    app.guard = guard
    if guard is not None:  # a later start just wakes us up; nothing to show
        guard.on_activate = lambda: log.info(
            "another start was attempted — this instance keeps running")
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: app.stop_event.set())
        except (ValueError, OSError):
            pass  # not available on this platform/thread
    app.start()
    app.run_forever()
    return 0
