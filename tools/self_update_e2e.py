"""End-to-end test of the self-update swap on real Windows (~1-2 minutes).

Builds two tiny PyInstaller one-file exes ("v1" and "v2") that use the real
vrc_parameter_relay.updater. v1 starts from a folder with a space in its
name, calls apply_update() with v2 as the "download" and exits a few seconds
later. The test then checks what a user would see: the helper waited for v1
to exit, v2 now sits at v1's path, v2 started by itself (proving the
PyInstaller environment reset works) from its own folder, and nothing was
left behind. %TEMP% also contains a space, to exercise the helper's quoting.

Needs PyInstaller in the venv. Touches nothing outside its temp folder.
Exits 0 on success.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PROBE = '''
import os, sys, time
from vrc_parameter_relay import updater

VERSION = "{version}"
markers = os.environ["PROBE_MARKERS"]

def mark(name, text):
    with open(os.path.join(markers, name), "w", encoding="utf-8") as fh:
        fh.write(text)

mark(f"started-{{VERSION}}.txt", "\\n".join([
    repr(time.time()), sys.executable, os.getcwd(),
    str(updater.is_self_updatable())]))
if len(sys.argv) > 2 and sys.argv[1] == "--update-to":
    ok = updater.apply_update(sys.argv[2])
    mark(f"applied-{{VERSION}}.txt", str(ok))
    time.sleep(4)  # the helper has to wait for this
    mark(f"exiting-{{VERSION}}.txt", repr(time.time()))
'''


def check(desc: str, cond: bool, detail: str = "") -> None:
    if not cond:
        print(f"FAIL {desc}" + (f"  [{detail}]" if detail else ""))
        sys.exit(1)
    print(f"OK   {desc}")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(work: Path, version: str) -> Path:
    src = work / f"probe_v{version}.py"
    src.write_text(PROBE.format(version=version), "utf-8")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--noconsole",
         "--name", f"probe{version}", "--paths", str(REPO),
         "--distpath", str(work / "dist"), "--workpath", str(work / "build"),
         "--specpath", str(work), str(src)],
        check=True, capture_output=True)
    return work / "dist" / f"probe{version}.exe"


def wait_for(path: Path, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if path.exists() and path.stat().st_size > 0:
            return True
        time.sleep(0.25)
    return False


def main() -> None:
    if not sys.platform.startswith("win"):
        print("SKIP self-update swap is Windows-only")
        return
    work = Path(tempfile.mkdtemp(prefix="vrcpr e2e "))
    try:
        print(f"building probes in {work} …")
        v1, v2 = build(work, "1"), build(work, "2")
        check("built two one-file probe exes", v1.exists() and v2.exists())

        app_dir = work / "My Apps"
        app_dir.mkdir()
        target = app_dir / "VRCParameterRelay-Windows.exe"
        shutil.copyfile(v1, target)
        download = work / "fake download.exe"
        shutil.copyfile(v2, download)
        markers = work / "markers"
        markers.mkdir()
        tmp = work / "temp dir"
        tmp.mkdir()

        env = dict(os.environ, PROBE_MARKERS=str(markers), TEMP=str(tmp), TMP=str(tmp))
        subprocess.Popen([str(target), "--update-to", str(download)], env=env,
                         cwd=str(work))

        check("v1 started and saw itself as self-updatable",
              wait_for(markers / "started-1.txt", 30)
              and (markers / "started-1.txt").read_text().splitlines()[-1] == "True")
        check("apply_update() started the helper",
              wait_for(markers / "applied-1.txt", 15)
              and (markers / "applied-1.txt").read_text() == "True")
        check("v2 started by itself after the swap", wait_for(markers / "started-2.txt", 60))

        exited = float((markers / "exiting-1.txt").read_text())
        started2, exe2, cwd2, _ = (markers / "started-2.txt").read_text().splitlines()
        check("the helper waited for v1 to exit first", float(started2) >= exited,
              f"v2 started {float(started2) - exited:+.1f}s after v1 exited")
        check("v2 now sits at v1's path", sha(target) == sha(v2))
        check("v2 runs as that file", Path(exe2).resolve() == target.resolve(), exe2)
        check("v2 started in its own folder", Path(cwd2).resolve() == app_dir.resolve(), cwd2)
        check("the download was moved, not copied", not download.exists())
        check("no failure note next to the app",
              not (app_dir / "VRCParameterRelay_update_failed.txt").exists())
        time.sleep(1.5)
        swap_dirs = list(tmp.glob("VRCParameterRelay_swap_*"))
        check("the helper removed itself and its temp folder", not swap_dirs, f"{swap_dirs}")
        print("\nself-update swap works end to end")
    finally:
        time.sleep(1)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
