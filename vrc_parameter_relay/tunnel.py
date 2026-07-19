"""Tunnel manager — gives the guest web server a public https URL.

Two providers:
  * "cloudflare" (default): Cloudflare quick tunnel. Zero configuration, no
    account — but the hostname is random per tunnel process, so the link
    stays stable only while the process lives (the app keeps it alive for
    the whole session; pausing shares no longer kills it).
  * "ngrok": static domain. One-time setup (free ngrok account: authtoken +
    reserved *.ngrok-free.app domain) buys a link that never changes across
    app restarts.

Binaries are downloaded once into the data dir on first use.
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
import subprocess
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

if sys.platform == "win32":
    CLOUDFLARED_URL = (
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    )
    CLOUDFLARED_BINARY="cloudflared.exe"
    NGROK_ZIP_URL = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip"
    NGROK_BINARY="ngrok.exe"


if sys.platform == "linux":
    CLOUDFLARED_URL = (
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    )
    CLOUDFLARED_BINARY="cloudflared"
    NGROK_ZIP_URL = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.zip"
    NGROK_BINARY="ngrok"

TRYCLOUDFLARE_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class Tunnel:
    """States: off -> downloading -> starting -> online -> off / error."""

    def __init__(self, store) -> None:
        self.store = store
        self.bin_dir = store.dir / "bin"
        self.state = "off"
        self.url: Optional[str] = None
        self.error: Optional[str] = None
        self.progress: float = 0.0
        self.on_change: Callable[[dict], None] = lambda st: None
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._err_hint: Optional[str] = None

    # -- public API ------------------------------------------------------------

    def provider(self) -> str:
        return self.store.settings.get("tunnel_provider") or "cloudflare"

    def status(self) -> dict:
        return {"state": self.state, "url": self.url, "error": self.error,
                "progress": self.progress, "provider": self.provider()}

    def start(self, local_port: int) -> None:
        with self._lock:
            if self.state in ("downloading", "starting", "online"):
                return
            exe = self._exe_path(self.provider())
            self._set("downloading" if not exe.exists() else "starting")
        threading.Thread(target=self._start_impl, args=(local_port,), daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._set("off")

    def restart(self, local_port: int) -> None:
        """Used by 'Reset link' on the cloudflare provider to force a new URL."""
        self.stop()
        self.start(local_port)

    # -- internals ----------------------------------------------------------------

    def _set(self, state: str, url: Optional[str] = None, error: Optional[str] = None,
             progress: float = 0.0) -> None:
        self.state, self.url, self.error, self.progress = state, url, error, progress
        self.on_change(self.status())

    def _exe_path(self, provider: str) -> Path:
        return self.bin_dir / ("ngrok.exe" if provider == "ngrok" else "cloudflared.exe")

    def _start_impl(self, local_port: int) -> None:
        provider = self.provider()
        try:
            if provider == "ngrok":
                token = (self.store.settings.get("ngrok_authtoken") or "").strip()
                domain = _normalize_domain(self.store.settings.get("ngrok_domain") or "")
                if not token or not domain:
                    raise RuntimeError(
                        "ngrok needs an authtoken and a static domain — open Link settings.")
            exe = self._exe_path(provider)
            if not exe.exists():
                self._download(provider, exe)
            self._set("starting")
            self._err_hint = None
            if provider == "ngrok":
                self._spawn_ngrok(exe, local_port, token, domain)
            else:
                self._spawn_cloudflared(exe, local_port)
        except Exception as exc:
            log.exception("tunnel failed")
            self._set("error", error=str(exc))

    # -- downloads -------------------------------------------------------------------

    def _download(self, provider: str, exe: Path) -> None:
        exe.parent.mkdir(parents=True, exist_ok=True)
        if provider == "ngrok":
            log.info("downloading ngrok…")
            tmp = exe.with_suffix(".zip")
            self._fetch(NGROK_ZIP_URL, tmp)
            with zipfile.ZipFile(tmp) as zf:
                with zf.open("ngrok.exe") as src, open(exe, "wb") as dst:
                    dst.write(src.read())
            tmp.unlink(missing_ok=True)
        else:
            log.info("downloading cloudflared…")
            tmp = exe.with_suffix(".part")
            self._fetch(CLOUDFLARED_URL, tmp)
            tmp.replace(exe)
        log.info("%s downloaded (%.1f MB)", exe.name, exe.stat().st_size / 1e6)

    def _fetch(self, url: str, dest: Path) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": "VRCParameterRelay"})
        with urllib.request.urlopen(req, timeout=60) as res, open(dest, "wb") as fh:
            total = int(res.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = res.read(1 << 18)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if total:
                    self._set("downloading", progress=done / total)

    # -- cloudflared -----------------------------------------------------------------

    def _spawn_cloudflared(self, exe: Path, local_port: int) -> None:
        proc = self._popen(
            [str(exe), "tunnel", "--url", f"http://127.0.0.1:{local_port}", "--no-autoupdate"])
        threading.Thread(target=self._watch_cloudflared, args=(proc,), daemon=True).start()

    def _watch_cloudflared(self, proc: subprocess.Popen) -> None:
        try:
            for line in proc.stdout:
                m = TRYCLOUDFLARE_RE.search(line)
                if m and self.state != "online":
                    self._set("online", url=m.group(0))
                    log.info("tunnel online: %s", self.url)
        except Exception:
            pass
        self._on_proc_exit(proc)

    # -- ngrok ------------------------------------------------------------------------

    def _spawn_ngrok(self, exe: Path, local_port: int, token: str, domain: str) -> None:
        env = {**os.environ, "NGROK_AUTHTOKEN": token}
        proc = self._popen(
            [str(exe), "http", str(local_port), "--url", f"https://{domain}",
             "--log", "stdout", "--log-format", "json"],
            env=env)
        threading.Thread(target=self._watch_ngrok, args=(proc,), daemon=True).start()

    def _watch_ngrok(self, proc: subprocess.Popen) -> None:
        try:
            for line in proc.stdout:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if obj.get("msg") == "started tunnel" and obj.get("url"):
                    self._set("online", url=obj["url"])
                    log.info("tunnel online: %s", self.url)
                err = str(obj.get("err") or "")
                if err and err != "<nil>":
                    self._err_hint = err
        except Exception:
            pass
        self._on_proc_exit(proc)

    # -- shared plumbing -----------------------------------------------------------------

    def _popen(self, cmd: list[str], env: Optional[dict] = None) -> subprocess.Popen:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", creationflags=creationflags, env=env)
        with self._lock:
            self._proc = proc
        return proc

    def _on_proc_exit(self, proc: subprocess.Popen) -> None:
        proc.wait()
        with self._lock:
            still_ours = self._proc is proc
            if still_ours:
                self._proc = None
        if not still_ours:
            return
        hint = f": {self._err_hint}" if self._err_hint else ""
        if self.state in ("starting", "downloading"):
            self._set("error", error=f"tunnel exited before coming up{hint}")
        elif self.state == "online":
            self._set("error", error=f"tunnel disconnected{hint}")


def _normalize_domain(domain: str) -> str:
    d = domain.strip()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d.strip("/")
