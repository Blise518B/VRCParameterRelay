"""What's new: the panel an update shows once, on the first start of the
new version (Help → What's new opens it again).

A fresh install never sees it — store.py marks the version seen on first
run. It shows once per feature release (1.4, 1.5 …). A bug-fix release
(1.4.1) has nothing new to show, so it doesn't open the panel again for
someone who saw 1.4's.

Every feature release keeps its own page in RELEASES, newest first. After
an update the panel pages through each release newer than
last_seen_version (Back / Next), so jumping from 1.3 to 1.5 shows 1.5's
news and then 1.4's. Help → What's new pages through all of them. Add a
page for each new feature release; never overwrite an older one.
"""
from __future__ import annotations

from typing import NamedTuple, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from ..updater import parse_version
from .theme import accent_of


class Release(NamedTuple):
    version: tuple[int, int]
    headline: tuple[str, str, str]        # icon, title, text (rich text)
    also: tuple[tuple[str, str, str], ...]
    #: offer the switch from the Cloudflare tunnel to the fixed link (1.4)
    fixed_link_switch: bool = False


RELEASES = (
    Release(
        (1, 5),
        ("📱", "VRChat on a Quest or phone",
         "Playing on a Quest standalone or an Android phone? Turn on "
         "<b>VRChat on another device</b> (Link settings → Network &amp; "
         "ports) and the relay finds VRChat there over your Wi-Fi. The PC or "
         "server running the relay just has to be on the same network."),
        (
            ("⚙", "Settings in the admin panel",
             "How friends reach your board, the password, guest names and "
             "ports, from any browser. Headless mode finally has a settings "
             "screen, and changes apply right away."),
            ("🔌", "Fixed ports",
             "Pick the OSC, OSCQuery and web server ports yourself, so a "
             "firewall rule (ufw, firewalld, Windows) keeps working."),
            ("🏠", "Admin panel on your network",
             "Open the admin panel from another computer or your phone at "
             "home, without sharing it to the internet."),
            ("📖", "Every release you skipped",
             "What's new now pages through each update you missed, not just "
             "the newest."),
        ),
    ),
    Release(
        (1, 4),
        ("🔗", "One fixed link",
         "Share now gives you one link that never changes — not after a "
         "restart, not tomorrow. No Cloudflare, nothing to download, no "
         "account. Friends open it in any browser and it connects straight "
         "to this app (peer-to-peer through iroh's free relays, end-to-end "
         "encrypted). <b>Reset link</b> still makes a new one whenever you "
         "want."),
        (
            ("🖥", "Tray &amp; autostart",
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
        ),
        fixed_link_switch=True,
    ),
)


def feature_release(version) -> Optional[tuple[int, ...]]:
    """'1.4.1' -> (1, 4): the release whose news the panel carries."""
    parsed = parse_version(version)
    return parsed[:2] if parsed else None


def releases_up_to_now() -> list[Release]:
    """Every page this version knows, newest first (Help → What's new)."""
    current = feature_release(__version__)
    return [r for r in RELEASES if current is None or r.version <= current]


def unseen(settings: dict) -> list[Release]:
    """The pages an update owes: each feature release newer than the one
    last seen. An unknown last-seen version is an update from before 1.4,
    which had no What's new: every page."""
    seen = feature_release(settings.get("last_seen_version"))
    return [r for r in releases_up_to_now() if seen is None or r.version > seen]


def should_show(settings: dict) -> bool:
    return bool(unseen(settings))


def _caption(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("color:#7f8f81; font-size:11px; letter-spacing:1.5px;")
    return label


class WhatsNewDialog(QDialog):
    def __init__(self, main, releases: Optional[list[Release]] = None) -> None:
        super().__init__(main)  # main: MainWindow
        self.main = main
        self.setWindowTitle(f"{APP_NAME} — What's new")
        self.setFixedWidth(560)   # the wrapped labels' height follows from it
        settings = main.core.store.settings
        self.accent = accent_of(settings.get("theme") or "neon")
        self.releases = list(releases) if releases else releases_up_to_now()

        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        lay.setContentsMargins(22, 20, 22, 18)

        # one page per release; only the current one is shown, so the
        # layout (and the window's height) follows that page alone
        self.pages: list[QWidget] = []
        self.switch_box: Optional[QWidget] = None
        for release in self.releases:
            page = self._page(release)
            lay.addWidget(page)
            self.pages.append(page)

        nav = QHBoxLayout()
        self.back_btn = QPushButton("‹ Back")
        self.back_btn.clicked.connect(lambda: self.show_page(self.index - 1))
        nav.addWidget(self.back_btn)
        self.page_label = QLabel()
        self.page_label.setStyleSheet("color:#7f8f81; font-size:12px;")
        nav.addWidget(self.page_label)
        self.next_btn = QPushButton("Next ›")
        self.next_btn.clicked.connect(lambda: self.show_page(self.index + 1))
        nav.addWidget(self.next_btn)
        nav.addStretch(1)
        self.close_btn = QPushButton("Got it")
        self.close_btn.clicked.connect(self.accept)
        nav.addWidget(self.close_btn)
        lay.addLayout(nav)
        for btn in (self.back_btn, self.next_btn):
            # the theme draws a disabled button like an enabled one: the one
            # that can't be used is hidden instead, keeping its place
            policy = btn.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            btn.setSizePolicy(policy)
        self.paged = len(self.pages) > 1
        self.page_label.setVisible(self.paged)

        # without a switch on its page, keep the attribute for callers/tests
        if self.switch_box is None:
            self.switch_box = QWidget()
            self.switch_box.setVisible(False)
        self.index = 0
        self.show_page(0)

    def _page(self, release: Release) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(14)
        lay.setContentsMargins(0, 0, 0, 0)
        shown = ".".join(str(n) for n in release.version)
        lay.addWidget(_caption(f"WHAT'S NEW IN v{shown}"))

        # The headline feature gets a card of its own: one styled label, title
        # and text together (a frame holding a layout of wrapped labels let
        # the switch row draw over the text).
        icon, title, text = release.headline
        card = QLabel(
            f'<div style="color:{self.accent}; font-size:20px; font-weight:700;">'
            f'{icon}&nbsp; {title}</div>'
            f'<div style="font-size:13px; margin-top:6px;">{text}</div>',
            objectName="WhatsNewCard")
        card.setWordWrap(True)
        card.setTextFormat(Qt.RichText)
        card.setStyleSheet(
            f"#WhatsNewCard {{ border:1px solid {self.accent}; border-radius:12px; "
            f"background:rgba(49,242,114,0.06); }}")
        # margins, not CSS padding: QLabel counts these when it works out how
        # tall its wrapped text is
        card.setContentsMargins(16, 14, 16, 14)
        lay.addWidget(card)

        if release.fixed_link_switch:
            # an update keeps the Cloudflare tunnel it had (store.py) — offer
            # the switch right here, where the news is
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
            self.switch_box.setVisible(self.main.share_method() == "tunnel")
            lay.addWidget(self.switch_box)

        lay.addWidget(_caption(f"ALSO NEW IN v{shown}"))
        for icon, title, text in release.also:
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
        return page

    def show_page(self, index: int) -> None:
        self.index = max(0, min(index, len(self.pages) - 1))
        for i, page in enumerate(self.pages):
            page.setVisible(i == self.index)
        last = self.index == len(self.pages) - 1
        self.back_btn.setEnabled(self.index > 0)
        self.next_btn.setEnabled(not last)
        self.back_btn.setVisible(self.paged and self.index > 0)
        self.next_btn.setVisible(self.paged and not last)
        self.page_label.setText(f"{self.index + 1} of {len(self.pages)}")
        # Enter pages on, then closes
        self.next_btn.setDefault(not last)
        self.close_btn.setDefault(last)
        if self.isVisible():
            self._fit()

    def _fit(self) -> None:
        # A window doesn't follow its layout's height-for-width by itself, and
        # came up too short for the wrapped text: size it to the shown page's
        # text at the fixed width, now that the fonts are styled.
        height = self.layout().totalHeightForWidth(self.width())
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        self.resize(self.width(), height)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit()

    def _switch(self) -> None:
        self.main.core.store.set("share_method", "fixed")
        self.main.apply_link_settings()
        self.switch_btn.setVisible(False)
        self.switch_note.setText("✅ Done — Share now shows your fixed link.")
        self.switch_note.setStyleSheet("color:#31f272; font-size:12px;")
        self._fit()
