"""VRChat on another device (Quest standalone / Android) + fixed OSC ports.

Starts tools/fake_vrchat.py --remote — it binds and advertises only this
machine's LAN address and refuses loopback, the way VRChat's Android build
sees a PC on the network — and checks, with the relay's VrcLink:

  * other devices off: such a VRChat can't connect (same-PC only, as before)
  * other devices on: discovery at the LAN address, full sync, OSC both ways
    over the LAN, HOST_INFO answers each client with the address it used
  * fixed ports are used; a taken port falls back to a random one + warning
  * restart() applies changed settings and can rebind the same fixed ports
  * listening on the network, OSC from an address no VRChat advertised is
    ignored (real UDP packets), and the relay stays with the first VRChat
  * a port another program holds on 0.0.0.0 counts as taken (Windows would
    let a 127.0.0.1 bind share it); VRChat's own 9000 is refused
  * two restarts at the same moment don't leak servers or lose mDNS

Throwaway data dir. Uses the LAN (mDNS + this machine's LAN address), no
internet. Exits 0 on success.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = tempfile.mkdtemp(prefix="vrcpr-remote-")

from vrc_parameter_relay.osc_link import VrcLink  # noqa: E402
from vrc_parameter_relay.store import Store  # noqa: E402

FAILS: list[str] = []
FAKE_AVATAR = "avtr_deadbeef-0000-4000-8000-c0ffee000001"


def check(desc: str, cond: bool) -> None:
    print(("OK   " if cond else "FAIL ") + desc, flush=True)
    if not cond:
        FAILS.append(desc)


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def free_port(kind: int) -> int:
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("0.0.0.0", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for(cond, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.2)
    return False


def host_info(ip: str, port: int) -> dict:
    with urllib.request.urlopen(f"http://{ip}:{port}/?HOST_INFO", timeout=4) as res:
        return json.loads(res.read().decode("utf-8"))


class Recorder:
    def __init__(self, link: VrcLink) -> None:
        self.params: dict = {}
        self.synced: list = []
        link.on_param = lambda name, ptype, value: self.params.__setitem__(name, value)
        link.on_full_sync = lambda avatar, params: self.synced.append(avatar)


def start_fake(log_path: Path) -> subprocess.Popen:
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-u", str(ROOT / "tools" / "fake_vrchat.py"),
                             "--remote"], stdout=log, stderr=subprocess.STDOUT)
    wait_for(lambda: "advertising" in log_path.read_text("utf-8", "replace"), 15)
    return proc


def port_in_use(kind: int, port: int) -> bool:
    s = socket.socket(socket.AF_INET, kind)
    try:
        s.bind(("0.0.0.0", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def before_any_vrchat(lan: str) -> None:
    """Checks that need no VRChat on the network (run before the fake starts)."""
    store = Store()
    store.set("osc_remote", True)
    link = VrcLink(store)
    got: list = []
    link.on_param = lambda name, ptype, value: got.append(name)
    link.start()
    try:
        # real UDP: from this machine's LAN address (no VRChat announced it)...
        from pythonosc.udp_client import SimpleUDPClient
        lan_sender = SimpleUDPClient(lan, link.osc_port)
        lan_sender._sock.bind((lan, 0))
        lan_sender.send_message("/avatar/parameters/FromLan", True)
        # ...and from loopback (this machine)
        SimpleUDPClient("127.0.0.1", link.osc_port).send_message(
            "/avatar/parameters/FromLoopback", True)
        wait_for(lambda: "FromLoopback" in got and "FromLan" in got, 5)
        check("real UDP: OSC from loopback is accepted", "FromLoopback" in got)
        # (another machine's packets can't be sent from here — the unknown-
        # sender case is checked through the handler further down)
        check("real UDP: OSC from this machine's own LAN address counts as local",
              "FromLan" in got)

        # one VRChat at a time: the first stays while it is there
        link._vrchat_seen("VRChat-Client-AAAAAA", ["10.1.1.1"], 9)
        link._vrchat_seen("VRChat-Client-BBBBBB", ["10.1.1.2"], 9)
        check("two VRChat clients: stays with the first",
              link._vrchat_name == "VRChat-Client-AAAAAA" and "10.1.1.2" not in link._vrchat_addrs)
        link._vrchat_gone("VRChat-Client-BBBBBB")
        check("...the other leaving changes nothing", link._vrchat_name == "VRChat-Client-AAAAAA")
        link._vrchat_seen("VRChat-Client-BBBBBB", ["10.1.1.2"], 9)
        link._vrchat_gone("VRChat-Client-AAAAAA")
        check("...the first leaving switches to the one still there",
              link._vrchat_name == "VRChat-Client-BBBBBB"
              and link._vrchat_addrs == {"10.1.1.2"})

        # clients that die without a goodbye (crashes) are dropped, never
        # cycled through; a live newcomer is then picked up
        def fail_twice() -> None:
            for _ in range(2):
                link._sync_from_vrchat()

        link._http_json = lambda url: None          # nothing answers
        link._vrchat_seen("VRChat-Client-CCCCCC", ["10.1.1.3"], 9)
        fail_twice()   # B is dead -> dropped, C (also dead) is tried
        check("a dead client is dropped after two failed polls",
              "VRChat-Client-BBBBBB" not in link._vrchat_clients
              and link._vrchat_name == "VRChat-Client-CCCCCC")
        fail_twice()   # C is dead too
        check("...every dead one is dropped, none cycled back",
              link._vrchat_clients == {} and link.vrchat_http is None)
        link._vrchat_seen("VRChat-Client-DDDDDD", ["10.1.1.4"], 9)
        check("...and a live newcomer is picked up at once",
              link._vrchat_name == "VRChat-Client-DDDDDD")
        link._vrchat_ok_at = time.time() - 60   # D stopped answering a while ago
        link._vrchat_seen("VRChat-Client-EEEEEE", ["10.1.1.5"], 9)
        check("a client that hasn't answered for a while doesn't block a newcomer",
              link._vrchat_name == "VRChat-Client-EEEEEE")
    finally:
        link.stop()

    # same PC (default): a new VRChat launch takes over at once, as before
    store = Store()
    link = VrcLink(store)
    link._http_json = lambda url: None
    link._vrchat_seen("VRChat-Client-OLD111", ["127.0.0.1"], 9)
    link._vrchat_seen("VRChat-Client-NEW222", ["127.0.0.1"], 9)
    check("same PC: the newest VRChat launch takes over", link._vrchat_name == "VRChat-Client-NEW222")
    link._vrchat_gone("VRChat-Client-NEW222")
    check("same PC: after its goodbye, no jump to an older (crashed) launch",
          link._vrchat_name is None and link.vrchat_http is None)

    # a poll timer from before a restart doesn't start a second poll chain
    link._gen = 5
    scheduled: list = []
    link._schedule_poll = lambda: scheduled.append(1)
    link._poll_tick(4)
    check("a poll from before a restart stops instead of running twice", scheduled == [])
    link._poll_tick(5)
    check("...the current one keeps polling", scheduled == [1])

    # a port held on 0.0.0.0 by another program is taken, even though Windows
    # would let our 127.0.0.1 bind succeed and split the traffic
    holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    holder.bind(("0.0.0.0", 0))
    held = holder.getsockname()[1]
    store = Store()
    store.set("osc_remote", False)          # binds 127.0.0.1
    store.set("osc_in_port", held)
    link = VrcLink(store)
    link.start()
    check("a port held on 0.0.0.0 counts as taken: random port + warning",
          link.osc_port != held and any("in use" in w for w in link.port_warnings))
    link.stop()
    holder.close()
    store.set("osc_in_port", 9000)
    link = VrcLink(store)
    link.start()
    check("OSC port 9000 (VRChat's own) is refused",
          link.osc_port != 9000 and any("VRChat's own" in w for w in link.port_warnings))
    link.stop()


def main() -> int:
    lan = lan_ip()
    print(f"this machine's LAN address: {lan}")
    before_any_vrchat(lan)
    log_path = Path(tempfile.mkdtemp(prefix="vrcpr-fake-")) / "fake.log"
    fake = start_fake(log_path)
    fake_log = lambda: log_path.read_text("utf-8", "replace")  # noqa: E731
    store = Store()
    try:
        # -- other devices OFF: the Quest-like VRChat can't reach us -----------
        link = VrcLink(store)
        rec = Recorder(link)
        link.start()
        check("off: listens on loopback only", link.bind_host == "127.0.0.1")
        check("off: HOST_INFO points at loopback",
              host_info("127.0.0.1", link.http_port)["OSC_IP"] == "127.0.0.1")
        time.sleep(8)
        check("off: no parameters arrive from a VRChat on another device", not rec.params)
        check("off: and it isn't synced", not rec.synced)
        link.stop()

        # -- other devices ON, fixed ports -----------------------------------
        osc_port, oq_port = free_port(socket.SOCK_DGRAM), free_port(socket.SOCK_STREAM)
        store.set("osc_remote", True)
        store.set("osc_in_port", osc_port)
        store.set("oscquery_port", oq_port)
        link = VrcLink(store)
        rec = Recorder(link)
        link.start()
        st = link.status()
        check("on: listens on every interface", st["bind_host"] == "0.0.0.0" and st["remote"])
        check("on: the fixed OSC port is used", link.osc_port == osc_port)
        check("on: the fixed OSCQuery port is used", link.http_port == oq_port)
        check("on: no port warnings", not st["port_warnings"])
        check("HOST_INFO via the LAN address names the LAN address",
              host_info(lan, oq_port)["OSC_IP"] == lan)
        check("HOST_INFO via loopback still names loopback",
              host_info("127.0.0.1", oq_port)["OSC_IP"] == "127.0.0.1")

        found = wait_for(lambda: (link.vrchat_http or "").startswith(f"http://{lan}:"), 25)
        check("on: VRChat discovered at the address it advertises (not 127.0.0.1)", found)
        check("on: full sync from VRChat's OSCQuery over the LAN",
              wait_for(lambda: FAKE_AVATAR in rec.synced, 15))
        # (its very first burst may land before VRChat's own mDNS record has
        # resolved here and be ignored — the full sync above covers it; what
        # matters is the live stream after that)
        check("on: VRChat streams parameter changes to us over the LAN",
              wait_for(lambda: "VelocityX" in rec.params, 20))
        check("on: OSC goes out to VRChat's LAN address",
              wait_for(lambda: link.status()["send_target"].startswith(f"{lan}:"), 10))
        link.send_param("TailWag", "Bool", True)
        check("on: a value we send reaches VRChat",
              wait_for(lambda: "RECV SET TailWag = True" in fake_log(), 10))

        # OSC from addresses no VRChat advertised is ignored
        got: list = []
        link.on_param = lambda name, ptype, value: got.append((name, value))
        link._handle_message(("10.9.8.7", 4242), "/avatar/parameters/Spoof", True)
        check("on: OSC from an unknown address on the network is ignored", not got)
        link._handle_message((lan, 4242), "/avatar/parameters/Real", True)
        link._handle_message(("127.0.0.1", 4242), "/avatar/parameters/Local", True)
        check("on: ...but VRChat's address and loopback are accepted",
              [n for n, _ in got] == ["Real", "Local"])

        # -- restart applies new settings and reuses the fixed ports ----------
        store.set("osc_remote", False)
        link.restart()
        check("restart: back on loopback", link.bind_host == "127.0.0.1")
        check("restart: the same fixed ports bind again",
              link.osc_port == osc_port and link.http_port == oq_port)
        store.set("osc_remote", True)
        link.restart()
        check("restart: on the network again", link.bind_host == "0.0.0.0")
        check("restart: VRChat reconnects",
              wait_for(lambda: (link.vrchat_http or "").startswith(f"http://{lan}:"), 25))
        link.restart()
        link.restart()   # again at once, while mDNS is still registering
        check("back-to-back restarts: still advertised on mDNS",
              wait_for(lambda: link.advertised, 15))

        # two Saves at the same moment: restarts from two threads
        threads = [threading.Thread(target=link.restart) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(40)
        check("simultaneous restarts: still on the fixed ports, no warnings",
              link.osc_port == osc_port and link.http_port == oq_port
              and not link.port_warnings)
        check("simultaneous restarts: advertised on mDNS",
              wait_for(lambda: link.advertised, 15))
        check("simultaneous restarts: VRChat reconnects",
              wait_for(lambda: (link.vrchat_http or "").startswith(f"http://{lan}:"), 25))

        # -- a taken port falls back to a random one -------------------------
        blocker_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker_udp.bind(("0.0.0.0", 0))
        blocker_tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker_tcp.bind(("0.0.0.0", 0))
        blocker_tcp.listen(1)
        store2_ports = (blocker_udp.getsockname()[1], blocker_tcp.getsockname()[1])
        store.set("osc_in_port", store2_ports[0])
        store.set("oscquery_port", store2_ports[1])
        link2 = VrcLink(store)
        link2.start()
        st2 = link2.status()
        check("taken ports: still starts, on other ports",
              link2.osc_port not in (0, store2_ports[0])
              and link2.http_port not in (0, store2_ports[1]))
        check("taken ports: one warning each",
              len(st2["port_warnings"]) == 2
              and all("in use" in w for w in st2["port_warnings"]))
        link2.stop()
        blocker_udp.close()
        blocker_tcp.close()
        link.stop()
        check("after stop no server is left behind: the fixed ports are free",
              wait_for(lambda: not port_in_use(socket.SOCK_DGRAM, osc_port)
                       and not port_in_use(socket.SOCK_STREAM, oq_port), 5))
    finally:
        fake.terminate()
        try:
            fake.wait(5)
        except subprocess.TimeoutExpired:
            fake.kill()

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
