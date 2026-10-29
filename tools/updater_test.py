"""Hermetic test of the self-updater — no network, nothing is replaced.

Covers updater.py (version compare, asset pick, release check, verified
download) and the update window's install / failure / busy flows with the
download and the swap stubbed out. The real swap of a running one-file exe
is exercised by tools/self_update_e2e.py (Windows, builds two tiny exes).
Exits 0 on success.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("VRC_PARAMETER_RELAY_DATA_DIR", tempfile.mkdtemp(prefix="vrcpr-updater-"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from vrc_parameter_relay import updater


def check(desc: str, cond: bool, detail: str = "") -> None:
    if not cond:
        print(f"FAIL {desc}" + (f"  [{detail}]" if detail else ""))
        sys.exit(1)
    print(f"OK   {desc}")


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, length=None, status=200, explode_after=None):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Length": str(len(body) if length is None else length)}
        self._explode_after = explode_after

    def read(self, n=-1):
        if self._explode_after is not None and self.tell() >= self._explode_after:
            raise ConnectionResetError("connection dropped")
        return super().read(n)


def serve(responses: dict):
    """Patch updater._open to answer from a {url: FakeResponse} table."""
    calls = []

    def fake_open(url, timeout):
        calls.append(url)
        resp = responses.get(url)
        if isinstance(resp, Exception):
            raise resp
        if resp is None:
            raise OSError(f"no fake for {url}")
        return resp
    updater._open = fake_open
    return calls


def leftovers() -> set:
    return {p.name for p in Path(tempfile.gettempdir()).glob("VRCParameterRelay_update_*")}


EXE = b"MZ" + os.urandom(700_000)  # stands in for the release exe
SHA = hashlib.sha256(EXE).hexdigest()
URL = "https://github.com/Blise518B/VRCParameterRelay/releases/download/v9.9.9/VRCParameterRelay-Windows.exe"
ASSETS = [
    {"name": "VRCParameterRelay-v9.9.9-x86_64.AppImage", "size": 5,
     "browser_download_url": "https://example.invalid/a.AppImage", "digest": "sha256:" + "a" * 64},
    {"name": "VRCParameterRelay-v9.9.9-x86_64.AppImage.zsync", "size": 5,
     "browser_download_url": "https://example.invalid/a.zsync"},
    {"name": "VRCParameterRelay-Windows.exe", "size": len(EXE),
     "browser_download_url": URL, "digest": "sha256:" + SHA.upper()},
]


def main() -> None:
    # -- versions ------------------------------------------------------------
    check("parse 'v1.3.2'", updater.parse_version("v1.3.2") == (1, 3, 2))
    check("parse suffix '1.4.0-rc1'", updater.parse_version("1.4.0-rc1") == (1, 4, 0))
    check("junk versions parse to None",
          updater.parse_version("nightly") is None and updater.parse_version(None) is None
          and updater.parse_version("") is None)
    check("1.3.10 is newer than 1.3.9", updater.is_newer("v1.3.10", "1.3.9"))
    check("same / older / junk are not newer",
          not updater.is_newer("v1.3.2", "1.3.2") and not updater.is_newer("v1.3.1", "1.3.2")
          and not updater.is_newer("garbage", "1.3.2"))

    # -- asset pick -------------------------------------------------------------
    asset = updater.pick_exe_asset(ASSETS)
    check("picks the Windows exe, not the AppImage / zsync",
          asset is not None and asset["name"] == "VRCParameterRelay-Windows.exe", f"{asset}")
    check("reads size and the (upper-case) sha256 digest",
          asset["size"] == len(EXE) and asset["sha256"] == SHA)
    check("plain-http asset is refused", updater.pick_exe_asset(
        [{**ASSETS[2], "browser_download_url": "http://example.invalid/x.exe"}]) is None)
    check("bad digest -> no sha (size still checked)", updater.pick_exe_asset(
        [{**ASSETS[2], "digest": "sha256:nothex"}])["sha256"] is None)
    check("someone else's exe is ignored", updater.pick_exe_asset(
        [{**ASSETS[2], "name": "Setup.exe"}]) is None)
    check("junk asset lists are safe",
          updater.pick_exe_asset(None) is None and updater.pick_exe_asset([1, "x"]) is None)

    # -- release check ------------------------------------------------------------
    def release(tag):
        return FakeResponse(json.dumps({
            "tag_name": tag, "html_url": "https://github.com/x/releases/tag/" + tag,
            "assets": ASSETS}).encode())
    serve({updater.LATEST_API: release("v9.9.9")})
    info = updater.check_for_update("1.3.2")
    check("newer release is offered with its exe",
          info and info["available"] and info["tag"] == "v9.9.9" and info["latest"] == "9.9.9"
          and info["asset"]["sha256"] == SHA, f"{info}")
    check("every download is listed for builds that can't swap themselves",
          [a["name"] for a in info["assets"]] == [a["name"] for a in ASSETS]
          and info["assets"][2]["url"] == URL)
    serve({updater.LATEST_API: release("v1.3.2")})
    check("same version is not offered", updater.check_for_update("1.3.2")["available"] is False)
    serve({updater.LATEST_API: release("nightly")})
    check("junk tag -> no result", updater.check_for_update("1.3.2") is None)
    serve({updater.LATEST_API: OSError("offline")})
    check("offline -> no result, no exception", updater.check_for_update("1.3.2") is None)
    serve({updater.LATEST_API: FakeResponse(b"<html>rate limited</html>")})
    check("non-JSON answer -> no result", updater.check_for_update("1.3.2") is None)

    # -- verified download ----------------------------------------------------------
    before = leftovers()
    ticks = []
    serve({URL: FakeResponse(EXE)})
    path = updater.download_update(asset, progress=lambda d, t: ticks.append((d, t)))
    check("good download returns a file with the exact bytes",
          path is not None and Path(path).read_bytes() == EXE)
    check("progress ticks reach the full size", ticks and ticks[-1] == (len(EXE), len(EXE)),
          f"{ticks[-1:]}")
    os.unlink(path)

    serve({URL: FakeResponse(EXE[:-1] + b"X")})
    check("tampered bytes (sha256 mismatch) are rejected", updater.download_update(asset) is None)
    serve({URL: FakeResponse(EXE[:-100])})
    check("truncated download is rejected", updater.download_update(asset) is None)
    serve({URL: FakeResponse(EXE, explode_after=300_000)})
    check("dropped connection is rejected", updater.download_update(asset) is None)
    serve({URL: FakeResponse(b"")})
    check("empty download is rejected",
          updater.download_update({**asset, "size": 0, "sha256": None}) is None)
    serve({URL: FakeResponse(b"x", length=10**12)})
    check("absurd Content-Length is refused",
          updater.download_update({**asset, "size": 0, "sha256": None}) is None)
    check("absurd declared size is refused without asking",
          updater.download_update({**asset, "size": 10**12}) is None)
    calls = serve({})
    check("non-https URL is refused without a request",
          updater.download_update({**asset, "url": "http://example.invalid/x.exe"}) is None
          and calls == [])
    check("failed downloads leave no temp files behind", leftovers() == before,
          f"{leftovers() - before}")

    # -- swap guards (the real swap: tools/self_update_e2e.py) -------------------------
    check("a source checkout can't replace itself", updater.is_self_updatable() is False)
    check("apply_update refuses a missing download",
          updater.apply_update(str(Path(tempfile.gettempdir()) / "nope.exe")) is False)

    # -- the update window ------------------------------------------------------------
    from PySide6.QtWidgets import QApplication
    from vrc_parameter_relay.core import AppCore
    from vrc_parameter_relay.store import Store
    from vrc_parameter_relay.tunnel import Tunnel
    from vrc_parameter_relay.ui import main_window
    from vrc_parameter_relay.webserver import GuestServer

    class StubLink:
        on_param = on_avatar = on_full_sync = on_status = None
        def send_param(self, *a): return True
        def refetch(self): pass

    updater.check_for_update = lambda *a, **k: None  # no launch check
    app = QApplication(sys.argv[:1])
    store = Store()
    core = AppCore(store, StubLink())
    win = main_window.MainWindow(core, Tunnel(store), GuestServer(core, 0))

    def pump(until, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end and not until():
            app.processEvents()
            time.sleep(0.01)
        return until()

    rel = {"available": True, "tag": "v9.9.9", "latest": "9.9.9",
           "url": "https://github.com/x/releases/tag/v9.9.9", "asset": asset}

    # a build that can't swap itself, with no download of its own: release page
    dlg = main_window.UpdateDialog(win, rel)
    check("source build: no Install button, release page is primary",
          dlg.install_btn is None and dlg.page_btn.objectName() == "Primary")
    dlg.dont_remind.setChecked(True)
    dlg.later_btn.click()
    check("'don't remind me' + Later stores the skip", store.settings["skip_update_version"] == "v9.9.9")

    # ...with a download that fits (the AppImage on Linux): saved to Downloads
    saved, revealed = [], []
    main_window._save_download = lambda a, progress=None: (
        progress(5, 10), saved.append(a["name"]), str(Path(tempfile.gettempdir()) / a["name"]))[-1]
    main_window._reveal_file = lambda path: revealed.append(path)
    listed = [{"name": "VRCParameterRelay-Windows.exe", "url": URL},
              {"name": "VRCParameterRelay-v9.9.9-x86_64.AppImage", "url": "https://example.invalid/a"}]
    dlg = main_window.UpdateDialog(win, {**rel, "assets": listed})
    dlg.show()
    check("no self-update: the platform download is offered instead",
          dlg.install_btn is None and dlg.download_btn is not None)
    dlg.download_btn.click()
    pump(lambda: not dlg.isVisible())
    check("...saved to Downloads and shown, window closed",
          len(saved) == 1 and len(revealed) == 1 and not dlg.isVisible(), f"{saved} {revealed}")
    main_window._save_download = lambda a, progress=None: None
    dlg = main_window.UpdateDialog(win, {**rel, "assets": listed})
    dlg.show()
    dlg.download_btn.click()
    pump(lambda: dlg.download_btn.isEnabled())
    check("a failed download says so and offers Try again",
          dlg.download_btn.text() == "Try again" and "nothing was saved" in dlg.status.text())
    dlg.later_btn.click()

    # pretend to be the frozen exe from here on
    updater.is_self_updatable = lambda: True
    fd, fake_name = tempfile.mkstemp(suffix=".exe")
    os.close(fd)
    fake_new = Path(fake_name)
    applied, quits = [], []
    gate = threading.Event()

    def fake_download(a, progress=None, timeout=30.0):
        progress(1, 2)
        gate.wait(5)
        progress(2, 2)
        return str(fake_new)
    updater.download_update = fake_download
    updater.apply_update = lambda path, target=None: applied.append(path) or True
    main_window.QApplication.quit = staticmethod(lambda: quits.append(True))

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    started = []
    real_fake = fake_download
    updater.download_update = lambda *a, **k: started.append(1) or None
    dlg = main_window.UpdateDialog(win, rel)
    dlg.show()
    dlg.activateWindow()
    QTest.keyClick(dlg, Qt.Key_Return)
    app.processEvents()
    check("a stray Enter means Later — no download, window closed",
          not started and not dlg.isVisible(), f"started={started}")
    updater.download_update = real_fake

    dlg = main_window.UpdateDialog(win, rel)
    dlg.show()
    check("frozen build: Install now is offered", dlg.install_btn is not None
          and dlg.install_btn.objectName() == "Primary")
    dlg.install_btn.click()
    pump(lambda: "50%" in dlg.status.text())
    check("progress shows while downloading", "50%" in dlg.status.text(), dlg.status.text())
    dlg.reject()
    app.processEvents()
    check("the window can't be closed mid-install", dlg.isVisible()
          and not dlg.later_btn.isEnabled())
    gate.set()
    pump(lambda: bool(quits))
    check("verified download is handed to the swap, then the app quits",
          applied == [str(fake_new)] and quits == [True], f"applied={applied} quits={quits}")
    check("the window closed for the restart", not dlg.isVisible())

    # a failed download changes nothing and lets the user retry or leave
    updater.download_update = lambda a, progress=None, timeout=30.0: None
    applied.clear()
    quits.clear()
    dlg = main_window.UpdateDialog(win, rel)
    dlg.show()
    dlg.install_btn.click()
    pump(lambda: dlg.install_btn.isEnabled())
    check("failed download: nothing applied, no quit, Try again offered",
          not applied and not quits and dlg.install_btn.text() == "Try again"
          and "nothing was changed" in dlg.status.text())
    dlg.later_btn.click()
    check("…and the window closes normally afterwards", not dlg.isVisible())

    # the swap helper refusing to start also changes nothing
    updater.download_update = lambda a, progress=None, timeout=30.0: str(fake_new)
    updater.apply_update = lambda path, target=None: False
    dlg = main_window.UpdateDialog(win, rel)
    dlg.show()
    dlg.install_btn.click()
    pump(lambda: dlg.install_btn.isEnabled())
    check("helper won't start: no quit, the user is told where the file is",
          not quits and str(fake_new) in dlg.status.text())
    dlg.later_btn.click()
    fake_new.unlink(missing_ok=True)

    print("\nall updater checks passed")


if __name__ == "__main__":
    main()
