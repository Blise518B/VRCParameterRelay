"""Hermetic test of self-hosted sharing — bind modes, rebind, URL, UPnP parsing.

No mDNS, no real router traffic (UPnP is tested on canned XML only).
Safe to run while VRChat is open. Exits 0 on success.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import urllib.request

from vrc_parameter_relay import upnp
from vrc_parameter_relay.core import AppCore
from vrc_parameter_relay.store import Store
from vrc_parameter_relay.webserver import GuestServer, compose_selfhost_url


class StubLink:
    on_param = on_avatar = on_full_sync = on_status = None

    def send_param(self, *a):
        return True


def check(desc: str, cond: bool) -> None:
    if not cond:
        print(f"FAIL {desc}")
        sys.exit(1)
    print(f"OK   {desc}")


def wait_health(port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=2) as res:
                if res.status == 200:
                    return True
        except OSError:
            time.sleep(0.15)
    return False


def main() -> None:
    # -- URL composition --------------------------------------------------
    check("bare host + port",
          compose_selfhost_url("myname.mooo.com", 3080, "tok")
          == "http://myname.mooo.com:3080/?k=tok")
    check("host with explicit port wins",
          compose_selfhost_url("myname.mooo.com:8443", 3080, "tok")
          == "http://myname.mooo.com:8443/?k=tok")
    check("https reverse proxy, default port omitted",
          compose_selfhost_url("https://relay.example.com", 443, "tok")
          == "https://relay.example.com/?k=tok")
    check("http default port omitted",
          compose_selfhost_url("myip.example.org", 80, "tok")
          == "http://myip.example.org/?k=tok")
    check("empty host -> empty url", compose_selfhost_url("", 3080, "tok") == "")
    check("trailing slash stripped",
          compose_selfhost_url("host.tld/", 81, "t") == "http://host.tld:81/?k=t")

    # -- UPnP XML parsing (canned FritzBox-style responses) -----------------
    igd_xml = """<?xml version="1.0"?>
    <root xmlns="urn:schemas-upnp-org:device-1-0">
      <device><deviceList><device><deviceList><device>
        <serviceList>
          <service>
            <serviceType>urn:schemas-upnp-org:service:WANPPPConnection:1</serviceType>
            <controlURL>/igdupnp/control/WANPPPConn1</controlURL>
          </service>
        </serviceList>
      </device></deviceList></device></deviceList></device>
    </root>"""
    found = upnp._find_control(igd_xml, "http://192.0.2.1:49000/igddesc.xml")
    check("control url found in IGD xml", found is not None)
    control, stype = found
    check("control url joined against base",
          control == "http://192.0.2.1:49000/igdupnp/control/WANPPPConn1")
    check("service type detected", "WANPPPConnection" in stype)

    body = upnp._soap_body(stype, "AddPortMapping", {
        "NewExternalPort": 3080, "NewInternalClient": "192.0.2.20"})
    check("soap body carries the port", "<NewExternalPort>3080</NewExternalPort>" in body)
    check("soap body names the action", "u:AddPortMapping" in body)

    ip_xml = ('<?xml version="1.0"?><s:Envelope '
              'xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
              '<u:GetExternalIPAddressResponse '
              'xmlns:u="urn:schemas-upnp-org:service:WANPPPConnection:1">'
              '<NewExternalIPAddress>203.0.113.7</NewExternalIPAddress>'
              '</u:GetExternalIPAddressResponse></s:Body></s:Envelope>')
    check("external ip parsed", upnp._parse_tag(ip_xml, "NewExternalIPAddress")
          == "203.0.113.7")
    check("missing tag -> None", upnp._parse_tag(ip_xml, "NoSuchTag") is None)
    check("broken xml -> None", upnp._parse_tag("<not-xml", "X") is None)

    # -- default bind: loopback only ---------------------------------------
    store = Store()
    store.set("advanced_link", False)      # explicit baseline — other suites
    store.set("tunnel_provider", "cloudflare")  # may share this data dir
    core = AppCore(store, StubLink())
    web = GuestServer(core, 0)
    web.start()
    deadline = time.time() + 10
    while not web.port and time.time() < deadline:
        time.sleep(0.1)
    check("web server up", bool(web.port))
    check("default bind is loopback", web.bind_host == "127.0.0.1")
    check("health reachable", wait_health(web.port))

    # -- selfhost bind: all interfaces, applied via live rebind -------------
    store.set("advanced_link", True)
    store.set("tunnel_provider", "selfhost")
    old_port = web.port
    web.rebind(0)  # new random port + re-evaluated bind host
    deadline = time.time() + 10
    while (web.bind_host != "*" or web.port == old_port) and time.time() < deadline:
        time.sleep(0.1)
    check("rebind switched to all interfaces (dual-stack)", web.bind_host == "*")
    check("rebound port is live", wait_health(web.port))

    # -- back to loopback ---------------------------------------------------
    store.set("advanced_link", False)
    store.set("tunnel_provider", "cloudflare")
    web.rebind(0)
    deadline = time.time() + 10
    while web.bind_host != "127.0.0.1" and time.time() < deadline:
        time.sleep(0.1)
    check("rebind back to loopback", web.bind_host == "127.0.0.1")
    check("loopback port live again", wait_health(web.port))

    print("ALL SELFHOST TESTS PASSED")


if __name__ == "__main__":
    main()
