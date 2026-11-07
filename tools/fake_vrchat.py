"""Fake VRChat for end-to-end testing without VRChat running.

Mirrors real client behaviour:
  * advertises "VRChat-Client-FAKE" via mDNS (_oscjson._tcp + _osc._udp)
  * serves an OSCQuery HTTP tree with HOST_INFO + avatar parameters
  * discovers other OSCQuery services (like VRC Parameter Relay), reads their
    HOST_INFO and streams /avatar/change + parameter values to them
  * receives OSC on its own port, prints "RECV SET ..." lines and echoes
    the new value back out (VRChat re-broadcasts changed parameters)

Run:  python tools/fake_vrchat.py            (desktop VRChat: loopback only)
      python tools/fake_vrchat.py --remote   (the Android build — Quest standalone,
          phones: binds and advertises this machine's LAN address, never
          loopback, and sends OSC only to apps that give it a LAN address)
"""
from __future__ import annotations

import json
import math
import socket
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient
from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf

AVATARS: list[dict] = [
    {
        "id": "avtr_deadbeef-0000-4000-8000-c0ffee000001",
        "params": {
            "Hoodie": ["T", True],
            "TailWag": ["T", False],
            "GlowToggle": ["T", False],
            "Brightness": ["f", 0.75],
            "TailSpeed": ["f", 0.2],
            "OutfitIndex": ["i", 1],
            "EmoteIndex": ["i", 0],
            "VelocityX": ["f", 0.0],
            "Viseme": ["i", 0],
        },
    },
    {
        "id": "avtr_cafebabe-0000-4000-8000-0123456789ab",
        "params": {
            "WingsOut": ["T", False],
            "Visor": ["T", True],
            "EyeGlow": ["f", 0.5],
            "SuitColor": ["i", 2],
            "VelocityX": ["f", 0.0],
            "Viseme": ["i", 0],
        },
    },
]

AVATAR_ID = AVATARS[0]["id"]  # kept for integration_test.py
PARAM_PREFIX = "/avatar/parameters/"
TREE_LAG = 3.0  # seconds the HTTP tree keeps showing the OLD avatar (like VRChat)


def log(*args) -> None:
    print(*args, flush=True)


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


class FakeVRChat:
    def __init__(self, remote: bool = False) -> None:
        # remote: behave like VRChat on a Quest/phone — everything on the
        # LAN address, as if this were another device on the network
        self.remote = remote
        self.ip = lan_ip() if remote else "127.0.0.1"
        self.subscribers: dict[str, SimpleUDPClient] = {}  # "host:port" -> client
        self.lock = threading.Lock()
        self.current = 0     # avatar being "worn" (OSC side)
        self.tree_index = 0  # avatar the HTTP tree describes (lags behind)

        dispatcher = Dispatcher()
        dispatcher.set_default_handler(self.on_osc)
        self.osc_server = ThreadingOSCUDPServer((self.ip, 0), dispatcher)
        self.osc_port = self.osc_server.socket.getsockname()[1]

        handler = type("H", (Handler,), {"fake": self})
        self.http_server = ThreadingHTTPServer((self.ip, 0), handler)
        self.http_port = self.http_server.server_address[1]

        self.zc = Zeroconf()

    def start(self) -> None:
        threading.Thread(target=self.osc_server.serve_forever, daemon=True).start()
        threading.Thread(target=self.http_server.serve_forever, daemon=True).start()

        addresses = [socket.inet_aton(self.ip)]
        for svc_type, port in (("_oscjson._tcp.local.", self.http_port),
                               ("_osc._udp.local.", self.osc_port)):
            info = ServiceInfo(
                svc_type, f"VRChat-Client-FAKE.{svc_type}",
                addresses=addresses, port=port, properties={"txtvers": "1"},
                server="fake-vrchat.local.",
            )
            self.zc.register_service(info, allow_name_change=True)
        log(f"[fake] advertising VRChat-Client-FAKE  http:{self.http_port}  osc:{self.osc_port}"
            f"  on {self.ip}{'  (remote mode)' if self.remote else ''}")

        ServiceBrowser(self.zc, "_oscjson._tcp.local.", self)
        threading.Thread(target=self.wiggle, daemon=True).start()

    # -- mDNS listener (subscribes to apps like VRC Parameter Relay) ----------------

    def add_service(self, zc: Zeroconf, svc_type: str, name: str) -> None:
        if name.startswith("VRChat-Client"):
            return
        info = zc.get_service_info(svc_type, name, timeout=3000)
        if not info or not info.port:
            return
        host = "127.0.0.1"
        if self.remote:
            # another device can't reach an app's loopback address
            lan = [a for a in info.parsed_addresses()
                   if ":" not in a and not a.startswith("127.")]
            if not lan:
                log(f"[fake] {name} advertises no LAN address — unreachable")
                return
            host = lan[0]
        threading.Thread(target=self.subscribe, args=(name, host, info.port),
                         daemon=True).start()

    def update_service(self, *a) -> None:
        pass

    def remove_service(self, zc, svc_type, name) -> None:
        with self.lock:
            for key in [k for k in self.subscribers if k.startswith(name + "|")]:
                del self.subscribers[key]
        log(f"[fake] unsubscribed {name}")

    def subscribe(self, name: str, host: str, http_port: int) -> None:
        try:
            with urllib.request.urlopen(f"http://{host}:{http_port}/?HOST_INFO", timeout=4) as res:
                host_info = json.loads(res.read().decode())
        except Exception as exc:
            log(f"[fake] HOST_INFO fetch failed for {name}: {exc}")
            return
        osc_port = host_info.get("OSC_PORT")
        if not osc_port:
            return
        osc_ip = str(host_info.get("OSC_IP") or "127.0.0.1")
        if self.remote and osc_ip.startswith("127."):
            log(f"[fake] {name} says send OSC to {osc_ip} — unreachable from another device")
            return
        key = f"{name}|{osc_port}"
        with self.lock:
            if key in self.subscribers:
                return
            client = SimpleUDPClient(osc_ip if self.remote else "127.0.0.1", int(osc_port))
            self.subscribers[key] = client
            avatar = AVATARS[self.current]
        log(f"[fake] subscribed {name} -> {osc_ip if self.remote else '127.0.0.1'}:{osc_port} "
            f"(app: {host_info.get('NAME')})")
        time.sleep(0.3)
        client.send_message("/avatar/change", avatar["id"])
        for pname, (tag, value) in avatar["params"].items():
            client.send_message(PARAM_PREFIX + pname, _typed(tag, value))
        log(f"[fake] sent avatar change + {len(avatar['params'])} param burst to {name}")

    # -- OSC in ----------------------------------------------------------------

    def on_osc(self, address: str, *args) -> None:
        if not address.startswith(PARAM_PREFIX) or not args:
            log(f"[fake] RECV {address} {args}")
            return
        name = address[len(PARAM_PREFIX):]
        value = args[0]
        log(f"[fake] RECV SET {name} = {value!r}")
        with self.lock:
            params = AVATARS[self.current]["params"]
            if name in params:
                params[name][1] = value
        self.broadcast(name, value)  # VRChat echoes changed params back out

    def broadcast(self, name: str, value) -> None:
        with self.lock:
            clients = list(self.subscribers.values())
        for client in clients:
            try:
                client.send_message(PARAM_PREFIX + name, value)
            except OSError:
                pass

    # -- live wiggling params -----------------------------------------------------

    def wiggle(self) -> None:
        t = 0
        while True:
            time.sleep(1.0)
            t += 1
            vx = round(math.sin(t / 3) * 2, 3)
            viseme = t % 15
            with self.lock:
                params = AVATARS[self.current]["params"]
                params["VelocityX"][1] = vx
                params["Viseme"][1] = viseme
            self.broadcast("VelocityX", float(vx))
            self.broadcast("Viseme", int(viseme))

    # -- avatar switching ---------------------------------------------------------

    def switch_avatar(self) -> None:
        """Like a real switch: OSC announces first, the HTTP tree lags behind."""
        with self.lock:
            self.current = (self.current + 1) % len(AVATARS)
            avatar = AVATARS[self.current]
            clients = list(self.subscribers.values())
        log(f"[fake] SWITCHING avatar -> {avatar['id']} (tree lags {TREE_LAG}s)")
        for client in clients:
            client.send_message("/avatar/change", avatar["id"])
            for pname, (tag, value) in avatar["params"].items():
                client.send_message(PARAM_PREFIX + pname, _typed(tag, value))

        def update_tree() -> None:
            time.sleep(TREE_LAG)
            with self.lock:
                self.tree_index = self.current
            log(f"[fake] HTTP tree now shows {avatar['id']}")

        threading.Thread(target=update_tree, daemon=True).start()

    # -- OSCQuery HTTP tree ----------------------------------------------------------

    def tree(self) -> dict:
        with self.lock:
            avatar = AVATARS[self.tree_index]  # deliberately lags on switches
            param_nodes = {
                name: {
                    "FULL_PATH": PARAM_PREFIX + name,
                    "TYPE": tag,
                    "ACCESS": 3,
                    "VALUE": [value],
                }
                for name, (tag, value) in avatar["params"].items()
            }
            avatar_id = avatar["id"]
        return {
            "FULL_PATH": "/", "ACCESS": 0,
            "CONTENTS": {
                "avatar": {
                    "FULL_PATH": "/avatar", "ACCESS": 0,
                    "CONTENTS": {
                        "change": {"FULL_PATH": "/avatar/change", "TYPE": "s",
                                   "ACCESS": 3, "VALUE": [avatar_id]},
                        "parameters": {"FULL_PATH": "/avatar/parameters",
                                       "ACCESS": 0, "CONTENTS": param_nodes},
                    },
                },
            },
        }

    def host_info(self) -> dict:
        return {
            "NAME": "VRChat-Client-FAKE",
            "OSC_IP": self.ip,
            "OSC_PORT": self.osc_port,
            "OSC_TRANSPORT": "UDP",
            "EXTENSIONS": {"ACCESS": True, "VALUE": True, "TYPE": True},
        }


class Handler(BaseHTTPRequestHandler):
    fake: FakeVRChat = None

    def do_GET(self) -> None:  # noqa: N802
        if "HOST_INFO" in self.path:
            body = self.fake.host_info()
        else:
            body = self.fake.tree()
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a) -> None:
        pass


def _typed(tag: str, value):
    if tag == "T":
        return bool(value)
    if tag == "i":
        return int(value)
    return float(value)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--switch", type=float, metavar="SECONDS", default=0,
                        help="switch between two avatars every N seconds")
    parser.add_argument("--remote", action="store_true",
                        help="act like VRChat on a Quest/phone: LAN address only")
    cli = parser.parse_args()

    fake = FakeVRChat(remote=cli.remote)
    fake.start()
    log("[fake] running — Ctrl+C to stop")

    if cli.switch > 0:
        def switcher() -> None:
            while True:
                time.sleep(cli.switch)
                fake.switch_avatar()
        threading.Thread(target=switcher, daemon=True).start()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        sys.exit(0)
