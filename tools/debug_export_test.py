"""Hermetic test: ⚙ Settings → Export debug log.

No mDNS, no OSC sockets: VRChat's OSCQuery is a tiny HTTP server on
127.0.0.1 serving a canned tree, and the data folder and Downloads are
throwaway folders. Safe to run while VRChat is open. Exits 0 on success.

Covers:
  * the file has the worn avatar's parameters (app's list and VRChat's raw
    tree, with the names only one side has), board state, settings, every
    saved board, the data-folder listing and the end of the log
  * no secret survives: current tokens + share password, an old link's token
    and an old password in the log, the ngrok token; the fixed link key is
    never read
  * VRChat not connected: still exports, says why there's no tree
  * the Settings button saves into Downloads and reports the file name
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
DATA = Path(tempfile.mkdtemp(prefix="vrcpr-debug-"))
os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = str(DATA)
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from vrc_parameter_relay import debug_export  # noqa: E402
from vrc_parameter_relay.core import AppCore  # noqa: E402
from vrc_parameter_relay.store import Store  # noqa: E402
from vrc_parameter_relay.tunnel import Tunnel  # noqa: E402
from vrc_parameter_relay.ui import main_window  # noqa: E402
from vrc_parameter_relay.ui.main_window import MainWindow, SettingsDialog  # noqa: E402
from vrc_parameter_relay.ui.theme import build_qss  # noqa: E402
from vrc_parameter_relay.webserver import GuestServer  # noqa: E402

AVATAR = "avtr_deadbeef-0000-4000-8000-c0ffee000007"
OTHER = "avtr_deadbeef-0000-4000-8000-c0ffee000008"
DRESS = "VF_CL2uklsV_Clothing/Full_Suits/_Ensnared_Dress/Active"
LATCHED = "VF_eMhGNGb4_VF_CL2uklsV_Clothing/Full_Suits/_Ensnared_Dress/Active/Latched"
PARAMS = [("GestureLeft", "Int", 0), ("VelocityX", "Float", 0.0),
          (DRESS, "Bool", True), (LATCHED, "Bool", False), ("Outfit", "Int", 2)]
OLD_TOKEN = secrets.token_hex(16)          # a link from before Reset link
OLD_PASSWORD = "neon-otter-cascade-42"      # a password from before a reset
NGROK = "2abcDEFghiJKLmnoPQRstuVWXyz_1234567890"
KEY_BYTES = b"PRIVATE-KEY-" + secrets.token_bytes(20)


def check(desc: str, cond: bool, detail: str = "") -> None:
    if not cond:
        print(f"FAIL {desc}" + (f"  [{detail}]" if detail else ""))
        sys.exit(1)
    print(f"OK   {desc}")


def pump_until(pred, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def vrchat_tree(params) -> dict:
    """VRChat's OSCQuery tree: names with '/' nest, a node can be both a
    parameter and a container (…/Active and …/Active/Latched)."""
    types = {"Bool": "T", "Int": "i", "Float": "f"}
    root: dict = {"CONTENTS": {}}
    for name, ptype, value in params:
        node = root
        for part in name.split("/"):
            node = node.setdefault("CONTENTS", {}).setdefault(part, {})
        node["TYPE"] = types[ptype]
        node["VALUE"] = [value]
    return {"CONTENTS": {"avatar": {"CONTENTS": {
        "change": {"TYPE": "s", "VALUE": [AVATAR]},
        "parameters": root}}}}


class FakeVrchat(BaseHTTPRequestHandler):
    tree: dict = {}

    def do_GET(self) -> None:  # noqa: N802
        body = (json.dumps({"NAME": "VRChat-Client-TEST", "OSC_IP": "127.0.0.1",
                            "OSC_PORT": 9000})
                if "HOST_INFO" in self.path else json.dumps(self.tree)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


class StubLink:
    on_param = on_avatar = on_full_sync = on_status = None
    vrchat_http = None

    def send_param(self, name, ptype, value) -> bool:
        return True

    def refetch(self) -> None:
        pass

    def status(self) -> dict:
        return {"vrchat_found": bool(self.vrchat_http), "receiving": True}


def main() -> None:
    store = Store()
    store.set("ngrok_authtoken", NGROK)
    secrets_now = [store.settings[k] for k in ("guest_token", "admin_token", "share_password")]
    (DATA / "fixed_link_key.bin").write_bytes(KEY_BYTES)
    (DATA / debug_export.LOG_FILE).write_text(
        "2026-11-20 10:00:00 INFO    headless: SHARE LINK: https://x.trycloudflare.com"
        f"/?k={OLD_TOKEN}\n"
        f"2026-11-20 10:00:01 INFO    headless: password: {OLD_PASSWORD}\n"
        f"2026-11-24 10:00:00 INFO    core: local test link: http://127.0.0.1:3080/?k="
        f"{store.settings['guest_token']}\n"
        "2026-11-24 10:00:02 INFO    vrc_parameter_relay.core: OSCQuery sync: 5 parameters\n",
        "utf-8")

    link = StubLink()
    core = AppCore(store, link)
    # another saved avatar in the library
    other = store.load_profile(OTHER)
    other["name"] = "Second avatar"
    store.save_profile(other)

    # VRChat: the app's list has a name VRChat no longer reports, and VRChat
    # one the app hasn't picked up yet
    core._on_avatar_change(AVATAR)
    core._on_full_sync(AVATAR, PARAMS)
    core.add_control(DRESS, "toggle", "Ensnared dress")
    core.add_control("Hoodie_old", "toggle")  # a card whose parameter is gone
    core._on_osc_param("StaleInApp", "Bool", True)
    FakeVrchat.tree = vrchat_tree(PARAMS + [("NewInVrchat", "Float", 0.25)])
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeVrchat)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    link.vrchat_http = f"http://127.0.0.1:{server.server_address[1]}"

    # -- the file ----------------------------------------------------------------
    out = Path(tempfile.mkdtemp(prefix="vrcpr-downloads-"))
    path = debug_export.export(core, out / debug_export.file_name())
    text = path.read_text("utf-8")
    report = json.loads(text)
    check("file name says what it is", path.name.startswith("VRCParameterRelay-debug-")
          and path.suffix == ".json", path.name)
    check("app version in it", report["app"]["version"]
          == __import__("vrc_parameter_relay").__version__)
    params = report["parameters"]["list"]
    check("every parameter the app holds, with type and value",
          params.get(DRESS) == {"type": "Bool", "value": True}
          and params.get(LATCHED) == {"type": "Bool", "value": False}
          and params.get("Outfit") == {"type": "Int", "value": 2}
          and report["parameters"]["count"] == len(params) == 6, str(params)[:300])
    av = report["avatar"]
    check("worn and viewed avatar", av["live_avatar_id"] == AVATAR
          and av["viewed_avatar_id"] == AVATAR and av["is_live"] and av["full_list_received"])
    check("cards with a missing parameter", av["cards_with_missing_parameter"] == ["Hoodie_old"],
          str(av["cards_with_missing_parameter"]))
    tree = report["vrchat_tree"]
    check("VRChat's own list fetched raw", tree["fetched"] and tree["avatar_id"] == AVATAR
          and tree["count"] == 6 and "parameters" in tree["avatar"]["CONTENTS"])
    check("names only one side has", tree["only_in_app"] == ["StaleInApp"]
          and tree["only_in_vrchat"] == ["NewInVrchat"], f"{tree['only_in_app']} {tree['only_in_vrchat']}")
    check("a name that is both parameter and folder survives the tree",
          LATCHED in params and DRESS in params)
    check("settings.json in it", report["settings"]["content"]["web_port"] == 3080)
    check("secret settings blanked", all(
        report["settings"]["content"][k] == debug_export.HIDDEN for k in debug_export.SECRET_KEYS))
    avatars = report["avatars"]
    check("every saved board", f"{AVATAR}.json" in avatars
          and f"{OTHER}.json" in avatars, str(list(avatars)))
    check("worn avatar's board first", next(iter(avatars)) == f"{AVATAR}.json")
    worn = avatars[f"{AVATAR}.json"]
    check("its cards are in the board", any(
        c["param"] == DRESS for c in worn["presets"][0]["controls"]))
    listing = {e["path"] for e in report["data_folder"]}
    check("data folder listed", {"settings.json", "avatars/", "fixed_link_key.bin"} <= listing,
          str(sorted(listing)))
    lines = report["log"]["lines"]
    check("end of the log in it", any("OSCQuery sync: 5 parameters" in ln for ln in lines))

    # -- nothing secret --------------------------------------------------------------
    for desc, value in [("guest token", secrets_now[0]), ("admin token", secrets_now[1]),
                        ("share password", secrets_now[2]), ("ngrok token", NGROK),
                        ("old link's token", OLD_TOKEN), ("old password", OLD_PASSWORD)]:
        check(f"no {desc} anywhere", value not in text)
    check("fixed link key never read", KEY_BYTES.hex() not in text
          and "PRIVATE-KEY-" not in text)
    check("the log still reads (links kept, secret blanked)",
          any(f"?k={debug_export.HIDDEN}" in ln for ln in lines))

    # -- VRChat not connected --------------------------------------------------------
    link.vrchat_http = None
    report = debug_export.build_report(core)
    check("no VRChat: still exports, says why", not report["vrchat_tree"]["fetched"]
          and "connected" in report["vrchat_tree"]["why"])
    link.vrchat_http = f"http://127.0.0.1:{server.server_address[1]}"

    # -- the Settings button -----------------------------------------------------------
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(build_qss("neon"))
    downloads = Path(tempfile.mkdtemp(prefix="vrcpr-dl-"))
    revealed: list = []
    main_window._download_dir = lambda: downloads
    main_window._reveal_file = lambda p: revealed.append(p)
    window = MainWindow(core, Tunnel(store), GuestServer(core, 0))
    dialog = SettingsDialog(window)
    dialog.show()
    check("the button is in the Settings window",
          dialog.debug_btn.text() == "Export debug log" and dialog.debug_btn.isVisible())
    dialog.debug_btn.click()
    check("button waits while it collects", not dialog.debug_btn.isEnabled())
    pump_until(lambda: dialog.debug_btn.isEnabled(), 15)
    saved = list(downloads.glob("VRCParameterRelay-debug-*.json"))
    check("saved into Downloads", len(saved) == 1, str(saved))
    check("dialog says where it went", "Saved to Downloads" in dialog.debug_result.text()
          and dialog.debug_result.toolTip() == str(saved[0]),
          f"{dialog.debug_result.text()} / {dialog.debug_result.toolTip()}")
    check("Explorer shows it", revealed == [saved[0]], str(revealed))
    check("a valid report", json.loads(saved[0].read_text("utf-8"))["avatar"]["live_avatar_id"]
          == AVATAR)
    dialog.debug_btn.click()
    pump_until(lambda: dialog.debug_btn.isEnabled(), 15)
    check("a second export never overwrites the first",
          len(list(downloads.glob("VRCParameterRelay-debug-*.json"))) == 2)
    # the button lives in the Close row: Settings is no taller than in 1.6.0
    check("Settings window keeps its height",
          dialog.minimumSizeHint().height() < 700, str(dialog.minimumSizeHint()))

    server.shutdown()
    print("\nall debug export checks passed")


if __name__ == "__main__":
    main()
