"""A tunnel start that is still resolving / downloading its binary can be
cancelled: restart() and stop() in that window never leave a cloudflared (or
ngrok) behind, and only the newest start spawns.

The binary is a slow stand-in and the "tunnel" a sleeping Python process:
no download, no network. Throwaway data dir. Exits 0 on success.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = tempfile.mkdtemp(prefix="vrcpr-tuncancel-")

from vrc_parameter_relay import tunnel as T  # noqa: E402
from vrc_parameter_relay.store import Store  # noqa: E402

FAILS: list[str] = []


def check(desc: str, cond: bool) -> None:
    print(("OK   " if cond else "FAIL ") + desc, flush=True)
    if not cond:
        FAILS.append(desc)


class SlowBinary:
    """Not installed yet: 'downloads' for half a second."""

    def system_path(self):
        return None

    def own_path(self, directory):
        return Path(directory) / "cloudflared-missing.exe"

    def verified(self, directory):
        return False

    def resolve(self, directory, on_progress=None):
        time.sleep(0.5)
        return Path(sys.executable)


class StaleCopy(SlowBinary):
    """An old cloudflared in bin/ (pin bumped): it exists, so start() says
    "starting", but checking its hash takes a moment and fails."""

    def own_path(self, directory):
        return Path(sys.executable)

    def verified(self, directory):
        time.sleep(0.3)
        return False


spawned: list = []


def fake_spawn(self, exe, port):
    proc = self._popen([sys.executable, "-c", "import time; time.sleep(30)"])
    spawned.append((port, proc))


def running() -> list:
    return [port for port, proc in spawned if proc.poll() is None]


def main() -> int:
    T.Tunnel._spawn_cloudflared = fake_spawn
    tun = T.Tunnel(Store())
    tun._binary = lambda provider=None: SlowBinary()
    try:
        # a Save that starts the tunnel and moves the web port right after
        tun.start(3080)
        time.sleep(0.1)
        tun.restart(8090)
        time.sleep(1.5)
        check("restart during the download: only the new port spawns",
              [p for p, _ in spawned] == [8090])
        tun.stop()
        time.sleep(0.5)
        check("stop() leaves nothing running", running() == [])

        # stop while still downloading: nothing spawns at all
        spawned.clear()
        tun.start(3080)
        time.sleep(0.1)
        tun.stop()
        time.sleep(1.0)
        check("stop during the download: nothing spawns", spawned == [])
        check("...and the state is off", tun.state == "off")

        # Share then Pause at once, while an old copy is still being checked:
        # the state must stay "off" and the next start must work
        spawned.clear()
        tun._binary = lambda provider=None: StaleCopy()
        tun.start(3082)
        time.sleep(0.05)
        tun.stop()
        time.sleep(1.2)
        check("stop while the old copy is checked: stays off, nothing spawns",
              tun.state == "off" and spawned == [])
        tun.start(3082)
        time.sleep(1.2)
        check("...and the next start isn't blocked", [p for p, _ in spawned] == [3082])
        tun.stop()
        time.sleep(0.5)
        spawned.clear()
        tun._binary = lambda provider=None: SlowBinary()

        # an ordinary start still works
        tun.start(3081)
        time.sleep(1.2)
        check("a normal start spawns once", [p for p, _ in spawned] == [3081] and running() == [3081])
        tun.stop()
        time.sleep(0.5)
        check("...and stops", running() == [])
    finally:
        for _, proc in spawned:
            if proc.poll() is None:
                proc.kill()

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
