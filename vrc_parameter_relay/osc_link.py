"""OSC + OSCQuery link to VRChat.

Receiving (the OscGoesBrrr approach — no fighting over port 9001):
  * bind a random free UDP port for OSC input
  * run a tiny OSCQuery HTTP server whose tree exposes /avatar
  * advertise both services via mDNS (_oscjson._tcp + _osc._udp)
  -> VRChat discovers us and streams /avatar/change + /avatar/parameters/*
     to our port, alongside any other OSC apps the user runs.

Sending:
  * discover VRChat's own "VRChat-Client-*" OSCQuery service, read its
    HOST_INFO for the real OSC input port (default 9000 as fallback)
  * fetch its parameter tree over HTTP for a complete parameter list with
    types and current values (instead of waiting for values to change).
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
import urllib.request
from typing import Any, Callable, Optional

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient
from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf

from . import APP_NAME, oscquery
from .oscquery import OSCJSON_TYPE, OSCUDP_TYPE

log = logging.getLogger(__name__)

PARAM_PREFIX = "/avatar/parameters/"

# After /avatar/change arrives via OSC, VRChat's OSCQuery HTTP tree can lag
# behind and still describe the previous avatar. Within this window the OSC
# message is authoritative and a mismatching tree is treated as stale.
STALE_TREE_WINDOW = 30.0
STALE_RETRY_DELAY = 2.0
# Fallback poll: if /avatar/change is ever missed, the periodic tree fetch
# still picks up the new avatar.
POLL_INTERVAL = 10.0


def _local_ip() -> Optional[str]:
    """Best-effort LAN IP (some mDNS stacks dislike loopback-only records)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


class VrcLink:
    """Bidirectional OSC link to VRChat with OSCQuery discovery/advertising.

    Callbacks (invoked from worker threads):
      on_param(name, ptype, value)   ptype in {'Bool','Int','Float'}
      on_avatar(avatar_id)
      on_full_sync(avatar_id|None, params: list[(name, ptype, value)])
      on_status(dict)
    """

    def __init__(self, store, service_name: str = APP_NAME) -> None:
        self.store = store
        self.service_name = service_name

        self.on_param: Callable = lambda *a: None
        self.on_avatar: Callable = lambda *a: None
        self.on_full_sync: Callable = lambda *a: None
        self.on_status: Callable = lambda *a: None

        self.osc_port: int = 0
        self.http_port: int = 0
        self.advertised = False
        self.vrchat_http: Optional[str] = None  # http://ip:port of VRChat's OSCQuery
        self.last_received: float = 0.0

        self._send_lock = threading.Lock()
        self._client: Optional[SimpleUDPClient] = None
        self._send_target = (store.settings["osc_send_host"], store.settings["osc_send_port"])

        self._zc: Optional[Zeroconf] = None
        self._infos: list[ServiceInfo] = []
        self._osc_server: Optional[ThreadingOSCUDPServer] = None
        self._http_server = None
        self._browser: Optional[ServiceBrowser] = None
        self._refetch_timer: Optional[threading.Timer] = None
        self._poll_timer: Optional[threading.Timer] = None
        self._stopped = False
        self._osc_avatar: Optional[str] = None  # last avatar announced via OSC
        self._osc_avatar_at = 0.0
        self._sync_fails = 0  # consecutive failed HOST_INFO fetches
        self._last_vrchat_http: Optional[str] = None  # for reconnect probing

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._start_osc_server()
        self._start_http_server()
        self._make_client()
        threading.Thread(target=self._start_mdns, name="mdns", daemon=True).start()
        self._schedule_poll()

    def stop(self) -> None:
        self._stopped = True
        for timer in (self._refetch_timer, self._poll_timer):
            if timer:
                timer.cancel()
        for srv in (self._osc_server, self._http_server):
            try:
                if srv:
                    srv.shutdown()
            except Exception:
                pass
        if self._zc:
            try:
                for info in self._infos:
                    self._zc.unregister_service(info)
                self._zc.close()
            except Exception:
                pass

    # -- OSC receive ---------------------------------------------------------

    def _start_osc_server(self) -> None:
        dispatcher = Dispatcher()
        dispatcher.map("/avatar/change", self._handle_avatar_change)
        dispatcher.set_default_handler(self._handle_message)
        self._osc_server = ThreadingOSCUDPServer(("127.0.0.1", 0), dispatcher)
        self.osc_port = self._osc_server.socket.getsockname()[1]
        threading.Thread(target=self._osc_server.serve_forever, name="osc-server", daemon=True).start()
        log.info("OSC input listening on 127.0.0.1:%d", self.osc_port)

    def _handle_message(self, address: str, *args: Any) -> None:
        if not address.startswith(PARAM_PREFIX) or not args:
            return
        self.last_received = time.time()
        name = address[len(PARAM_PREFIX):]
        ptype, value = _coerce(args[0])
        if ptype:
            self.on_param(name, ptype, value)

    def _handle_avatar_change(self, address: str, *args: Any) -> None:
        self.last_received = time.time()
        if args and isinstance(args[0], str):
            self._osc_avatar = args[0]
            self._osc_avatar_at = time.time()
            self.on_avatar(args[0])
            # VRChat rebuilds its OSCQuery tree shortly after an avatar switch
            self._schedule_refetch(1.5)

    # -- OSC send ------------------------------------------------------------

    def _make_client(self) -> None:
        host, port = self._send_target
        with self._send_lock:
            self._client = SimpleUDPClient(host, port)
        log.info("OSC output -> %s:%d", host, port)

    def send_param(self, name: str, ptype: str, value: Any) -> bool:
        if ptype == "Bool":
            value = bool(value)
        elif ptype == "Int":
            value = int(round(float(value)))
        elif ptype == "Float":
            value = float(value)
        else:
            return False
        with self._send_lock:
            client = self._client
        if not client:
            return False
        try:
            client.send_message(PARAM_PREFIX + name, value)
            return True
        except OSError:
            return False

    # -- OSCQuery HTTP server (what VRChat queries about us) ------------------

    def host_info(self) -> dict:
        return oscquery.build_host_info(self.service_name, self.osc_port)

    def tree_node(self, path: str) -> Optional[dict]:
        return oscquery.node_at(oscquery.build_advertise_tree(self.service_name), path)

    def _start_http_server(self) -> None:
        self._http_server = oscquery.make_http_server(self)
        self.http_port = self._http_server.server_address[1]
        threading.Thread(target=self._http_server.serve_forever, name="oscquery-http", daemon=True).start()
        log.info("OSCQuery HTTP on 127.0.0.1:%d", self.http_port)

    # -- mDNS ------------------------------------------------------------------

    def _start_mdns(self) -> None:
        try:
            self._zc = Zeroconf()
            addresses = [socket.inet_aton("127.0.0.1")]
            lan_ip = _local_ip()
            if lan_ip:
                addresses.append(socket.inet_aton(lan_ip))
            hostname = socket.gethostname().split(".")[0]
            for svc_type, port in ((OSCJSON_TYPE, self.http_port), (OSCUDP_TYPE, self.osc_port)):
                info = ServiceInfo(
                    svc_type,
                    f"{self.service_name}.{svc_type}",
                    addresses=addresses,
                    port=port,
                    properties={"txtvers": "1"},
                    server=f"{hostname}-vrcrelay.local.",
                )
                self._zc.register_service(info, allow_name_change=True)
                self._infos.append(info)
            self.advertised = True
            log.info("mDNS advertised as %s", self.service_name)
            self._browser = ServiceBrowser(self._zc, OSCJSON_TYPE, _VrcListener(self))
        except Exception as exc:  # mDNS blocked by firewall etc.
            log.warning("mDNS setup failed: %s", exc)
        self._emit_status()

    def _emit_status(self) -> None:
        self.on_status(self.status())

    def status(self) -> dict:
        host, port = self._send_target
        return {
            "advertised": self.advertised,
            "osc_port": self.osc_port,
            "vrchat_found": bool(self.vrchat_http),
            "send_target": f"{host}:{port}",
            "receiving": (time.time() - self.last_received) < 5.0,
        }

    # -- VRChat discovery + tree fetch -------------------------------------------

    def _vrchat_service_found(self, url: Optional[str]) -> None:
        self.vrchat_http = url
        self._last_vrchat_http = url  # None on a real mDNS goodbye
        self._sync_fails = 0
        if url:
            threading.Thread(target=self._sync_from_vrchat, name="oscq-sync", daemon=True).start()
        self._emit_status()

    def _schedule_refetch(self, delay: float) -> None:
        if self._stopped:
            return
        if self._refetch_timer:
            self._refetch_timer.cancel()
        self._refetch_timer = threading.Timer(delay, self._sync_from_vrchat)
        self._refetch_timer.daemon = True
        self._refetch_timer.start()

    def refetch(self) -> None:
        self._schedule_refetch(0.05)

    def _schedule_poll(self) -> None:
        if self._stopped:
            return
        self._poll_timer = threading.Timer(POLL_INTERVAL, self._poll_tick)
        self._poll_timer.daemon = True
        self._poll_timer.start()

    def _poll_tick(self) -> None:
        try:
            if self.vrchat_http:
                self._sync_from_vrchat()
            elif self._last_vrchat_http:
                # VRChat marked gone after failed fetches, but mDNS never said
                # goodbye — probe the last-known URL in case it's back (or the
                # disconnect was a false alarm from a long loading screen).
                if self._http_json(self._last_vrchat_http + "/?HOST_INFO"):
                    log.info("VRChat's OSCQuery service is responding again")
                    self._vrchat_service_found(self._last_vrchat_http)
                else:
                    self._emit_status()
            else:
                self._emit_status()  # keep the UI's 'receiving' staleness fresh
        finally:
            self._schedule_poll()

    def _http_json(self, url: str) -> Optional[dict]:
        try:
            with urllib.request.urlopen(url, timeout=4) as res:
                return json.loads(res.read().decode("utf-8"))
        except Exception as exc:
            log.debug("OSCQuery fetch failed (%s): %s", url, exc)
            return None

    def _sync_from_vrchat(self) -> None:
        base = self.vrchat_http
        if not base:
            return
        host_info = self._http_json(base + "/?HOST_INFO")
        if host_info is None:
            # VRChat quit without an mDNS goodbye (its record TTL can keep the
            # service "alive" in the cache for a long time) — after two
            # consecutive failures, treat it as disconnected. The poll keeps
            # probing the last-known URL in case it comes back.
            self._sync_fails += 1
            if self._sync_fails >= 2 and self.vrchat_http:
                log.info("VRChat's OSCQuery service stopped responding — disconnected")
                self.vrchat_http = None
            self._emit_status()
            return
        self._sync_fails = 0
        if host_info.get("OSC_PORT"):
            target = (host_info.get("OSC_IP") or "127.0.0.1", int(host_info["OSC_PORT"]))
            if target != self._send_target:
                self._send_target = target
                self._make_client()
        tree = self._http_json(base + "/")
        if not tree:
            self._emit_status()
            return
        avatar_id, params = oscquery.parse_vrc_tree(tree)
        if (avatar_id and self._osc_avatar and avatar_id != self._osc_avatar
                and time.time() - self._osc_avatar_at < STALE_TREE_WINDOW):
            # OSC just told us the avatar changed but VRChat's HTTP tree still
            # describes the old one — retry until they agree instead of
            # flipping the board back to the stale avatar.
            log.debug("stale OSCQuery tree (%s, OSC says %s) — retrying",
                      avatar_id, self._osc_avatar)
            self._schedule_refetch(STALE_RETRY_DELAY)
            return
        self.on_full_sync(avatar_id, params)
        self._emit_status()


class _VrcListener:
    """Watches mDNS for VRChat's own OSCQuery service."""

    def __init__(self, link: VrcLink) -> None:
        self.link = link

    def add_service(self, zc: Zeroconf, svc_type: str, name: str) -> None:
        if not name.startswith("VRChat-Client"):
            return
        info = zc.get_service_info(svc_type, name, timeout=3000)
        if not info or not info.port:
            return
        addr = "127.0.0.1"  # VRChat-Windows only serves OSCQuery locally anyway
        self.link._vrchat_service_found(f"http://{addr}:{info.port}")

    def update_service(self, zc: Zeroconf, svc_type: str, name: str) -> None:
        self.add_service(zc, svc_type, name)

    def remove_service(self, zc: Zeroconf, svc_type: str, name: str) -> None:
        if name.startswith("VRChat-Client"):
            self.link._vrchat_service_found(None)


def _coerce(value: Any) -> tuple[Optional[str], Any]:
    if isinstance(value, bool):
        return "Bool", value
    if isinstance(value, int):
        return "Int", value
    if isinstance(value, float):
        return "Float", round(value, 4)
    return None, None
