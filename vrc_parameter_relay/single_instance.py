"""One relay per data folder.

Without this, autostart plus a double-click gives you two relays fighting
over the same avatar files, both advertising themselves to VRChat and both
sending OSC. The guard is a loopback socket: whoever binds it owns the
data folder, and a later start hands over ("activate") and exits.

The port is derived from the data folder, so a test/preview instance with
VRC_PARAMETER_RELAY_DATA_DIR set never blocks the real app.
"""
from __future__ import annotations

import hashlib
import logging
import socket
import threading
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

MAGIC = b"VRCPARAMETERRELAY/1"
_PORT_BASE, _PORT_SPAN = 39000, 4000


def port_for(data_dir: Path) -> int:
    """A stable loopback port for this data folder."""
    key = str(Path(data_dir).resolve()).lower().encode("utf-8")
    digest = hashlib.sha256(key).digest()
    return _PORT_BASE + int.from_bytes(digest[:2], "big") % _PORT_SPAN


class Guard:
    """Holds the lock. `on_activate` fires when another start is attempted."""

    def __init__(self, sock: Optional[socket.socket]) -> None:
        self._sock = sock
        self.on_activate: Optional[Callable[[], None]] = None
        if sock is not None:
            threading.Thread(target=self._serve, name="instance-guard",
                             daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return  # socket closed on shutdown
            try:
                if conn.recv(64).startswith(MAGIC):
                    conn.sendall(MAGIC + b" ok")
                    callback = self.on_activate
                    if callback:
                        log.info("another start was attempted — activating")
                        callback()
            except OSError:
                pass
            finally:
                conn.close()

    def release(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None


def _ping(port: int) -> bool:
    """True if the port is held by another instance of *this* app."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5) as conn:
            conn.sendall(MAGIC + b" activate")
            return conn.recv(64).startswith(MAGIC)
    except OSError:
        return False


def acquire(data_dir: Path) -> Optional[Guard]:
    """Become the single instance, or None if one is already running.

    Returning None means: an instance was told to show itself, so this
    process should exit quietly.
    """
    port = port_for(data_dir)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))  # no SO_REUSEADDR: bind must fail if taken
        sock.listen(4)
        return Guard(sock)
    except OSError:
        sock.close()
    if _ping(port):
        return None  # a relay already owns this data folder
    # the port belongs to something else entirely — don't hold the app hostage
    log.warning("instance-guard port %d is busy but not ours; continuing "
                "without the single-instance guard", port)
    return Guard(None)
