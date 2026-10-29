"""What's new: the panel an update shows once, on the first start of the
new version (Help → What's new opens it again).

A fresh install never sees it — store.py marks the version seen on first
run. Rewrite HEADLINE and ALSO_NEW for every release that should show it.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from .theme import accent_of

HEADLINE = ("🔗", "One fixed link",
            "Share now gives you one link that never changes — not after a "
            "restart, not tomorrow. No Cloudflare, nothing to download, no "
            "account. Friends open it in any browser and it connects straight "
            "to this app (peer-to-peer through iroh's free relays, end-to-end "
            "encrypted). <b>Reset link</b> still makes a new one whenever you "
            "want.")

ALSO_NEW = (
    ("🖥", "Tray & autostart",
     "Closing the window keeps the relay running in the tray. Start it with "
     "Windows or with SteamVR in ⚙ Settings."),
    ("🛠", "Admin panel",
     "Edit your board from a browser on a second screen or your phone — "
     "Share → Admin panel."),
    ("🔢", "Number parameters as toggles",
     "Outfit = 1, 2, 3 … as separate toggles that switch each other off, "
     "like VRChat's own menu."),
    ("🙋", "Guest names",
     "Optionally make guests pick a name before they can press anything."),
    ("🌐", "Your own address",
     "Have a domain? Share on it instead, with a password door."),
    ("⬇", "One-click updates",
     "The update prompt downloads the right file for you."),
)


def should_show(settings: dict) -> bool:
    return settings.get("last_seen_version") != __version__


class WhatsNewDialog(QDialog):
    def __init__(self, main) -> None:  # main: MainWindow
        super().__init__(main)
        self.main = main
        self.setWindowTitle(f"{APP_NAME} — What's new")
        self.setFixedWidth(560)   # the wrapped labels' height follows from it
        settings = main.core.store.settings
        accent = accent_of(settings.get("theme") or "neon")

        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        lay.setContentsMargins(22, 20, 22, 18)

        eyebrow = QLabel(f"WHAT'S NEW IN v{__version__}")
        eyebrow.setStyleSheet("color:#7f8f81; font-size:11px; letter-spacing:1.5px;")
        lay.addWidget(eyebrow)

        # The headline feature gets a card of its own: one styled label, title
        # and text together (a frame holding a layout of wrapped labels let
        # the switch row draw over the text).
        icon, title, text = HEADLINE
        card = QLabel(
            f'<div style="color:{accent}; font-size:20px; font-weight:700;">'
            f'{icon}&nbsp; {title}</div>'
            f'<div style="font-size:13px; margin-top:6px;">{text}</div>',
            objectName="WhatsNewCard")
        card.setWordWrap(True)
        card.setTextFormat(Qt.RichText)
        card.setStyleSheet(
            f"#WhatsNewCard {{ border:1px solid {accent}; border-radius:12px; "
            f"background:rgba(49,242,114,0.06); }}")
        # margins, not CSS padding: QLabel counts these when it works out how
        # tall its wrapped text is
        card.setContentsMargins(16, 14, 16, 14)
        lay.addWidget(card)

        # an update keeps the Cloudflare tunnel it had (store.py) — offer the
        # switch right here, where the news is
        self.switch_box = QWidget()
        sl = QHBoxLayout(self.switch_box)
        sl.setContentsMargins(4, 0, 0, 0)
        self.switch_note = QLabel(
            "You're still on the Cloudflare tunnel — your setting was kept.")
        self.switch_note.setWordWrap(True)
        self.switch_note.setStyleSheet("color:#facc15; font-size:12px;")
        sl.addWidget(self.switch_note, 1)
        self.switch_btn = QPushButton("Switch to the fixed link", objectName="Primary")
        self.switch_btn.clicked.connect(self._switch)
        sl.addWidget(self.switch_btn)
        self.switch_box.setVisible(main.share_method() == "tunnel")
        lay.addWidget(self.switch_box)

        also = QLabel("ALSO IN THIS UPDATE")
        also.setStyleSheet("color:#7f8f81; font-size:11px; letter-spacing:1.5px;")
        lay.addWidget(also)
        for icon, title, text in ALSO_NEW:
            row = QHBoxLayout()
            row.setSpacing(10)
            glyph = QLabel(icon)
            glyph.setFixedWidth(26)
            glyph.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
            glyph.setStyleSheet("font-size:16px;")
            row.addWidget(glyph)
            item = QLabel(f"<b>{title}</b> — {text}")
            item.setWordWrap(True)
            item.setTextFormat(Qt.RichText)
            item.setStyleSheet("font-size:12px;")
            row.addWidget(item, 1)
            lay.addLayout(row)

        close = QPushButton("Got it")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignRight)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # A window doesn't follow its layout's height-for-width by itself, and
        # came up too short for the wrapped text: size it to the text at the
        # fixed width, now that the fonts are styled.
        self.resize(self.width(), self.layout().totalHeightForWidth(self.width()))

    def _switch(self) -> None:
        self.main.core.store.set("share_method", "fixed")
        self.main.apply_link_settings()
        self.switch_btn.setVisible(False)
        self.switch_note.setText("✅ Done — Share now shows your fixed link.")
        self.switch_note.setStyleSheet("color:#31f272; font-size:12px;")
