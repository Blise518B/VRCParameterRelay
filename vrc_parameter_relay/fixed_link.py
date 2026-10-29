"""Fixed link — the share link that never changes.

The relay keeps a permanent iroh identity: a key pair in its own file
(fixed_link_key.bin, never in settings.json). Its public half, the endpoint
ID, goes into one link together with the guest token:

    https://blise518b.github.io/VRCParameterRelay/remote/#<endpoint id>.<token>

That page is static (GitHub Pages, docs/remote/ in this repo — written by
tools/build_pages.py). It is the same guest app the tunnel link serves, and
it dials this relay with iroh compiled to WebAssembly (tools/web_guest),
always through n0's free relays because a browser can't send UDP. Nothing
after the # ever reaches GitHub: the token travels end-to-end encrypted
inside the QUIC connection, to us. No tunnel, no account, nothing to
download. The link changes only when the user hits Reset link (a new
token); the endpoint ID stays.

On the wire: the browser opens one bidirectional stream and sends frames —
a 4-byte big-endian length, then UTF-8 JSON. The first frame is a join,
``{"t": "join", "k": <token>, "n": <name>, "v": 1}``; after that both
directions carry exactly the guest websocket's messages, so the session
runs through the same GuestServer code as a websocket guest (pause, locks,
names, YOLO, Reset link). A refusal is a ``denied`` frame (``why``: "link"
or "full") and the connection closes with the same word, in case the frame
is lost on the way.

Runs on the guest web server's asyncio loop. One per process: the iroh
bindings keep a single asyncio loop for every call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections import namedtuple
from typing import Callable, Optional

from aiohttp import WSMsgType

log = logging.getLogger(__name__)

IROH_ERROR = ""   # why iroh didn't load, for the Share window
try:
    import iroh
except Exception as _e:  # noqa: BLE001 — a build without the wheel: say so
    iroh = None
    IROH_ERROR = f"{type(_e).__name__}: {_e}"

ALPN = b"vrcpr/guest/1"
PROTOCOL_VERSION = 1
KEY_FILE = "fixed_link_key.bin"
# Where the guest page lives. The env override points a test build at a
# local copy of docs/ (python -m http.server) before it is on GitHub Pages.
PAGE_URL = (os.environ.get("VRCPR_FIXED_LINK_PAGE")
            or "https://blise518b.github.io/VRCParameterRelay/remote/")

JOIN_TIMEOUT_S = 10.0    # a browser has this long to open its stream and join
MAX_JOINING = 16         # handshakes in flight; more are refused outright
MAX_FRAME_IN = 16 * 1024  # webserver.MAX_WS_MSG — what a guest may send
SEND_TIMEOUT_S = 10.0    # a guest that can't take a frame this long is dropped
CLOSE_GRACE_S = 0.3      # lets a last frame leave before the connection closes

_Text = namedtuple("_Text", "type data")


def available() -> bool:
    return iroh is not None


def compose_fixed_url(endpoint_id: str, token: str, page: str = "") -> str:
    base = page or PAGE_URL
    if not base.endswith("/"):
        base += "/"
    return f"{base}#{endpoint_id}.{token}"


class GuestSocket:
    """One browser's stream, shaped like the aiohttp websocket the guest
    session code was written for: `send_json`, `close`, and `async for`
    yielding TEXT messages."""

    def __init__(self, conn, send, recv) -> None:
        self.conn = conn
        self._send = send
        self._recv = recv
        self._write_lock = asyncio.Lock()
        self._denied = False
        self.closed = False

    async def send_json(self, obj) -> None:
        if obj.get("t") == "denied":
            self._denied = True   # the close that follows says so too
        data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        frame = len(data).to_bytes(4, "big") + data
        async with self._write_lock:
            await asyncio.wait_for(self._send.write_all(frame), SEND_TIMEOUT_S)

    async def close(self, code: int = 0, reason: bytes = b"bye") -> None:
        if self.closed:
            return
        self.closed = True
        if self._denied and reason == b"bye":
            code, reason = 5, b"denied link"   # Reset link kicked everyone
        # Closing at once would throw away a frame still on its way out. No
        # finish() first: the browser would see the stream end before the
        # close, and lose the reason it carries.
        asyncio.get_running_loop().call_later(CLOSE_GRACE_S, _close_quietly,
                                              self.conn, code, reason)

    def __aiter__(self):
        return self._frames()

    async def _frames(self):
        while True:
            try:
                data = await read_frame(self._recv, MAX_FRAME_IN)
            except Exception:
                return  # the browser left, or the connection dropped
            if data is None:
                await self.close(5, b"too big")
                return
            yield _Text(WSMsgType.TEXT, data.decode("utf-8", errors="replace"))


async def read_frame(recv, limit: int) -> Optional[bytes]:
    """The next frame's body; None when it is bigger than `limit`."""
    head = await recv.read_exact(4)
    size = int.from_bytes(head, "big")
    if size > limit:
        return None
    return await recv.read_exact(size) if size else b""


def _close_quietly(conn, code: int, reason: bytes) -> None:
    try:
        conn.close(code, reason)
    except Exception:
        pass


class FixedLink:
    """States: off -> starting -> online -> off / error (like Tunnel)."""

    def __init__(self, store, web) -> None:
        self.store = store
        self.web = web
        self.key_path = store.dir / KEY_FILE
        self.state = "off"
        self.error: Optional[str] = None
        self.on_change: Callable[[dict], None] = lambda st: None
        self._key_lock = threading.Lock()
        self._id = ""
        self._future = None          # the endpoint's run on the web server's loop
        self._stop_ev: Optional[asyncio.Event] = None
        self._conns: set = set()
        self._joining = 0
        self._tasks: set = set()

    # -- public API -------------------------------------------------------------

    def status(self) -> dict:
        return {"state": self.state, "error": self.error, "url": self.url()}

    def endpoint_id(self) -> str:
        """This relay's public ID — known without going online, so the
        Share window can show the link before sharing ever started."""
        if not self._id and iroh is not None:
            try:
                self._id = str(iroh.SecretKey.from_bytes(self._load_key()).public())
            except Exception as exc:
                log.warning("fixed link: no identity yet (%s)", exc)
        return self._id

    def url(self) -> str:
        endpoint = self.endpoint_id()
        if not endpoint:
            return ""
        return compose_fixed_url(endpoint, self.store.settings["guest_token"])

    def start(self) -> None:
        if self.state in ("starting", "online"):
            return
        if iroh is None:
            self._set("error", f"the fixed link isn't available in this build ({IROH_ERROR})")
            return
        loop = getattr(self.web, "loop", None)
        if loop is None:
            self._set("error", "the web server isn't up yet — try again in a second")
            return
        self._set("starting")
        self._future = asyncio.run_coroutine_threadsafe(self._main(), loop)

    def stop(self, timeout: float = 3.0) -> None:
        future, loop, stop_ev = self._future, getattr(self.web, "loop", None), self._stop_ev
        if future is None:
            return
        if loop is not None and stop_ev is not None:
            try:
                loop.call_soon_threadsafe(stop_ev.set)
            except RuntimeError:
                pass
        try:
            future.result(timeout)
        except Exception:
            pass
        self._future = None
        if self.state != "error":
            self._set("off")

    # -- internals ----------------------------------------------------------------

    def _set(self, state: str, error: Optional[str] = None) -> None:
        self.state, self.error = state, error
        self.on_change(self.status())

    def _load_key(self) -> bytes:
        """The link's secret. Only a missing file makes a new one: a file
        that can't be read right now (antivirus, a sync tool) is an error
        instead — a new key would be a new link. A damaged one is kept
        aside as .bad and replaced, loudly."""
        with self._key_lock:
            path = str(self.key_path)
            try:
                with open(path, "rb") as fh:
                    key = fh.read()
            except FileNotFoundError:
                key = b""
            except OSError as exc:
                raise OSError(f"couldn't read the fixed link's key ({exc})") from exc
            if len(key) == 32:
                return key
            if key:
                os.replace(path, path + ".bad")
                log.warning("fixed link: the key file was damaged (%d bytes) — kept it "
                            "as %s.bad and made a new link", len(key), KEY_FILE)
            key = iroh.SecretKey.generate().to_bytes()
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(key)
            os.replace(tmp, path)
            self._id = ""
            return key

    async def _main(self) -> None:
        loop = asyncio.get_running_loop()
        iroh.iroh_ffi.uniffi_set_event_loop(loop)
        self._stop_ev = asyncio.Event()
        try:
            ep = await iroh.Endpoint.bind(iroh.EndpointOptions(
                preset=iroh.preset_n0(), secret_key=self._load_key(), alpns=[ALPN]))
        except Exception as exc:
            log.exception("fixed link: couldn't start")
            self._set("error", str(exc))
            return
        self._id = str(ep.id())
        accept = loop.create_task(self._accept_loop(ep))
        stopper = loop.create_task(self._stop_ev.wait())
        # online() only returns once a home relay answers — never, without
        # internet — so a stop must not wait for it
        coming = loop.create_task(ep.online())
        await asyncio.wait({coming, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if coming.done() and coming.exception() is None:
            self._set("online")
            log.info("fixed link online: %s", self.url().split("#", 1)[0])
        elif coming.done():
            self._set("error", f"couldn't reach iroh's relays ({coming.exception()})")
        await stopper
        coming.cancel()
        accept.cancel()
        for conn in list(self._conns):
            _close_quietly(conn, 0, b"bye")
        await asyncio.sleep(0.05)
        try:
            await asyncio.wait_for(ep.close(), 1.0)
        except Exception:
            pass

    async def _accept_loop(self, ep) -> None:
        while True:
            inc = await ep.accept_next()
            if inc is None:
                return
            if self._joining >= MAX_JOINING:
                self._spawn(inc.refuse())
                continue
            self._spawn(self._incoming(inc))

    def _spawn(self, coro) -> None:
        """A task of its own, kept until it ends (the loop holds tasks only
        weakly); a crash is logged instead of vanishing."""
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.warning("fixed link: a guest task failed (%s)", task.exception())

    async def _incoming(self, inc) -> None:
        self._joining += 1
        conn = None
        try:
            conn = await asyncio.wait_for((await inc.accept()).connect(), JOIN_TIMEOUT_S)
            bi = await asyncio.wait_for(conn.accept_bi(), JOIN_TIMEOUT_S)
            sock = GuestSocket(conn, bi.send(), bi.recv())
            raw = await asyncio.wait_for(read_frame(sock._recv, MAX_FRAME_IN), JOIN_TIMEOUT_S)
            join = json.loads(raw or b"null")
            if not isinstance(join, dict) or join.get("t") != "join":
                raise ValueError("no join")
        except Exception:
            if conn is not None:
                _close_quietly(conn, 5, b"no join")
            return
        finally:
            self._joining -= 1
        peer = str(conn.remote_id())
        self._conns.add(conn)
        try:
            await self.web.serve_fixed_guest(sock, join, peer)
        finally:
            self._conns.discard(conn)
            if not sock.closed:
                await sock.close(0, b"bye")
