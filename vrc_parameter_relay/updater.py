"""Self-update for the Windows one-file exe (same design as OscGoesPurrr).

No Qt and no app imports: the UI runs check_for_update() / download_update()
on worker threads and calls apply_update() right before it quits.

The shape of an update, end to end:
  1. check_for_update() asks GitHub for the latest release, compares its tag
     with ours and picks the Windows exe attached to it.
  2. download_update() streams that exe to %TEMP%, checking the size and the
     SHA-256 digest GitHub reports before it is trusted.
  3. apply_update() writes a tiny .cmd that waits for THIS process to exit,
     moves the new exe over the running one, starts it again and deletes
     itself. The app then quits normally.

Failure model: nothing here raises into the caller. Every function returns a
plain result (None / False) so a failed update is an inconvenience, never a
crash — and never a half-replaced exe: the swap only happens after the
download has been fully verified.

Only a frozen PyInstaller --onefile build on Windows can replace itself. A
source checkout or the Linux AppImage gets is_self_updatable() == False and
the UI falls back to the release page.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from typing import Callable, Optional

LATEST_API = "https://api.github.com/repos/Blise518B/VRCParameterRelay/releases/latest"
USER_AGENT = "VRCParameterRelay-updater"

# Streamed in 256 KiB blocks: a ~60 MB exe is a few hundred reads, and the
# progress callback still feels live.
_CHUNK = 256 * 1024

# A release asset far bigger than the app can be means something is wrong
# (wrong asset, an HTML error page lying about its length). 400 MB is way
# above a PySide6 one-file build and way below a surprise worth writing to
# someone's disk.
_MAX_ASSET_BYTES = 400 * 1024 * 1024

# Open handle on the liveness lock the swap helper waits for. Module-level so
# it outlives apply_update(): the OS, not us, closes it when this process is
# finally gone — however it goes.
_lock_handle = None


# -- versions -------------------------------------------------------------------

def parse_version(text) -> Optional[tuple[int, ...]]:
    """'v1.3.2' -> (1, 3, 2). Tolerates a leading v and a suffix after the
    numbers ('1.4.0-rc1' compares as 1.4.0); junk -> None."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    text = re.split(r"[-+ ]", text, 1)[0]
    try:
        return tuple(int(part) for part in text.split(".")) if text else None
    except ValueError:
        return None


def is_newer(remote, local) -> bool:
    """Is version `remote` newer than `local`? Junk on either side -> False."""
    a, b = parse_version(remote), parse_version(local)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


# -- what can update itself -----------------------------------------------------

def is_self_updatable() -> bool:
    """True for a frozen one-file Windows exe — the only build with a single
    file we can swap. (_MEIPASS marks one-file; a one-folder build would be
    torn in half by replacing just the exe.)"""
    return (sys.platform.startswith("win")
            and bool(getattr(sys, "frozen", False))
            and hasattr(sys, "_MEIPASS")
            and os.path.isfile(sys.executable))


def current_exe_path() -> str:
    return os.path.abspath(sys.executable)


def pick_exe_asset(assets) -> Optional[dict]:
    """The Windows exe among a release's assets, as
    {"name", "url", "size", "sha256"} — sha256 is None when GitHub reported
    no digest. None when the release has no exe (yet: CI attaches it a few
    minutes after the release is created)."""
    if not isinstance(assets, (list, tuple)):
        return None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        lowered = name.lower()
        if not (lowered.endswith(".exe") and "vrcparameterrelay" in lowered):
            continue
        url = str(asset.get("browser_download_url") or "")
        if not url.lower().startswith("https://"):
            continue
        try:
            size = int(asset.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        digest = str(asset.get("digest") or "")
        sha256 = None
        if digest.lower().startswith("sha256:"):
            candidate = digest.split(":", 1)[1].strip().lower()
            if re.fullmatch(r"[0-9a-f]{64}", candidate):
                sha256 = candidate
        return {"name": name, "url": url, "size": size, "sha256": sha256}
    return None


# -- network ----------------------------------------------------------------------

def _open(url: str, timeout: float):
    """One HTTPS request (patched out by the tests)."""
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def check_for_update(current_version: str, timeout: float = 6.0) -> Optional[dict]:
    """Ask GitHub for the latest release.

    Returns {"available", "tag", "latest", "url", "asset", "assets"} —
    `latest` is the tag without its v, `url` the release page, `asset` the
    exe to install (see pick_exe_asset) or None, `assets` every download as
    {"name", "url"} (a build that can't swap itself, like the Linux
    AppImage, fetches its own file from these) — or None on ANY failure
    (offline, rate-limited, junk tag). Blocking; run it off the GUI thread.
    """
    try:
        with _open(LATEST_API, timeout) as res:
            release = json.loads(res.read().decode("utf-8"))
        if not isinstance(release, dict):
            return None
        tag = str(release.get("tag_name") or "")
        if parse_version(tag) is None or parse_version(current_version) is None:
            return None
        return {
            "available": is_newer(tag, current_version),
            "tag": tag,
            "latest": tag[1:] if tag[:1] in ("v", "V") else tag,
            "url": str(release.get("html_url") or ""),
            "asset": pick_exe_asset(release.get("assets")),
            "assets": [{"name": str(a.get("name") or ""),
                        "url": str(a.get("browser_download_url") or "")}
                       for a in (release.get("assets") or [])
                       if isinstance(a, dict) and a.get("browser_download_url")],
        }
    except Exception:
        return None  # a failed check is "no result", never an error


def download_update(asset: dict,
                    progress: Optional[Callable[[int, int], None]] = None,
                    timeout: float = 30.0) -> Optional[str]:
    """Download the release exe to a temp file and verify it.

    progress(done_bytes, total_bytes) ticks as the stream advances (total 0
    when unknown). Returns the verified file's path, or None on ANY failure
    — network, truncation, size or digest mismatch. A file that fails
    verification is deleted, never left around to be run by accident.
    """
    if not isinstance(asset, dict):
        return None
    url = str(asset.get("url") or "")
    if not url.lower().startswith("https://"):
        return None
    expected_size = int(asset.get("size") or 0)
    expected_sha = asset.get("sha256")
    if expected_size > _MAX_ASSET_BYTES:
        return None

    tmp_path = None
    try:
        with _open(url, timeout) as res:
            if getattr(res, "status", 200) != 200:
                return None
            total = expected_size or int(res.headers.get("Content-Length") or 0)
            if total > _MAX_ASSET_BYTES:
                return None
            fd, tmp_path = tempfile.mkstemp(prefix="VRCParameterRelay_update_",
                                            suffix=".exe")
            hasher = hashlib.sha256()
            done = 0
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = res.read(_CHUNK)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > _MAX_ASSET_BYTES:  # streams more than it said
                        raise ValueError("download exceeded the size ceiling")
                    hasher.update(chunk)
                    out.write(chunk)
                    if progress is not None:
                        try:
                            progress(done, total)
                        except Exception:
                            pass
        # verification — the installed exe has not been touched yet
        if done <= 0:
            raise ValueError("empty download")
        if expected_size and done != expected_size:
            raise ValueError(f"size mismatch: got {done}, want {expected_size}")
        if expected_sha and hasher.hexdigest() != expected_sha:
            raise ValueError("sha256 mismatch")
        return tmp_path
    except Exception:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return None


# -- the swap ---------------------------------------------------------------------
#
# The helper waits for this process to die with a LOCK FILE, not tasklist:
# inside a detached, console-less cmd, `tasklist | find` prints nothing and
# the loop thinks the app is already gone (seen on real Windows: two live
# instances). Windows refuses to delete a file we hold open, and the OS drops
# the handle the moment we exit, clean exit or crash. If the lock never frees
# nothing is swapped: better a skipped update than two running copies.
#
# `ping -n 2` is the ~1 s sleep (`timeout /t` fails with redirected stdin,
# which a detached process has); called by absolute path so another `ping`
# on PATH can't remove the wait. The move retries because Windows (or an
# antivirus scanning the fresh download) can hold the exe a moment longer.
# Written with CRLF and run by cmd.exe — by then our interpreter is gone.
_SWAP_CMD = r"""@echo off
setlocal
set "TARGET=%~1"
set "NEWEXE=%~2"
set "LOCK=%~3"
set "SYS=%SystemRoot%\System32"

rem ---- wait for the app to exit (~1s per try, up to ~90s)
set /a TRIES=0
:waitloop
del /q "%LOCK%" >nul 2>&1
if not exist "%LOCK%" goto swap
set /a TRIES+=1
if %TRIES% GEQ 90 goto stillrunning
"%SYS%\ping.exe" -n 2 127.0.0.1 >nul
goto waitloop

rem ---- replace the exe (~1s per try, up to ~30s)
:swap
set /a TRIES=0
:moveloop
move /y "%NEWEXE%" "%TARGET%" >nul 2>&1
if not errorlevel 1 goto relaunch
set /a TRIES+=1
if %TRIES% GEQ 30 goto failed
"%SYS%\ping.exe" -n 2 127.0.0.1 >nul
goto moveloop

:stillrunning
echo VRC Parameter Relay was still running, so the update was not installed.> "%~dp1VRCParameterRelay_update_failed.txt"
echo Quit it completely and run this file to update by hand:>> "%~dp1VRCParameterRelay_update_failed.txt"
echo %NEWEXE%>> "%~dp1VRCParameterRelay_update_failed.txt"
goto cleanup

:relaunch
rem start the app from its own folder, not from this temp one
cd /d "%~dp1"
start "" "%TARGET%"
goto cleanup

:failed
rem the note goes NEXT TO THE APP (%~dp1 = the exe's folder), where it is seen
echo Could not replace "%TARGET%".> "%~dp1VRCParameterRelay_update_failed.txt"
echo Close VRC Parameter Relay and copy this file over it by hand:>> "%~dp1VRCParameterRelay_update_failed.txt"
echo %NEWEXE%>> "%~dp1VRCParameterRelay_update_failed.txt"
cd /d "%~dp1"
start "" "%TARGET%"

:cleanup
rem leave no empty temp folder behind: step out of it, then remove it with
rem this script inside (cmd re-opens the script per line, so that's fine)
cd /d "%~dp1"
rd /s /q "%~dp0."
"""


def apply_update(downloaded_exe: str, target_exe: Optional[str] = None) -> bool:
    """Hand the swap to a detached helper and return.

    The caller must quit right afterwards: the helper waits for this process
    to exit before it touches the exe. Returns False (having changed nothing)
    if the helper could not be written or started.
    """
    global _lock_handle
    if not downloaded_exe or not os.path.isfile(downloaded_exe):
        return False
    target = os.path.abspath(target_exe or current_exe_path())
    if not os.path.isfile(target):
        return False
    script_path = None
    try:
        script_dir = tempfile.mkdtemp(prefix="VRCParameterRelay_swap_")
        script_path = os.path.join(script_dir, "apply_update.cmd")
        with open(script_path, "w", encoding="ascii", newline="\r\n") as fh:
            fh.write(_SWAP_CMD)
        lock_path = os.path.join(script_dir, "running.lock")
        _lock_handle = open(lock_path, "w", encoding="ascii")
        _lock_handle.write("VRC Parameter Relay is still running\n")
        _lock_handle.flush()

        # detached + no window: the helper must outlive us, and a console
        # flashing up during the quit would read as a crash
        flags = 0
        for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
            flags |= getattr(subprocess, name, 0)
        # The relaunched exe inherits this environment. Without the reset it
        # would look for OUR unpacked _MEI folder (gone by then) and never
        # start: swapped, but the app doesn't come back.
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
        # One string, every path quoted, the whole thing wrapped once more for
        # /s: cmd strips exactly that outer pair. (A list makes subprocess
        # quote only paths with spaces, and plain /c then strips the first
        # and LAST quote of the line — a user name with a space in %TEMP%
        # would break the call.)
        command = (f'cmd.exe /s /c ""{script_path}" "{target}" '
                   f'"{downloaded_exe}" "{lock_path}""')
        subprocess.Popen(command, creationflags=flags, close_fds=True,
                         cwd=script_dir, env=env)
        return True
    except Exception:
        if script_path:
            try:
                os.unlink(script_path)
            except OSError:
                pass
        return False
