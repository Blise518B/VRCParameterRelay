"""Guest-facing web server (aiohttp, runs in its own thread + event loop).

Guests arrive through a tunnel or the user's own reverse proxy. A one-click
link carries the token; the bare public address shows a password door and
plants a cookie instead, so no secret has to live in a URL.
They only ever see the current board's controls and the values of the
parameters on it — never the full parameter list, and the server rejects
writes to anything that isn't a board control.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional, Set

from aiohttp import WSMsgType, web

from . import resource_path
from .store import CATEGORY_COLORS

log = logging.getLogger(__name__)

MAX_GUESTS = 32
GUEST_MSGS_PER_SEC = 40  # sliders send bursts while dragging

# Facing the internet through a reverse proxy (docs/REMOTE-ACCESS.md), the
# token — or the spoken password — is the only thing between a stranger and
# the board, so guessing has to cost something. A 128-bit token is not
# brute-forceable anyway; this is about the password, scanners, and
# leaked-and-revoked links.
AUTH_FAILS = 8           # bad tokens/passwords from one address before it is blocked
AUTH_WINDOW_S = 300      # ...counted within this window
AUTH_BLOCK_S = 900       # ...and then ignored for this long
AUTH_TRACK_MAX = 512     # addresses remembered: a scan can't grow memory
# ...and wrong passwords are capped across EVERYONE. The per-address block
# trusts the address a tunnel or proxy reports, so anyone who can show up
# under a fresh address per guess (a router that rewrites forwarded
# connections to its own LAN address, or just a botnet) never hits it. This
# ceiling makes guessing the password take years whatever the setup. Hitting
# it only pauses NEW sign-ins for a moment: signed-in guests carry a cookie
# and never send the password again.
PASSWORD_FAILS_GLOBAL = 30       # wrong passwords, from anyone...
PASSWORD_WINDOW_GLOBAL_S = 60    # ...within this many seconds
MAX_BODY = 64 * 1024     # no route reads a body at all; this is a backstop
MAX_WS_MSG = 16 * 1024   # the biggest real guest message is a few hundred bytes

# A token in the URL leaks: into the proxy's access log, the guest's history,
# every screenshare of that tab, and every chat the link is forwarded to. So
# the public address carries no secret at all — the guest types the spoken
# password once and the browser keeps a cookie.
#
# The password arrives in a HEADER on a GET of "/", not a form POST and not a
# query string. That keeps it out of logs and history, and it means the
# reverse proxy in front can stay GET-only with no route beyond "/" — a
# cross-site page cannot set a custom header without a CORS preflight, so it
# cannot make a browser submit it either.
PASSWORD_HEADER = "X-Share-Password"
COOKIE = "vrcpr_share"
COOKIE_MAX_AGE = 30 * 24 * 3600   # a month; rotating the token ends it early


def web_root() -> Path:
    return resource_path("web")


def font_root() -> Path:
    """Bundled .ttf files, served so guests get the host's font too."""
    return resource_path("assets/fonts")


def lan_ip() -> str:
    """This machine's address on the local network ("" if offline).

    Nothing is sent — connecting a UDP socket just picks the route the OS
    would use, which is how we learn the outgoing interface's address.
    """
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed anywhere
        ip = sock.getsockname()[0]
        return "" if ip.startswith("127.") else ip
    except OSError:
        return ""
    finally:
        sock.close()


def _free_port() -> int:
    """A concrete free TCP port (so dual-stack binds can share one)."""
    import socket
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def normalize_public_url(raw: str) -> str:
    """The user's own address as scheme://host[:port] ("" if unusable).

    No scheme means https: an own domain almost always sits behind something
    that terminates TLS (a Cloudflare Tunnel, a reverse proxy), so a plain
    port forward has to say http:// on purpose. Paths are dropped — the guest
    page lives at the root of its host — and so is a default port.
    """
    from urllib.parse import urlsplit
    text = (raw or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        parts.port  # raises on a non-numeric or out-of-range port
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    netloc = parts.netloc.rsplit("@", 1)[-1].lower()   # never keep credentials
    default = ":443" if parts.scheme == "https" else ":80"
    if netloc.endswith(default):
        netloc = netloc[:-len(default)]
    return f"{parts.scheme}://{netloc}"


def compose_public_url(address: str, token: str) -> str:
    """One-click link on the user's own address ("" when none is set)."""
    base = normalize_public_url(address)
    return f"{base}/?k={token}" if base else ""


class GuestServer:
    def __init__(self, core, port: int) -> None:
        self.core = core
        self.requested_port = port
        self.port: Optional[int] = None
        self.bind_host = "127.0.0.1"
        self._runner = None
        self._site = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._sockets: Set[web.WebSocketResponse] = set()
        self._admins: Set[web.WebSocketResponse] = set()
        self._names: dict[web.WebSocketResponse, str] = {}
        self._ids: dict[web.WebSocketResponse, int] = {}
        self._guest_seq = 0  # per-session counter behind "Anonymous #N"
        self._fails: dict = {}    # client ip -> [monotonic times of bad tokens]
        self._blocks: dict = {}   # client ip -> blocked until (monotonic)
        # monotonic times of the latest wrong passwords, anyone's
        self._pw_fails: deque = deque(maxlen=PASSWORD_FAILS_GLOBAL)
        self._admin_status_seen = None  # last (vrchat_found, receiving) sent to panels
        self._param_pending: dict = {}  # name -> (value, ptype), flushed in batches
        self._param_flush_handle = None
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
        app = web.Application(client_max_size=MAX_BODY)
        app.router.add_get("/", self._index)
        app.router.add_get("/ws", self._ws)
        app.router.add_get("/admin", self._admin_page)
        app.router.add_get("/health", lambda r: web.json_response({"ok": True}))
        app.router.add_get("/favicon.ico",
                           lambda r: web.FileResponse(web_root() / "favicon.png"))
        app.router.add_static("/static/", web_root(), show_index=False)
        fonts = font_root()
        if fonts.is_dir():
            app.router.add_static("/fonts/", fonts, show_index=False)
        runner = web.AppRunner(app, access_log=None)
        self._runner = runner
        self.loop.run_until_complete(runner.setup())
        self.loop.run_until_complete(self._start_site())
        self.loop.run_forever()

    async def _start_site(self) -> None:
        """Bind the listener on the configured host/port and record both.

        Never binds port 0: with a dual-stack bind asyncio makes one socket
        per address family, and port 0 would hand each a *different* random
        port. A concrete port is resolved first so both families share it.
        """
        host = self._bind_host()
        port = self.requested_port or _free_port()
        try:
            site = web.TCPSite(self._runner, host, port)
            await site.start()
        except OSError:
            port = _free_port()  # taken -> pick another concrete one
            site = web.TCPSite(self._runner, host, port)
            await site.start()
        self._site = site
        self.bind_host = host or "*"
        self.port = port
        log.info("guest web server on http://%s:%d", self.bind_host, self.port)

    def _bind_host(self) -> Optional[str]:
        """Loopback-only, unless the user shares on their own address.

        Then whatever routes that address here — a router port forward, a
        reverse proxy on another box, a Cloudflare Tunnel — has to reach the
        listener, so it opens on every interface (IPv4 + IPv6).
        """
        if self.core.store.settings.get("public_url"):
            return None
        return "127.0.0.1"

    def rebind(self, port: Optional[int] = None) -> None:
        """Re-create the listener with the current settings (thread-safe).

        Existing guest connections stay open; only the listening socket moves.
        """
        if not self.loop:
            return
        if port:
            self.requested_port = port
        self.loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._rebind()))

    async def _rebind(self) -> None:
        if self._runner is None:
            return
        if self._site is not None:
            await self._site.stop()
            self._site = None
        await self._start_site()

    # -- auth (guests) -----------------------------------------------------

    def _trusted_proxies(self) -> set:
        raw = str(self.core.store.settings.get("trusted_proxy") or "")
        return {part.strip() for part in raw.split(",") if part.strip()}

    def _client_ip(self, request: web.Request) -> str:
        """The guest's real address.

        Behind a tunnel or reverse proxy every connection arrives FROM the
        proxy, so X-Forwarded-For is the only way to tell guests apart — and
        it is a header anyone can type. It is believed only when the
        connection itself came from this machine, the LAN (where a tunnel or
        reverse proxy runs) or a configured `trusted_proxy`; a guest reaching
        a forwarded port straight from the internet can't shift the blame.
        The LAST entry is the one our own proxy added — anything before it
        arrived from the client and could be made up.
        """
        peer = request.remote or ""
        if peer and (peer in self._trusted_proxies() or _is_local_peer(peer)):
            hops = [hop.strip() for hop in
                    request.headers.get("X-Forwarded-For", "").split(",")]
            hops = [hop for hop in hops if hop]
            if hops:
                return hops[-1]
        return peer

    def _blocked(self, ip: str) -> bool:
        until = self._blocks.get(ip)
        if until is None:
            return False
        if until > time.monotonic():
            return True
        self._blocks.pop(ip, None)
        return False

    def _note_auth_fail(self, ip: str, path: str) -> None:
        now = time.monotonic()
        fails = [t for t in self._fails.get(ip, []) if now - t < AUTH_WINDOW_S]
        fails.append(now)
        self._fails[ip] = fails
        while len(self._fails) > AUTH_TRACK_MAX:
            self._fails.pop(next(iter(self._fails)))
        if len(fails) >= AUTH_FAILS:
            self._blocks[ip] = now + AUTH_BLOCK_S
            self._fails.pop(ip, None)
            log.warning("guest: blocking %s for %d s after %d bad tokens",
                        ip or "?", AUTH_BLOCK_S, AUTH_FAILS)
        elif len(fails) == 1:
            log.info("guest: token refused from %s (%s)", ip or "?", path)

    def _password_wait(self) -> float:
        """Seconds until anyone may try a password again (0 = open now)."""
        if len(self._pw_fails) < PASSWORD_FAILS_GLOBAL:
            return 0.0
        return max(0.0, self._pw_fails[0] + PASSWORD_WINDOW_GLOBAL_S - time.monotonic())

    def _note_password_fail(self) -> None:
        self._pw_fails.append(time.monotonic())
        if self._password_wait():  # refused attempts aren't counted, so this
            # fires once per pause, not once per refused guess
            log.warning("guest: %d wrong passwords within %d s — new sign-ins "
                        "paused for a moment (signed-in guests unaffected)",
                        PASSWORD_FAILS_GLOBAL, PASSWORD_WINDOW_GLOBAL_S)

    def _token_ok(self, request: web.Request) -> bool:
        return _tokens_match(str(request.query.get("k", "")), self.core.guest_token())

    def _cookie_ok(self, request: web.Request) -> bool:
        """The cookie carries the token itself, so rotating the token logs
        every browser out — one button, every device, no extra state."""
        return _tokens_match(str(request.cookies.get(COOKIE, "")), self.core.guest_token())

    def _password_ok(self, supplied: str) -> bool:
        want = self.core.share_password()
        if not want or not supplied:
            return False
        # typed by a human off a voice call: case and stray spaces forgiven
        return _tokens_match(supplied.strip().lower(), want.strip().lower())

    def _set_cookie(self, response: web.StreamResponse,
                    request: web.Request) -> None:
        # Secure only when the request really arrived over TLS — the LAN link
        # is plain http, and a Secure cookie would silently never be stored.
        proto = request.headers.get("X-Forwarded-Proto", request.scheme)
        response.set_cookie(
            COOKIE, self.core.guest_token(), max_age=COOKIE_MAX_AGE,
            httponly=True, samesite="Lax", secure=(proto == "https"), path="/")

    def _gate(self, request: web.Request) -> int:
        """0 = let it through, otherwise the HTTP status to answer with.

        Every guest route goes through here — only /health and the static
        assets need nothing.
        """
        ip = self._client_ip(request)
        if self._blocked(ip):
            return 429
        if self._token_ok(request) or self._cookie_ok(request):
            self._fails.pop(ip, None)   # success forgives earlier fumbles
            return 0
        if request.query.get("k"):
            self._note_auth_fail(ip, request.path)   # a wrong token in a URL
            return 403
        # Nothing usable was offered. Either an ordinary first visit, or a
        # cookie left stale by a token rotation — both are honest, so show
        # the door instead of counting it towards a block. The cookie guards
        # a 128-bit token; the guessable secret is the password, and THAT
        # path is counted (see _password_login).
        return 401

    # -- http -------------------------------------------------------------

    async def _index(self, request: web.Request) -> web.Response:
        supplied = request.headers.get(PASSWORD_HEADER)
        if supplied is not None:
            return self._password_login(request, supplied)
        status = self._gate(request)
        if status == 401:
            page = web.FileResponse(web_root() / "login.html", status=401)
            page.del_cookie(COOKIE, path="/")   # clear a rotated-away cookie
            return page
        if status:
            return self._denied_page(status)
        if request.query.get("k") and "text/html" in request.headers.get(
                "Accept", ""):
            # A browser arrived on a token link: plant the cookie, then bounce
            # to the bare address so the secret leaves the URL bar and the
            # history. Only for browsers — the redirect exists to clean an
            # address BAR, and a script following it would just lose the
            # token it was authenticating with.
            response = web.HTTPFound("/")
            self._set_cookie(response, request)
            return response
        response = web.FileResponse(web_root() / "index.html",
                                    headers={"Cache-Control": "no-cache"})
        self._set_cookie(response, request)   # slide the month forward
        return response

    def _password_login(self, request: web.Request,
                        supplied: str) -> web.Response:
        ip = self._client_ip(request)
        if self._blocked(ip):
            return web.Response(status=429, text="too many attempts")
        wait = self._password_wait()
        if wait:
            # Refused BEFORE checking, right password or not: answering
            # right/wrong now would let a guesser carry on at full speed.
            return web.Response(status=429, text="busy",
                                headers={"Retry-After": str(math.ceil(wait))})
        if not self._password_ok(supplied):
            self._note_auth_fail(ip, "password")
            self._note_password_fail()
            return web.Response(status=403, text="wrong password")
        response = web.Response(status=204)
        self._set_cookie(response, request)
        self._fails.pop(ip, None)
        log.info("guest: password accepted from %s", ip or "?")
        return response

    @staticmethod
    def _denied_page(status: int) -> web.Response:
        message = ("Too many wrong links from your address. Try again in "
                   "a few minutes." if status == 429 else
                   "This share link is invalid or was revoked. Ask the host "
                   "for the current one (Share button in the app).")
        return web.Response(
            status=status, content_type="text/html", text=(
                "<body style='background:#0a0c0a;color:#c9d4cc;"
                "font-family:Segoe UI,sans-serif;padding:40px'>"
                f"<h2>VRC Parameter Relay</h2><p>{message}</p></body>"))

    def _public_board(self, board: dict) -> dict:
        """What guests see: the avatar's name plus the active preset's board."""
        return {
            "name": self.core.avatar_name,
            "categories": board.get("categories", []),
            "controls": board.get("controls", []),
        }

    def _style(self) -> dict:
        """The host's chosen look, so the guest page can match it."""
        settings = self.core.store.settings
        return {"theme": settings.get("theme") or "neon",
                "font": settings.get("font") or "Segoe UI"}

    def _hello_payload(self) -> dict:
        payload = {
            "t": "hello",
            "board": self._public_board(self.core.board),
            "avatar": self.core.avatar_id,
            "values": self.core.board_values(),
            "yolo": self.core.yolo_enabled,
            "paused": not self.core.sharing_enabled,
            "style": self._style(),
        }
        if self.core.yolo_enabled:
            payload["params"] = self.core.param_snapshot()
        return payload

    # -- admin panel ---------------------------------------------------------

    def _admin_ok(self, token: str) -> bool:
        if not self.core.store.settings.get("admin_enabled", True):
            return False
        return _tokens_match(token, self.core.admin_token())

    def _admin_origin_ok(self, request: web.Request) -> bool:
        """Is this request allowed to reach the admin panel at all?

        Default: only direct local/LAN connections — a request relayed in from
        the internet through the reverse proxy or tunnel is refused, so the
        public guest link cannot even reach /admin. Opt out with 'admin_public'
        for deliberate remote administration of a headless relay.
        """
        if self.core.store.settings.get("admin_public"):
            return True
        if (request.remote or "") in self._trusted_proxies():
            return False   # came in through the reverse proxy = the public path
        return _request_is_local(request)

    async def _admin_page(self, request: web.Request) -> web.Response:
        if not self._admin_origin_ok(request):
            return web.Response(status=403, content_type="text/html", text=(
                "<body style='background:#0a0c0a;color:#c9d4cc;"
                "font-family:Segoe UI,sans-serif;padding:40px'>"
                "<h2>Admin panel is local-only</h2><p>This relay only serves the "
                "admin panel on the local machine and your LAN. Open it from the "
                "app's own network, or enable “admin over the internet” in "
                "Link settings if you really need remote access.</p></body>"))
        if not self._admin_ok(request.query.get("k", "")):
            return web.Response(status=403, content_type="text/html", text=(
                "<body style='background:#0a0c0a;color:#c9d4cc;"
                "font-family:Segoe UI,sans-serif;padding:40px'>"
                "<h2>Admin link invalid</h2><p>Open the app and copy the current "
                "admin link (Share → Admin panel).</p></body>"))
        return web.FileResponse(web_root() / "admin.html")

    def _admin_state(self) -> dict:
        """Everything the panel needs to draw itself."""
        core = self.core
        return {
            "avatar_id": core.avatar_id,
            "avatar_name": core.avatar_name,
            "is_live": core.is_live,
            "avatars": core.list_avatars(),
            "presets": core.list_presets(),
            "preset_id": core.board.get("id"),
            "board": {"categories": core.board.get("categories", []),
                      "controls": core.board.get("controls", [])},
            "values": core.board_values(),
            "params": core.param_snapshot(),
            "sharing": core.sharing_enabled,
            "yolo": core.yolo_enabled,
            "guests": len(self._sockets),
            "guest_names": [n or f"Anonymous #{self._ids.get(w, 0)}"
                            for w, n in self._names.items()],
            "colors": list(CATEGORY_COLORS),
            "style": self._style(),
        }

    async def _admin_ws(self, ws: web.WebSocketResponse) -> web.WebSocketResponse:
        self._admins.add(ws)
        await ws.send_json({"t": "admin_state", **self._admin_state()})
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data, parse_constant=_no_constants)
                except ValueError:
                    continue
                try:
                    result = self._admin_op(data)
                except Exception as exc:  # noqa: BLE001 — report, never die
                    log.exception("admin op failed: %s", data.get("op"))
                    result = f"{type(exc).__name__}: {exc}"
                if result:
                    await ws.send_json({"t": "error", "msg": str(result)})
                # Answer the sender directly with state as it is *now*. Other
                # panels get their own snapshot from the core event. "for"
                # marks this as the reply to that op — a broadcast queued by
                # an earlier op can otherwise land in between.
                # set_value is the exception: it fires at slider-drag rate and
                # its outcome already streams back as a `param` delta, so a
                # snapshot per message just made the panel re-render nonstop.
                # A FAILED set_value still gets one, to undo the panel's
                # optimistic flip.
                if data.get("op") != "set_value" or result:
                    await ws.send_json({"t": "admin_state", "for": data.get("op"),
                                        **self._admin_state()})
        finally:
            self._admins.discard(ws)
        return ws

    def _admin_op(self, data: dict) -> Optional[str]:
        """Run one panel command against the core. Returns an error or None."""
        core = self.core
        op = str(data.get("op") or "")
        arg = data.get  # shorthand

        if op == "set_value":
            if not core.set_control_value(str(arg("id")), arg("value"), source="host"):
                return "Value not sent — this avatar isn't live in VRChat right now."
        elif op == "add_control":
            ptype = (core.param_snapshot().get(str(arg("param")))
                     or {}).get("ptype", "Float")
            index = arg("index")
            core.add_control(str(arg("param")), arg("kind") or core.default_kind(ptype),
                             category=arg("cat"),
                             index=int(index) if index is not None else None)
        elif op == "update_control":
            changes = {k: v for k, v in (data.get("changes") or {}).items()
                       if k in ("label", "kind", "min", "max", "invert", "on", "off")}
            core.update_control(str(arg("id")), **changes)
        elif op == "remove_control":
            core.remove_control(str(arg("id")))
        elif op == "move_control":
            core.move_control_to_category(str(arg("id")), str(arg("cat")),
                                          int(arg("index") or 0))
        elif op == "add_category":
            core.add_category(arg("name") or None)
        elif op == "rename_category":
            core.rename_category(str(arg("cat")), str(arg("name") or ""))
        elif op == "category_color":
            core.set_category_color(str(arg("cat")), str(arg("color")))
        elif op == "category_locked":
            core.set_category_locked(str(arg("cat")), bool(arg("locked")))
        elif op == "remove_category":
            if not core.remove_category(str(arg("cat"))):
                return "The board needs at least one category."
        elif op == "move_category":
            core.move_category(str(arg("cat")), str(arg("target")))
        elif op == "copy_category":
            core.copy_category_to_preset(str(arg("cat")), str(arg("preset")))
        elif op == "add_preset":
            core.add_preset(arg("name") or None)
        elif op == "rename_preset":
            core.rename_preset(str(arg("preset")), str(arg("name") or ""))
        elif op == "remove_preset":
            if not core.remove_preset(str(arg("preset"))):
                return "The avatar needs at least one preset."
        elif op == "switch_preset":
            core.switch_preset(str(arg("preset")))
        elif op == "open_avatar":
            core.open_avatar(str(arg("avatar")))
        elif op == "rename_avatar":
            core.rename_avatar(str(arg("name") or ""))
        elif op == "set_sharing":
            core.set_sharing(bool(arg("enabled")))
        elif op == "set_yolo":
            core.set_yolo(bool(arg("enabled")))
        elif op == "resync":
            refetch = getattr(core.link, "refetch", None)
            if callable(refetch):
                refetch()
        elif op == "reset_guest_link":
            core.regenerate_token()
        else:
            return f"unknown operation {op!r}"
        return None

    # -- websocket ----------------------------------------------------------

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=25, max_msg_size=MAX_WS_MSG)
        await ws.prepare(request)

        token = request.query.get("k", "")
        if request.query.get("admin"):
            # the admin secret is separate on purpose: a guest link, however
            # widely shared, must never be able to edit the board. And unless
            # remote admin is explicitly enabled, the socket is refused for
            # anyone reaching us through the public proxy/tunnel — same gate as
            # the /admin page, so a leaked admin token is still useless online.
            if not self._admin_origin_ok(request) or not self._admin_ok(token):
                await ws.send_json({"t": "denied"})
                await ws.close()
                return ws
            return await self._admin_ws(ws)

        # token in the URL (one-click link) OR the cookie a signed-in browser
        # sends by itself; a wrong token counts toward the per-address block
        if self._gate(request) or len(self._sockets) >= MAX_GUESTS:
            await ws.send_json({"t": "denied"})
            await ws.close()
            return ws

        # Guests stay connected even while paused — they just see a "paused"
        # overlay and their inputs are ignored server-side until resume.
        self._sockets.add(ws)
        self._guest_seq += 1
        self._ids[ws] = self._guest_seq
        self._names[ws] = _clean_name(request.query.get("n", ""))
        self._notify_guests()
        payload = self._hello_payload()
        if (self.core.store.settings.get("require_guest_names")
                and not self._names[ws]):
            payload["need_name"] = True
        await ws.send_json(payload)

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
                    data = json.loads(msg.data, parse_constant=_no_constants)
                except ValueError:
                    continue
                if data.get("t") == "name":  # guest set/changed their display name
                    self._names[ws] = _clean_name(data.get("name", ""))
                    self._notify_guests()
                    continue
                if not self.core.sharing_enabled:
                    continue  # paused — ignore guest input, keep the socket open
                if (self.core.store.settings.get("require_guest_names")
                        and not self._names.get(ws)):
                    continue  # nameless while names are required — input ignored
                if data.get("t") == "set":
                    self.core.set_control_value(str(data.get("id")), data.get("value"), source="guest")
                elif data.get("t") == "setp":  # YOLO mode: arbitrary parameter
                    self.core.set_param_guest(str(data.get("name")), data.get("value"))
        finally:
            self._sockets.discard(ws)
            self._names.pop(ws, None)
            self._ids.pop(ws, None)
            self._notify_guests()
        return ws

    def _notify_guests(self) -> None:
        # one entry per guest, in connect order; unnamed guests get a stable tag
        names = [name or f"Anonymous #{self._ids.get(ws, 0)}"
                 for ws, name in self._names.items()]
        self.on_guests(len(self._sockets))
        self.core.emit({"t": "guests", "count": len(self._sockets), "names": names})

    # -- pushing core events to guests ------------------------------------------

    def _core_event(self, event: dict) -> None:
        """Called from arbitrary threads; hop onto the aiohttp loop."""
        if not self.loop:
            return
        # panels mirror the desktop UI, so they get a fresh snapshot on any
        # structural change (values stream as deltas via the guest path).
        # The noisy events are exempt: the 10s status heartbeat and tunnel
        # chatter would each push a full snapshot -> full re-render.
        if self._admins and event["t"] not in ("param", "vrc_status", "tunnel"):
            self.loop.call_soon_threadsafe(self._schedule_admin)
        elif self._admins and event["t"] == "vrc_status":
            seen = (bool(event.get("vrchat_found")), bool(event.get("receiving")))
            if seen != self._admin_status_seen:  # a real connect/disconnect
                self._admin_status_seen = seen
                self.loop.call_soon_threadsafe(self._schedule_admin)
        if event["t"] in ("vrc_status", "tunnel", "guests"):
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
        if event["t"] == "param":
            self.loop.call_soon_threadsafe(
                self._queue_param, event["name"], event["ptype"], event["value"])
            return
        out = None
        if event["t"] in ("avatar", "board"):
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
        elif event["t"] == "require_names":
            # named guests ignore the "on" case client-side
            out = {"t": "need_name", "enabled": event["enabled"]}
        elif event["t"] == "style":  # host switched theme/font — follow along
            out = {"t": "style", **self._style()}
        if out:
            self.loop.call_soon_threadsafe(self._schedule, out, False)

    def _schedule(self, payload: dict, close: bool) -> None:
        asyncio.ensure_future(self._broadcast(payload, close))

    # -- param delta coalescing (runs on the aiohttp loop) --------------------
    # A live avatar streams tracking/physics values hundreds of times per
    # second. One websocket message per delta starved the loop — panel ops sat
    # unprocessed for a second — and pinned a client CPU core, so deltas are
    # collected and flushed as one batch per 50 ms (newest value wins).

    def _queue_param(self, name: str, ptype: str, value) -> None:
        self._param_pending[name] = (value, ptype)
        if self._param_flush_handle is None:
            self._param_flush_handle = self.loop.call_later(0.05, self._flush_params)

    def _flush_params(self) -> None:
        self._param_flush_handle = None
        pending, self._param_pending = self._param_pending, {}
        if not pending:
            return
        if self._admins:
            payload = {"t": "param_batch",
                       "params": {n: [v, p] for n, (v, p) in pending.items()}}
            asyncio.ensure_future(self._broadcast_admin_payload(payload))
        if self._sockets:
            names = {c["param"] for c in self.core.board["controls"]}
            allowed = {n: vp for n, vp in pending.items()
                       if self.core.yolo_enabled or n in names}
            if allowed:
                asyncio.ensure_future(self._broadcast(
                    {"t": "param_batch",
                     "params": {n: [v, p] for n, (v, p) in allowed.items()}}, False))

    async def _broadcast_admin_payload(self, payload: dict) -> None:
        for ws in list(self._admins):
            try:
                await ws.send_json(payload)
            except Exception:
                self._admins.discard(ws)

    def _schedule_admin(self) -> None:
        asyncio.ensure_future(self._broadcast_admin())

    async def _broadcast_admin(self) -> None:
        if not self._admins:
            return
        await self._broadcast_admin_payload({"t": "admin_state", **self._admin_state()})

    async def _broadcast(self, payload: dict, close: bool) -> None:
        for ws in list(self._sockets):
            try:
                await ws.send_json(payload)
                if close:
                    await ws.close()
            except Exception:
                self._sockets.discard(ws)


def _no_constants(literal: str):
    """json.loads parse_constant hook: refuse Infinity/NaN, which Python's
    json accepts by default — an infinite slider value would go straight
    to VRChat."""
    raise ValueError(f"refusing JSON constant {literal!r}")


def _clean_name(name: str) -> str:
    return " ".join(str(name).split())[:24]


def _tokens_match(a: str, b: str) -> bool:
    import hmac
    return bool(a) and bool(b) and hmac.compare_digest(str(a), str(b))


# Headers a reverse proxy or tunnel adds. Their presence means the request did
# NOT arrive as a direct local connection — it was relayed from somewhere else,
# i.e. the public path. cloudflared and Caddy both set X-Forwarded-For.
_PROXY_HEADERS = ("X-Forwarded-For", "Forwarded", "X-Real-IP",
                  "Cf-Connecting-Ip", "X-Forwarded-Host")


def _reached_via_proxy(request: web.Request) -> bool:
    return any(request.headers.get(h) for h in _PROXY_HEADERS)


def _is_local_peer(ip: Optional[str]) -> bool:
    """True for a loopback or private-LAN source address."""
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private or addr.is_link_local


def _request_is_local(request: web.Request) -> bool:
    """A direct connection from this machine or the LAN — never the internet.

    Anything from the internet either arrives *through* a tunnel or reverse
    proxy, carrying a forwarding header, or straight through a forwarded
    port from a public address. Requiring both "no proxy header" and
    "private peer" refuses both — a proxied request even though the proxy
    itself sits on this machine or the LAN.
    """
    return not _reached_via_proxy(request) and _is_local_peer(request.remote)
