"""Recreate docs/screenshot.png with the current UI, same demo content.

Renders the window at 2x (crisp) offscreen and composites a Windows-11-style
dark title bar on top, to match the original hero image.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

SCALE = 2  # crisp retina-style render via a painter transform

AVATAR = "avtr_00000000-0000-4000-8000-00000000b007"

from PySide6.QtCore import QPoint, QRect, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from vrc_parameter_relay import resource_path  # noqa: E402
from vrc_parameter_relay.core import AppCore  # noqa: E402
from vrc_parameter_relay.store import Store  # noqa: E402
from vrc_parameter_relay.tunnel import Tunnel  # noqa: E402
from vrc_parameter_relay.ui.main_window import MainWindow  # noqa: E402
from vrc_parameter_relay.ui.theme import build_qss, load_bundled_fonts  # noqa: E402


class StubLink:
    on_param = on_avatar = on_full_sync = on_status = None

    def send_param(self, *a):
        return True

    def refetch(self):
        pass


BOARD_PARAMS = {
    "BinaryHaloOff": ("Bool", True), "JacketOff": ("Bool", False),
    "ShoesOff": ("Bool", False), "SocksOff": ("Bool", False),
    "518H_HapticsOff": ("Bool", False), "518_bHapticVisuals": ("Int", 1),
    "CrouchIdle": ("Int", 5), "ProneIdle": ("Int", 2),
    "MaxBrightness": ("Float", 1.0), "OutlineThickness": ("Float", 0.5),
    "MinBrightness": ("Float", 0.1), "AudioLinkOff": ("Bool", False),
}
GO_PARAMS = {
    "Go/Action": ("Bool", False), "Go/Crouch": ("Bool", False),
    "Go/CrouchIdle": ("Int", 5), "Go/CrouchIdleMirror": ("Bool", False),
    "Go/Dash": ("Bool", False), "Go/DashDirection": ("Bool", False),
    "Go/DashDistance": ("Float", 0.0), "Go/Float": ("Float", 0.0),
    "Go/FloatEnd": ("Bool", False), "Go/FloatFactor": ("Float", 0.0),
    "Go/FloatSave": ("Float", 0.0), "Go/Head": ("Bool", False),
    "Go/Height": ("Float", 0.0), "Go/HipDriftX": ("Float", 0.0),
}


def build_board(core: AppCore) -> None:
    core._on_avatar_change(AVATAR)  # live avatar
    for name, (ptype, value) in {**BOARD_PARAMS, **GO_PARAMS}.items():
        core._on_osc_param(name, ptype, value)

    cats = core.board["categories"]
    layout = [("Outfit", "green"), ("Haptics", "red"),
              ("Locomotion", "yellow"), ("Shader", "blue")]
    for cat, (name, color) in zip(cats, layout):
        core.rename_category(cat["id"], name)
        core.set_category_color(cat["id"], color)
    outfit, haptics, locomotion, shader = (c["id"] for c in cats)

    for p in ("BinaryHaloOff", "JacketOff", "ShoesOff", "SocksOff"):
        core.add_control(p, "toggle", p, category=outfit)
    core.add_control("518H_HapticsOff", "toggle", "518H_HapticsOff", category=haptics)
    core.add_control("518_bHapticVisuals", "int", "518_bHapticVisuals", 0, 255, category=haptics)
    core.add_control("CrouchIdle", "int", "CrouchIdle", 0, 255, category=locomotion)
    core.add_control("ProneIdle", "int", "ProneIdle", 0, 255, category=locomotion)
    core.add_control("MaxBrightness", "slider", "MaxBrightness", 0, 1, category=shader)
    core.add_control("OutlineThickness", "slider", "OutlineThickness", 0, 1, category=shader)
    core.add_control("MinBrightness", "slider", "MinBrightness", 0, 1, category=shader)
    core.add_control("AudioLinkOff", "toggle", "AudioLinkOff", category=shader)

    core.rename_avatar("Base V7")
    core.set_yolo(True)


def title_bar(painter: QPainter, w: int, h: int) -> None:
    painter.fillRect(0, 0, w, h, QColor("#0a0c0a"))
    icon = QImage(str(resource_path("assets/icon.png")))
    if not icon.isNull():
        s = 34
        painter.drawImage(QRect(16, (h - s) // 2, s, s), icon)
    painter.setPen(QColor("#c3cdc6"))
    f = QFont("Segoe UI")
    f.setPixelSize(22)
    painter.setFont(f)
    painter.drawText(QRect(62, 0, w - 300, h), Qt.AlignVCenter | Qt.AlignLeft,
                     "VRC Parameter Relay")
    # window controls (minimize / maximize / close)
    painter.setPen(QPen(QColor("#c8ccc9"), 2))
    bw, cy = 62, h // 2
    close_x = w - bw // 2
    painter.drawLine(close_x - 9, cy - 9, close_x + 9, cy + 9)
    painter.drawLine(close_x - 9, cy + 9, close_x + 9, cy - 9)
    max_x = w - bw - bw // 2
    painter.drawRect(max_x - 8, cy - 8, 16, 16)
    min_x = w - 2 * bw - bw // 2
    painter.drawLine(min_x - 9, cy, min_x + 9, cy)


def main() -> int:
    store = Store()
    core = AppCore(store, StubLink())
    web = type("Web", (), {"port": 3080, "requested_port": 3080})()
    tunnel = Tunnel(store)
    build_board(core)

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    load_bundled_fonts()
    app.setStyleSheet(build_qss(store.settings["theme"], store.settings["font"]))
    win = MainWindow(core, tunnel, web)
    win.search.setText("go")
    core.emit({"t": "vrc_status", "vrchat_found": True, "osc_port": 52915,
               "send_target": "127.0.0.1:9000", "advertised": True})
    win.resize(1372, 602)
    win.show()

    out = Path(__file__).parent.parent / "docs" / "screenshot.png"

    def grab() -> None:
        try:
            w, h = win.width(), win.height()
            cw, ch = w * SCALE, h * SCALE
            tb = 31 * SCALE
            final = QImage(cw, ch + tb, QImage.Format_RGB32)
            final.fill(QColor("#0a0c0a"))
            p = QPainter(final)
            title_bar(p, cw, tb)
            p.translate(0, tb)
            p.scale(SCALE, SCALE)          # re-rasterise the UI at 2x = crisp
            win.render(p, QPoint(0, 0))
            p.end()
            final.save(str(out))
            print(f"saved {out}  ({final.width()}x{final.height()})  window {w}x{h}")
        finally:
            app.quit()

    QTimer.singleShot(1300, grab)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
