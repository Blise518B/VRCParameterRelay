"""Probe the router's UPnP support without changing anything.

Discovers the gateway, finds the port-mapping service and asks for the
public IP — read-only, no mapping is created. Run it on the real network:

  .venv\\Scripts\\python.exe tools\\upnp_probe.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).parent.parent))

from vrc_parameter_relay import upnp


def main() -> int:
    print("searching for a UPnP router (3s)…")
    location = upnp._discover()
    if not location:
        print("NO ROUTER FOUND — UPnP may be disabled on it, or multicast is blocked.")
        return 1
    print(f"router description: {location}")
    try:
        with urlopen(location, timeout=5) as res:
            xml_text = res.read().decode(errors="replace")
    except OSError as exc:
        print(f"description unreachable: {exc}")
        return 1
    found = upnp._find_control(xml_text, location)
    if not found:
        print("router offers NO port-mapping service (UPnP/IGD disabled?)")
        return 1
    control, stype = found
    print(f"port-mapping service: {stype}")
    print(f"control url:          {control}")
    try:
        response = upnp._soap(control, stype, "GetExternalIPAddress", {})
        print(f"public IP:            {upnp._parse_tag(response, 'NewExternalIPAddress')}")
    except OSError as exc:
        print(f"GetExternalIPAddress failed: {exc}")
        return 1
    print("UPnP looks GOOD — automatic port opening should work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
