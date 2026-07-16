"""OSCQuery protocol pieces: the HTTP tree we serve and VRChat tree parsing.

Kept free of sockets and threads — `osc_link.VrcLink` wires these into its
mDNS/OSC plumbing.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional, Protocol

OSCJSON_TYPE = "_oscjson._tcp.local."
OSCUDP_TYPE = "_osc._udp.local."

_TYPE_TO_PTYPE = {"f": "Float", "d": "Float", "i": "Int", "h": "Int",
                  "T": "Bool", "F": "Bool", "b": "Bool"}


class TreeProvider(Protocol):
    """What the HTTP server needs from its owner."""

    def host_info(self) -> dict: ...
    def tree_node(self, path: str) -> Optional[dict]: ...


# -- what we serve to VRChat ---------------------------------------------------

def build_host_info(name: str, osc_port: int) -> dict:
    return {
        "NAME": name,
        "OSC_IP": "127.0.0.1",
        "OSC_PORT": osc_port,
        "OSC_TRANSPORT": "UDP",
        "EXTENSIONS": {"ACCESS": True, "CLIPMODE": False, "RANGE": True,
                       "TYPE": True, "VALUE": True},
    }


def build_advertise_tree(name: str) -> dict:
    """Exposing /avatar makes VRChat stream avatar change + parameters to us."""
    return {
        "DESCRIPTION": name,
        "FULL_PATH": "/",
        "ACCESS": 0,
        "CONTENTS": {
            "avatar": {
                "FULL_PATH": "/avatar",
                "ACCESS": 2,
                "CONTENTS": {
                    "change": {"FULL_PATH": "/avatar/change", "TYPE": "s", "ACCESS": 2},
                    "parameters": {"FULL_PATH": "/avatar/parameters", "ACCESS": 2,
                                   "CONTENTS": {}},
                },
            },
        },
    }


def node_at(tree: dict, path: str) -> Optional[dict]:
    node = tree
    if path in ("", "/"):
        return node
    for part in path.strip("/").split("/"):
        node = (node.get("CONTENTS") or {}).get(part)
        if node is None:
            return None
    return node


class _Handler(BaseHTTPRequestHandler):
    """Serves HOST_INFO and the node tree VRChat checks before sending data."""

    provider: TreeProvider = None  # set by make_http_server

    def do_GET(self) -> None:  # noqa: N802
        path, _, query = self.path.partition("?")
        if "HOST_INFO" in query:
            self._json(self.provider.host_info())
            return
        node = self.provider.tree_node(path)
        if node is None:
            self._json({"MESSAGE": "no such node"}, status=404)
        else:
            self._json(node)

    def _json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # silence default stderr logging
        pass


def make_http_server(provider: TreeProvider) -> ThreadingHTTPServer:
    handler = type("Handler", (_Handler,), {"provider": provider})
    return ThreadingHTTPServer(("127.0.0.1", 0), handler)


# -- what we read from VRChat -----------------------------------------------------

def parse_vrc_tree(tree: dict) -> tuple[Optional[str], list[tuple[str, str, Any]]]:
    """Extract (avatar_id, [(name, ptype, value)]) from VRChat's OSCQuery tree."""
    avatar = (tree.get("CONTENTS") or {}).get("avatar") or {}
    contents = avatar.get("CONTENTS") or {}

    avatar_id = None
    change = contents.get("change") or {}
    val = change.get("VALUE")
    if isinstance(val, list) and val and isinstance(val[0], str):
        avatar_id = val[0]

    params: list[tuple[str, str, Any]] = []
    root = contents.get("parameters")
    if root:
        _collect_params(root, "", params)
    return avatar_id, params


def _collect_params(node: dict, prefix: str, out: list) -> None:
    for key, child in (node.get("CONTENTS") or {}).items():
        if not isinstance(child, dict):
            continue
        name = f"{prefix}/{key}" if prefix else key
        if child.get("CONTENTS"):  # parameter names may contain '/'
            _collect_params(child, name, out)
        ptype = _TYPE_TO_PTYPE.get(child.get("TYPE"))
        if not ptype:
            continue
        value = child.get("VALUE")
        value = value[0] if isinstance(value, list) and value else None
        if value is not None:
            if ptype == "Bool":
                value = value in (True, 1, "true", "True")
            else:
                try:
                    value = round(float(value), 4) if ptype == "Float" else int(value)
                except (TypeError, ValueError):
                    value = None
        out.append((name, ptype, value))
