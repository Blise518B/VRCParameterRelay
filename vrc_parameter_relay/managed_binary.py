"""Third-party executables the app downloads, verifies and runs.

cloudflared and ngrok both arrive the same way: fetch a pinned build,
check it against a known digest, then execute it. Because the app *runs* these
binaries, the verification here is the only thing between a bad day at GitHub
(or a hostile file in the working directory) and code running as the user — so
it lives in one place rather than being reimplemented per provider.

Pins are updated with tools/pin_binaries.py.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)


class Binary:
    """A pinned third-party executable.

    `sha256` is None for a binary that cannot be pinned — ngrok publishes only
    a moving "v3-stable" URL, whose content changes without the URL changing.
    `member` names the file to pull out when the download is an archive.
    """

    def __init__(self, name: str, exe_name: str, url: str,
                 sha256: Optional[str] = None, member: Optional[str] = None) -> None:
        self.name = name          # PATH lookup name, e.g. "cloudflared"
        self.exe_name = exe_name  # filename in our bin dir, e.g. "cloudflared.exe"
        self.url = url
        self.sha256 = sha256
        self.member = member

    # -- locating ------------------------------------------------------------

    def system_path(self) -> Optional[Path]:
        """An already-installed copy on PATH, if there is one.

        Distro packages and winget/scoop installs are just as good as our own
        copy — using them skips a large download and keeps the tool updated by
        whatever installed it.

        A hit inside the working directory is refused. On Windows
        shutil.which() searches the CWD *before* PATH, and this app is normally
        run straight out of the Downloads folder — exactly where a hostile
        binary of the same name would land. Falling through to our own pinned
        copy is the safe way to lose that race.
        """
        found = shutil.which(self.name)
        if not found:
            return None
        exe = Path(found).resolve()
        try:
            if exe.parent == Path.cwd().resolve():
                log.warning("ignoring the %s in the working directory (%s) — "
                            "using our own copy instead", self.name, exe)
                return None
        except OSError:  # cwd deleted out from under us
            return None
        return exe

    def own_path(self, bin_dir: Path) -> Path:
        return bin_dir / self.exe_name

    def verified(self, bin_dir: Path) -> bool:
        """Is our own copy the exact build we pinned?

        Also covers the upgrade path: a copy fetched by an older release — back
        when the download was unverified, or simply a version ago — is replaced
        rather than trusted.
        """
        exe = self.own_path(bin_dir)
        if not exe.exists():
            return False
        if not self.sha256:
            return True  # nothing to pin against — keep what we have
        if hmac.compare_digest(sha256_file(exe), self.sha256):
            return True
        log.warning("%s in the data dir is not the pinned build — replacing it",
                    exe.name)
        return False

    # -- fetching ------------------------------------------------------------

    def download(self, bin_dir: Path,
                 on_progress: Optional[Callable[[float], None]] = None) -> Path:
        """Fetch, verify, and install. Never leaves an unverified file behind."""
        exe = self.own_path(bin_dir)
        exe.parent.mkdir(parents=True, exist_ok=True)
        tmp = exe.with_suffix(".part")
        log.info("downloading %s…", self.name)

        req = urllib.request.Request(self.url, headers={"User-Agent": "VRCParameterRelay"})
        with urllib.request.urlopen(req, timeout=60) as res, open(tmp, "wb") as fh:
            total = int(res.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = res.read(1 << 18)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if total and on_progress:
                    on_progress(done / total)

        # verify what we fetched, before anything is unpacked or renamed into
        # the path we will later execute
        check_digest(tmp, self.sha256)

        if self.member:
            # unpack beside the target first: a failure part-way through must
            # not clobber a working copy from a previous run
            staged = exe.with_suffix(".new")
            _extract(tmp, self.member, staged)
            staged.replace(exe)
            tmp.unlink(missing_ok=True)
        else:
            tmp.replace(exe)
        make_executable(exe)
        log.info("%s installed (%.1f MB)", exe.name, exe.stat().st_size / 1e6)
        return exe

    def resolve(self, bin_dir: Path,
                on_progress: Optional[Callable[[float], None]] = None) -> Path:
        """The executable to run: an existing install, or our verified copy."""
        found = self.system_path()
        if found is not None:
            log.info("using the %s already on PATH: %s", self.name, found)
            return found
        if not self.verified(bin_dir):
            return self.download(bin_dir, on_progress)
        exe = self.own_path(bin_dir)
        make_executable(exe)
        return exe


# -- helpers ------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_digest(path: Path, expected: Optional[str]) -> None:
    """Refuse a download that isn't what we pinned, and don't leave it lying
    around under a name we might later execute."""
    if not expected:
        return
    got = sha256_file(path)
    if hmac.compare_digest(got, expected):
        return
    path.unlink(missing_ok=True)
    raise RuntimeError(
        f"{path.name} does not match the pinned checksum — refusing to run it "
        f"(expected {expected[:16]}…, got {got[:16]}…)")


def make_executable(path: Path) -> None:
    if sys.platform != "win32":  # our own copy needs the exec bit
        path.chmod(os.stat(path).st_mode | stat.S_IEXEC)


def _extract(archive: Path, member: str, dest: Path) -> None:
    """Pull one file out of a release archive.

    Detected by content, not by name: the download always lands as a ".part"
    file regardless of whether upstream ships a .zip or a .tar.gz.
    """
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            with zf.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
        return
    with tarfile.open(archive, "r:*") as tf:
        src = tf.extractfile(member)
        if src is None:
            raise RuntimeError(f"{member} missing from {archive.name}")
        with src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)


def popen(cmd: list[str], env: Optional[dict] = None) -> subprocess.Popen:
    """Run one of these binaries with its output captured and no console window."""
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", creationflags=creationflags, env=env)
