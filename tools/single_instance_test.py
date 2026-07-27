"""Hermetic test of the single-instance guard.

Verifies that a second start is refused and activates the first, that
different data folders stay independent (so tests/previews never block the
real app), and that releasing frees the lock again.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vrc_parameter_relay.single_instance import MAGIC, Guard, acquire, port_for


def check(desc: str, cond: bool) -> None:
    if not cond:
        print(f"FAIL {desc}")
        sys.exit(1)
    print(f"OK   {desc}")


def main() -> None:
    here = Path(__file__).parent.parent
    dir_a = here / ".si-test-a"
    dir_b = here / ".si-test-b"

    # ports are stable per folder and differ between folders
    check("port is stable for a folder", port_for(dir_a) == port_for(dir_a))
    check("different folders get different ports",
          port_for(dir_a) != port_for(dir_b))
    check("port is in the expected range", 39000 <= port_for(dir_a) < 43000)
    check("path case/format does not matter",
          port_for(dir_a) == port_for(Path(str(dir_a).upper())))

    # first start wins
    first = acquire(dir_a)
    check("first instance acquires the lock", isinstance(first, Guard))

    activated = threading.Event()
    first.on_activate = activated.set

    # second start is refused and wakes the first
    second = acquire(dir_a)
    check("second instance is refused", second is None)
    check("first instance was told to show itself", activated.wait(3))

    # a different data folder is unaffected (preview/test isolation)
    other = acquire(dir_b)
    check("another data folder still starts", isinstance(other, Guard))
    other.release()

    # releasing frees the lock for a genuine restart
    first.release()
    time.sleep(0.2)
    restarted = acquire(dir_a)
    check("lock is free after release", isinstance(restarted, Guard))
    restarted.release()

    # a stranger on the port must not lock the app out (fail open)
    port = port_for(dir_a)
    stranger = socket.socket()
    stranger.bind(("127.0.0.1", port))
    stranger.listen(1)
    try:
        guard = acquire(dir_a)
        check("foreign port holder does not block startup", isinstance(guard, Guard))
        guard.release()
    finally:
        stranger.close()

    print("ALL SINGLE-INSTANCE TESTS PASSED")


if __name__ == "__main__":
    main()
