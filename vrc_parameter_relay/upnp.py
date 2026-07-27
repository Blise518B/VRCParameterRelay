"""Minimal UPnP-IGD client: find the router, open/close a TCP port.

Used by self-hosted sharing to forward the guest-page port automatically.
Standard library only, no accounts, no services — and strictly fail-open:
every call degrades to a "couldn't, do it manually" answer, never an exception.
"""
from __future__ import annotations

import logging
import socket
import threading
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

log = logging.getLogger(__name__)

SSDP_ADDR = ("239.255.255.250", 1900)
_SEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 2\r\n"
    "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n"
)

# mappings created this session, so we can undo them on exit
_mapped: list[tuple[str, str, int]] = []  # (control_url, service_type, ext_port)


def _discover(timeout: float = 3.0) -> Optional[str]:
    """SSDP search; returns the gateway's description URL or None."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(_SEARCH.encode(), SSDP_ADDR)
        while True:
            data, _ = sock.recvfrom(4096)
            for line in data.decode(errors="replace").splitlines():
                if line.lower().startswith("location:"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        return None
    finally:
        sock.close()


_DEV_NS = "{urn:schemas-upnp-org:device-1-0}"


def _find_control(xml_text: str, base_url: str) -> Optional[tuple[str, str]]:
    """(control_url, service_type) of the WAN*Connection service, or None."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return None
    for service in root.iter(f"{_DEV_NS}service"):
        stype = (service.findtext(f"{_DEV_NS}serviceType") or "").strip()
        if "WANIPConnection" in stype or "WANPPPConnection" in stype:
            control = (service.findtext(f"{_DEV_NS}controlURL") or "").strip()
            if control:
                return urljoin(base_url, control), stype
    return None


def _soap_body(service_type: str, action: str, args: dict) -> str:
    inner = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
    return (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{action} xmlns:u="{service_type}">{inner}</u:{action}></s:Body>'
        "</s:Envelope>"
    )


def _soap(control_url: str, service_type: str, action: str, args: dict) -> str:
    req = Request(control_url, data=_soap_body(service_type, action, args).encode(),
                  headers={"Content-Type": 'text/xml; charset="utf-8"',
                           "SOAPACTION": f'"{service_type}#{action}"'})
    with urlopen(req, timeout=5) as res:
        return res.read().decode(errors="replace")


def _parse_tag(xml_text: str, tag: str) -> Optional[str]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return None
    for el in root.iter():
        if el.tag.endswith(tag):
            return (el.text or "").strip()
    return None


def _local_ip_towards(host: str) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((host, 1900))
        return sock.getsockname()[0]
    finally:
        sock.close()


def open_port(external_port: int, internal_port: int,
              description: str = "VRC Parameter Relay") -> tuple[bool, str]:
    """Map router:external_port -> this machine:internal_port (TCP).

    Returns (ok, info): info is the router's public IP on success (may be
    empty) or a human-readable reason on failure.
    """
    location = _discover()
    if not location:
        return False, "no UPnP router found"
    try:
        with urlopen(location, timeout=5) as res:
            xml_text = res.read().decode(errors="replace")
    except OSError as exc:
        return False, f"router description unreachable ({exc})"
    found = _find_control(xml_text, location)
    if not found:
        return False, "router offers no port-mapping service"
    control, stype = found
    gateway_host = urlsplit(location).hostname or ""
    my_ip = _local_ip_towards(gateway_host)
    try:
        _soap(control, stype, "AddPortMapping", {
            "NewRemoteHost": "", "NewExternalPort": external_port,
            "NewProtocol": "TCP", "NewInternalPort": internal_port,
            "NewInternalClient": my_ip, "NewEnabled": 1,
            "NewPortMappingDescription": description, "NewLeaseDuration": 0,
        })
    except OSError as exc:
        return False, f"router refused the mapping ({exc})"
    _mapped.append((control, stype, external_port))
    log.info("UPnP: mapped router:%d -> %s:%d", external_port, my_ip, internal_port)
    external_ip = ""
    try:
        response = _soap(control, stype, "GetExternalIPAddress", {})
        external_ip = _parse_tag(response, "NewExternalIPAddress") or ""
    except OSError:
        pass
    return True, external_ip


def cleanup_mappings() -> None:
    """Remove every mapping this session created (called on app exit)."""
    while _mapped:
        control, stype, port = _mapped.pop()
        try:
            _soap(control, stype, "DeletePortMapping",
                  {"NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": "TCP"})
            log.info("UPnP: removed mapping for port %d", port)
        except OSError:
            pass


def map_async(settings: dict, internal_port: int, notify: Callable[[dict], None]) -> None:
    """Fire-and-forget UPnP attempt; notify() receives a {"t": "upnp"} event."""
    def work() -> None:
        try:
            ext_port = int(settings.get("selfhost_port")
                           or settings.get("web_port") or internal_port)
        except (TypeError, ValueError):
            ext_port = internal_port
        ok, info = open_port(ext_port, internal_port)
        notify({"t": "upnp", "ok": ok, "info": info, "port": ext_port})

    threading.Thread(target=work, name="upnp", daemon=True).start()
