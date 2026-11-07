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

Same PC (default) vs. other devices ("osc_remote"):
  Desktop VRChat only serves and reads OSCQuery on 127.0.0.1, so by default
  everything binds to loopback. VRChat's Android build (Quest standalone,
  phones) advertises its LAN address instead: with "osc_remote" on, the OSC
  and OSCQuery servers listen on every interface, HOST_INFO tells each
  client the address it reached us on, VRChat is contacted at the address it
  advertises, and OSC input is only accepted from loopback or an address a
  VRChat client advertised. "osc_in_port" / "oscquery_port" pin the ports
  (0 = random) so a port-based firewall can let VRChat in.
"""
from __future__ import annotations

import json
import logging
import socket
import sys
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
# in remote mode, stay with the VRChat we talk to while it answered this
# recently (two polls and a margin); after that a newcomer may take over
STICKY_FOR = 2 * POLL_INTERVAL + 5.0


def _is_loopback(ip: Optional[str]) -> bool:
    return bool(ip) and (ip.startswith("127.") or ip in ("::1", "localhost"))


# VRChat's own OSC ports: it listens on 9000 and sends to 9001 by default
# (where the classic OSC apps listen) — the relay must never sit on them
RESERVED_OSC_PORTS = (9000, 9001)
MAX_IGNORED_LOGGED = 32   # distinct unknown senders logged, then quiet


def _port_free(kind: int, port: int) -> bool:
    """Is the port free on every interface?

    The real bind can't tell: Windows lets a bind on 127.0.0.1 succeed while
    another program holds the same port on 0.0.0.0, and the two then split
    the traffic. A trial bind on the wildcard address fails in that case."""
    for address in ("0.0.0.0", "127.0.0.1"):   # held on all, or on loopback only
        probe = socket.socket(socket.AF_INET, kind)
        try:
            if kind == socket.SOCK_STREAM and sys.platform != "win32":
                # Linux: ignore our own TIME_WAIT leftovers, still fail on a listener
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((address, port))
        except OSError:
            return False
        finally:
            probe.close()
    return True


def _bind_with_fallback(make, host: str, port: int, what: str, warnings: list,
                        kind: int, reserved: tuple = ()) -> Any:
    """Bind on the configured port; if it can't be used, a random one (so
    VRChat on this PC still works) and a warning the settings and log show."""
    if port:
        problem = None
        if port in reserved:
            problem = "is VRChat's own port"
        elif not _port_free(kind, port):
            problem = "is in use by another program"
        else:
            try:
                return make(host, port)
            except OSError as exc:
                problem = f"can't be used ({exc.strerror or exc})"
        msg = f"{what} port {port} {problem} — using a random port"
        log.warning(msg)
        warnings.append(msg)
    return make(host, 0)


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
        self.remote = False             # osc_remote, read on (re)start
        self.bind_host = "127.0.0.1"
        self.port_warnings: list[str] = []
        self.advertised = False
        self.vrchat_http: Optional[str] = None  # http://ip:port of VRChat's OSCQuery
        self._vrchat_name: Optional[str] = None  # its mDNS service name
        self._vrchat_host: Optional[str] = None  # the ip in vrchat_http
        # every VRChat client seen on mDNS: name -> (addresses, OSCQuery port).
        # We talk to one at a time and stay with it while it's alive.
        self._vrchat_clients: dict[str, tuple[list[str], int]] = {}
        # the addresses of the client we talk to: in remote mode, OSC input
        # from anywhere else on the network is ignored
        self._vrchat_addrs: set[str] = set()
        self._ignored_sources: set[str] = set()  # logged once each
        self._own_addrs: set[str] = set()   # this machine's LAN address = local
        self._vrchat_ok_at = 0.0   # when the client we talk to last answered
        # each start is a new generation: timers and fetches from before a
        # restart see a stale number and stop instead of running twice
        self._gen = 0
        # restarts come from worker threads (window, admin panel): one at a time
        self._restart_lock = threading.RLock()
        self.last_received: float = 0.0

        self._send_lock = threading.Lock()
        self._client: Optional[SimpleUDPClient] = None
        self._send_target = (store.settings["osc_send_host"], store.settings["osc_send_port"])

        self._zc: Optional[Zeroconf] = None
        self._infos: list[ServiceInfo] = []
        self._osc_server: Optional[ThreadingOSCUDPServer] = None
        self._http_server = None
        self._browser: Optional[ServiceBrowser] = None
        self._mdns_thread: Optional[threading.Thread] = None
        self._refetch_timer: Optional[threading.Timer] = None
        self._poll_timer: Optional[threading.Timer] = None
        self._stopped = False
        self._osc_avatar: Optional[str] = None  # last avatar announced via OSC
        self._osc_avatar_at = 0.0
        self._sync_fails = 0  # consecutive failed HOST_INFO fetches
        self._last_vrchat_http: Optional[str] = None  # for reconnect probing

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._gen += 1
        settings = self.store.settings
        self.remote = bool(settings.get("osc_remote"))
        self._own_addrs = {a for a in (_local_ip(),) if a}
        self.bind_host = "0.0.0.0" if self.remote else "127.0.0.1"
        self.port_warnings = []
        self._start_osc_server()
        self._start_http_server()
        self._make_client()
        self._mdns_thread = threading.Thread(target=self._start_mdns, name="mdns", daemon=True)
        self._mdns_thread.start()
        self._schedule_poll()
        if self.remote:
            log.info("VRChat on other devices allowed: OSC + OSCQuery listen on "
                     "every interface (OSC %d, OSCQuery %d)", self.osc_port, self.http_port)

    def stop(self) -> None:
        with self._restart_lock:
            self._stop()

    def _stop(self) -> None:
        self._stopped = True
        for timer in (self._refetch_timer, self._poll_timer):
            if timer:
                timer.cancel()
        for srv in (self._osc_server, self._http_server):
            try:
                if srv:
                    srv.shutdown()
                    srv.server_close()   # free the port now: a restart may reuse it
            except Exception:
                pass
        # closing zeroconf while it is still registering us leaves its shared
        # event loop blocked — the next start then fails (EventLoopBlocked)
        mdns = self._mdns_thread
        if mdns is not None and mdns.is_alive() and mdns is not threading.current_thread():
            mdns.join(timeout=5)   # registering takes ~2 s; never hang a quit long
        if self._zc:
            try:
                for info in self._infos:
                    self._zc.unregister_service(info)
                self._zc.close()
            except Exception:
                pass

    def restart(self) -> None:
        """Apply changed connection settings (other devices, ports): close
        everything and come up again. VRChat rediscovers us within seconds."""
        with self._restart_lock:   # a second Save waits for the first restart
            log.info("restarting the VRChat connection with the new settings")
            self._stop()
            self._zc = None
            self._infos = []
            self._browser = None
            self.advertised = False
            self.vrchat_http = self._last_vrchat_http = None
            self._vrchat_name = self._vrchat_host = None
            self._vrchat_clients = {}
            self._vrchat_addrs = set()
            self._ignored_sources = set()
            self._sync_fails = 0
            self._stopped = False
            self.start()
        self._emit_status()

    # -- OSC receive ---------------------------------------------------------

    def _start_osc_server(self) -> None:
        dispatcher = Dispatcher()
        dispatcher.map("/avatar/change", self._handle_avatar_change, needs_reply_address=True)
        dispatcher.set_default_handler(self._handle_message, needs_reply_address=True)
        self._osc_server = _bind_with_fallback(
            lambda host, port: ThreadingOSCUDPServer((host, port), dispatcher),
            self.bind_host, int(self.store.settings.get("osc_in_port") or 0),
            "OSC", self.port_warnings, socket.SOCK_DGRAM, RESERVED_OSC_PORTS)
        self.osc_port = self._osc_server.socket.getsockname()[1]
        threading.Thread(target=self._osc_server.serve_forever, name="osc-server", daemon=True).start()
        log.info("OSC input listening on %s:%d", self.bind_host, self.osc_port)

    def _accept_from(self, source: Any) -> bool:
        """Same-PC mode listens on loopback only, so anything that arrives is
        local. Listening on the network, only the VRChat we talk to may feed
        the board: loopback, or the addresses that client advertised. (A
        filter against stray traffic, not authentication — UDP senders can
        be faked on a network you don't trust.)"""
        if not self.remote:
            return True
        ip = source[0] if isinstance(source, tuple) and source else str(source or "")
        if _is_loopback(ip) or ip in self._own_addrs or ip in self._vrchat_addrs:
            return True   # this machine (by any of its addresses), or our VRChat
        if ip not in self._ignored_sources and len(self._ignored_sources) < MAX_IGNORED_LOGGED:
            self._ignored_sources.add(ip)
            # VRChat may start streaming before its own mDNS record has
            # resolved here; the full OSCQuery sync on discovery catches up
            log.warning("ignoring OSC from %s — not (yet) known as a VRChat "
                        "client on this network", ip)
        return False

    def _handle_message(self, source: Any, address: str, *args: Any) -> None:
        if not address.startswith(PARAM_PREFIX) or not args:
            return
        if not self._accept_from(source):
            return
        self.last_received = time.time()
        name = address[len(PARAM_PREFIX):]
        ptype, value = _coerce(args[0])
        if ptype:
            self.on_param(name, ptype, value)

    def _handle_avatar_change(self, source: Any, address: str, *args: Any) -> None:
        if not self._accept_from(source):
            return
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

    def host_info(self, local_ip: Optional[str] = None) -> dict:
        """Where VRChat should send OSC: the address the asking client
        reached us on (a Quest on the LAN gets our LAN address), loopback
        for anything local or when we only listen locally anyway."""
        osc_ip = "127.0.0.1"
        if self.remote and local_ip and not _is_loopback(local_ip) and local_ip != "0.0.0.0":
            osc_ip = local_ip
        return oscquery.build_host_info(self.service_name, self.osc_port, osc_ip)

    def tree_node(self, path: str) -> Optional[dict]:
        return oscquery.node_at(oscquery.build_advertise_tree(self.service_name), path)

    def _start_http_server(self) -> None:
        self._http_server = _bind_with_fallback(
            lambda host, port: oscquery.make_http_server(self, host, port),
            self.bind_host, int(self.store.settings.get("oscquery_port") or 0),
            "OSCQuery", self.port_warnings, socket.SOCK_STREAM)
        self.http_port = self._http_server.server_address[1]
        threading.Thread(target=self._http_server.serve_forever, name="oscquery-http", daemon=True).start()
        log.info("OSCQuery HTTP on %s:%d", self.bind_host, self.http_port)

    # -- mDNS ------------------------------------------------------------------

    def _start_mdns(self) -> None:
        try:
            self._zc = Zeroconf()
            addresses = [socket.inet_aton("127.0.0.1")]
            lan_ip = _local_ip()
            if lan_ip:
                # another device can only use the LAN address: list it first
                # when it's invited (some clients just take the first one)
                if self.remote:
                    addresses.insert(0, socket.inet_aton(lan_ip))
                else:
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
            log.warning("mDNS setup failed: %s: %s", type(exc).__name__, exc)
        self._emit_status()

    def _emit_status(self) -> None:
        self.on_status(self.status())

    def status(self) -> dict:
        host, port = self._send_target
        return {
            "advertised": self.advertised,
            "osc_port": self.osc_port,
            "oscquery_port": self.http_port,
            "bind_host": self.bind_host,
            "remote": self.remote,
            "port_warnings": list(self.port_warnings),
            "vrchat_found": bool(self.vrchat_http),
            "vrchat_host": self._vrchat_host,
            "send_target": f"{host}:{port}",
            "receiving": (time.time() - self.last_received) < 5.0,
        }

    # -- VRChat discovery + tree fetch -------------------------------------------

    def _vrchat_address(self, addresses: list[str]) -> str:
        """Which of a VRChat client's advertised addresses to talk to.

        Desktop VRChat advertises (and only answers on) 127.0.0.1. The
        Android build advertises its LAN address — reachable only when we
        listen on the network too, so outside remote mode it's loopback."""
        ipv4 = [a for a in addresses if ":" not in a]
        if not self.remote or not ipv4 or any(_is_loopback(a) for a in ipv4):
            return "127.0.0.1"
        return ipv4[0]

    def _vrchat_seen(self, name: str, addresses: list[str], port: int) -> None:
        self._vrchat_clients[name] = ([a for a in addresses if a], port)
        if (self.remote and self.vrchat_http and self._vrchat_name not in (None, name)
                and time.time() - self._vrchat_ok_at < STICKY_FOR):
            # stay with the client we talk to while it answers: a second
            # device — or anything else calling itself VRChat — can't take
            # over the board
            log.info("another VRChat client on the network (%s) — staying with %s",
                     name, self._vrchat_name)
            return
        self._connect_vrchat(name)

    def _connect_vrchat(self, name: str) -> bool:
        entry = self._vrchat_clients.get(name)
        if entry is None:   # said goodbye a moment ago (zeroconf thread)
            return False
        addresses, port = entry
        host = self._vrchat_address(addresses)
        url = f"http://{host}:{port}"
        if url != self.vrchat_http:
            log.info("VRChat found: %s at %s", name, url)
        self._vrchat_name = name
        self._vrchat_host = host
        self._vrchat_addrs = set(addresses)
        self._vrchat_ok_at = time.time()   # a grace period to answer
        self._vrchat_service_found(url)
        return True

    def _connect_other_client(self) -> bool:
        """The client we talked to is gone: another one we've seen, if any.

        Remote mode only (a Quest and a phone, say). On the same PC a new
        VRChat launch announces itself anyway — and a leftover record there
        is a crashed launch, not a second device worth switching to."""
        if not self.remote:
            return False
        for name in list(self._vrchat_clients):
            if name != self._vrchat_name and self._connect_vrchat(name):
                return True
        return False

    def _vrchat_gone(self, name: str) -> None:
        """mDNS goodbye. Only the client we talk to disconnects us — another
        VRChat (a second device) leaving changes nothing."""
        self._vrchat_clients.pop(name, None)
        if self._vrchat_name in (None, name):
            self._vrchat_name = self._vrchat_host = None
            self._vrchat_addrs = set()
            self._vrchat_service_found(None)
            self._connect_other_client()

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
        self._poll_timer = threading.Timer(POLL_INTERVAL, self._poll_tick, args=(self._gen,))
        self._poll_timer.daemon = True
        self._poll_timer.start()

    def _poll_tick(self, gen: Optional[int] = None) -> None:
        if gen is not None and gen != self._gen:
            return   # a timer from before a restart: the new chain runs instead
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
            if gen is None or gen == self._gen:
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
        gen = self._gen
        if not base:
            return
        host_info = self._http_json(base + "/?HOST_INFO")
        if gen != self._gen or base != self.vrchat_http:
            return   # restarted or switched clients meanwhile: not ours to judge
        if host_info is None:
            # VRChat quit without an mDNS goodbye (its record TTL can keep the
            # service "alive" in the cache for a long time) — after two
            # consecutive failures, treat it as disconnected. The poll keeps
            # probing the last-known URL in case it comes back.
            self._sync_fails += 1
            if self._sync_fails >= 2 and self.vrchat_http:
                log.info("VRChat's OSCQuery service stopped responding — disconnected")
                self.vrchat_http = None
                # gone without an mDNS goodbye (a crash): forget it, or we'd
                # keep coming back to it. The poll still probes its last
                # address in case it was only a long loading screen.
                if self._vrchat_name:
                    self._vrchat_clients.pop(self._vrchat_name, None)
                if self._connect_other_client():   # a second device still there
                    return
            self._emit_status()
            return
        self._sync_fails = 0
        self._vrchat_ok_at = time.time()
        if host_info.get("OSC_PORT"):
            osc_ip = str(host_info.get("OSC_IP") or "127.0.0.1")
            vrc_host = self._vrchat_host
            if (vrc_host and not _is_loopback(vrc_host)
                    and (_is_loopback(osc_ip) or osc_ip == "0.0.0.0")):
                # a remote VRChat that names no usable address: it's at the
                # address we just reached its OSCQuery on
                osc_ip = vrc_host
            target = (osc_ip, int(host_info["OSC_PORT"]))
            if target != self._send_target:
                self._send_target = target
                self._make_client()
        tree = self._http_json(base + "/")
        if gen != self._gen or base != self.vrchat_http:
            return   # restarted or switched while the tree was loading
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
        try:
            addresses = info.parsed_addresses()
        except Exception:  # noqa: BLE001 — a malformed record
            addresses = []
        self.link._vrchat_seen(name, addresses, info.port)

    def update_service(self, zc: Zeroconf, svc_type: str, name: str) -> None:
        self.add_service(zc, svc_type, name)

    def remove_service(self, zc: Zeroconf, svc_type: str, name: str) -> None:
        if name.startswith("VRChat-Client"):
            self.link._vrchat_gone(name)


def _coerce(value: Any) -> tuple[Optional[str], Any]:
    if isinstance(value, bool):
        return "Bool", value
    if isinstance(value, int):
        return "Int", value
    if isinstance(value, float):
        return "Float", round(value, 4)
    return None, None
