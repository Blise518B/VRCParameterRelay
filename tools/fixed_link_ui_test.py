"""The fixed link in the desktop UI: the Share window shows the link before
sharing starts, Start/Pause/Reset drive the fixed link (not the tunnel),
Link settings switches between fixed link and Cloudflare right away, and
What's new appears once after an update — with a working "Switch to the
fixed link" for an install that kept its tunnel — and never on a new install.

Throwaway data dirs, offscreen Qt, stub fixed link and tunnel: no network.
Exits 0 on success.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = tempfile.mkdtemp(prefix="vrcpr-fixui-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# real font metrics for the What's new fit checks (offscreen Qt has none)
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from vrc_parameter_relay import __version__  # noqa: E402
from vrc_parameter_relay.core import AppCore  # noqa: E402
from vrc_parameter_relay.store import Store  # noqa: E402

FAILS: list[str] = []


def check(desc: str, cond: bool) -> None:
    print(("OK   " if cond else "FAIL ") + desc, flush=True)
    if not cond:
        FAILS.append(desc)


class StubLink:
    on_param = on_avatar = on_full_sync = on_status = None

    def send_param(self, *a):
        return True

    def refetch(self):
        pass


class StubTunnel:
    state = "off"
    url = None

    def __init__(self):
        self.started: list = []
        self.on_change = lambda st: None

    def provider(self):
        return "cloudflare"

    def start(self, port):
        self.started.append(port)
        self.state = "starting"

    def stop(self):
        self.state = "off"

    def restart(self, port):
        self.start(port)


class StubFixed:
    """FixedLink's surface, without iroh."""

    def __init__(self, store):
        self.store = store
        self.state = "off"
        self.error = None
        self.starts = self.stops = 0
        self.on_change = lambda st: None

    def url(self):
        return f"https://pages.example/remote/#{'ab' * 32}.{self.store.settings['guest_token']}"

    def start(self):
        if self.state == "online":
            return   # like the real one: already up
        self.starts += 1
        self.state = "online"
        self.on_change({"state": "online"})

    def stop(self):
        self.stops += 1
        self.state = "off"


class StubWeb:
    port = 3080
    requested_port = 3080
    loop = None

    def rebind(self, port=None):
        pass


def window(store):
    from vrc_parameter_relay.ui.main_window import MainWindow
    core = AppCore(store, StubLink())
    tun, fixed = StubTunnel(), StubFixed(store)
    return MainWindow(core, tun, StubWeb(), fixed), core, tun, fixed


def fits(dialog) -> bool:
    """Every wrapped label on the shown page is as tall as its text needs,
    and the buttons are inside the window."""
    for label in dialog.findChildren(QLabel):
        if label.isVisible() and label.wordWrap() and \
                label.height() < label.heightForWidth(label.width()):
            return False
    return dialog.close_btn.geometry().bottom() <= dialog.height()


def main() -> int:
    qapp = QApplication.instance() or QApplication([])
    from vrc_parameter_relay.ui.dialogs import LinkSettingsDialog
    from vrc_parameter_relay.ui.whats_new import (
        RELEASES, WhatsNewDialog, feature_release, should_show, unseen,
    )

    # -- a new install ------------------------------------------------------------
    store = Store()
    win, core, tun, fixed = window(store)
    dlg = win.share_dialog
    dlg._refresh()
    check("new install: the method is the fixed link", win.share_method() == "fixed")
    check("the link shows before sharing starts", dlg.url_edit.text() == fixed.url())
    check("...and says it works once sharing starts", "start sharing" in dlg.state_label.text())
    check("the tray hands out the fixed link", win.current_share_url() == fixed.url())
    check("the one-click link is the fixed link too", dlg._oneclick_url() == fixed.url())
    check("no password row on the fixed link", dlg.pw_box.isHidden())
    check("the fixed link's window is just the link: other methods, admin panel "
          "and footnote folded away",
          dlg.more_box.isHidden() and dlg.admin_box.isHidden() and dlg.note.isHidden()
          and not dlg.own_edit.isVisibleTo(dlg) and not dlg.settings_btn.isVisibleTo(dlg))
    dlg.more_btn.click()
    check("'Other sharing methods' opens Link settings and the own address",
          not dlg.more_box.isHidden() and dlg.more_btn.text().startswith("▾"))
    dlg.more_btn.click()
    dlg.admin_btn.click()
    check("the admin panel has its own fold", not dlg.admin_box.isHidden()
          and dlg.more_box.isHidden())
    dlg.admin_btn.click()
    check("new install: no What's new", not should_show(store.settings))

    win.start_sharing()
    check("Start sharing brings the fixed link up, not the tunnel",
          fixed.starts == 1 and tun.started == [] and core.sharing_enabled)
    dlg._refresh()
    check("live state reads 'Sharing is live'", "live" in dlg.state_label.text())
    check("board button is the emergency stop", win.pause_btn.text() == "■ STOP")
    win.stop_sharing()
    dlg._refresh()
    check("pause keeps the fixed link up", fixed.stops == 0 and "Paused" in dlg.state_label.text())
    check("...and the board button offers RESUME", win.pause_btn.text() == "▶ RESUME")
    win.start_sharing()

    before = fixed.url()
    dlg._reset()
    check("Reset link: a new link", fixed.url() != before and dlg.url_edit.text() == fixed.url())
    check("Reset link never touches a tunnel", tun.started == [])

    links = LinkSettingsDialog(win)
    check("Link settings: the fixed link is picked", links.fixed_radio.isChecked())
    links.cf_radio.setChecked(True)
    links._save()
    check("switching to Cloudflare while sharing: fixed link off, tunnel on",
          store.settings["share_method"] == "tunnel" and fixed.stops == 1 and tun.started == [3080])
    dlg._refresh()
    check("the Share window follows", "trycloudflare" not in dlg.url_edit.text()
          and "Quick tunnel" in dlg.note.text())
    links = LinkSettingsDialog(win)
    check("Link settings: Cloudflare is picked now", links.cf_radio.isChecked())
    links.fixed_radio.setChecked(True)
    links._save()
    check("and back: tunnel off, fixed link on",
          store.settings["share_method"] == "fixed" and tun.state == "off" and fixed.starts == 2)
    win._quitting = True
    win.close()

    # -- an update from a version with the Cloudflare tunnel ---------------------------
    old = Path(tempfile.mkdtemp(prefix="vrcpr-fixui-old-"))
    (old / "settings.json").write_text(json.dumps({"guest_token": "cd" * 16}), "utf-8")
    os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = str(old)
    store = Store()
    win, core, tun, fixed = window(store)
    check("update: still on the tunnel", win.share_method() == "tunnel")
    check("...so 'Other sharing methods' starts open, with the footnote",
          not win.share_dialog.more_box.isHidden() and not win.share_dialog.note.isHidden())
    check("update: What's new is due", should_show(store.settings))
    win.show()
    qapp.processEvents()
    pages = unseen(store.settings)
    check("from before What's new existed: every release's page, newest first",
          [r.version for r in pages] == [r.version for r in RELEASES] and len(pages) >= 2)
    news = WhatsNewDialog(win, pages)
    news.show()
    qapp.processEvents()
    check("it opens on the newest release, with Next and no Back",
          news.index == 0 and news.next_btn.isEnabled() and not news.back_btn.isEnabled()
          and news.page_label.text() == f"1 of {len(pages)}")
    check("...and the newest page fits its window", fits(news))
    heights = [news.height()]
    for _ in pages[1:]:
        news.next_btn.click()
        qapp.processEvents()
        heights.append(news.height())
        check(f"Next: page {news.index + 1} alone is shown, and fits",
              [p.isVisible() for p in news.pages]
              == [i == news.index for i in range(len(pages))] and fits(news))
    check("each page sizes the window to itself", len(set(heights)) > 1)
    check("the last page has no Next, and Enter closes", not news.next_btn.isEnabled()
          and news.close_btn.isDefault())
    news.back_btn.click()
    check("Back goes to the newer page", news.index == len(pages) - 2)
    check("the switch to the fixed link sits on the 1.4 page",
          news.switch_box.parent() is news.pages[[r.version for r in pages].index((1, 4))])
    check("What's new offers the switch", not news.switch_box.isHidden())
    win.start_sharing()
    check("(sharing through the tunnel)", tun.started == [3080] and fixed.starts == 0)
    news.switch_btn.click()
    check("Switch: the fixed link, right away",
          store.settings["share_method"] == "fixed" and fixed.starts == 1 and tun.state == "off")
    check("...and the panel says so", "Done" in news.switch_note.text())
    win._show_whats_new = lambda: None
    store.set("last_seen_version", __version__)
    check("seen once: not again", not should_show(store.settings))
    major, minor = (int(n) for n in __version__.split(".")[:2])
    check("a bug-fix release of the same version doesn't show it again",
          not should_show({"last_seen_version": f"{major}.{minor}.99"}))
    check("...but coming from an older feature release does",
          should_show({"last_seen_version": f"{major}.{max(minor - 1, 0)}.9"})
          or minor == 0)
    check("...and an unknown last-seen version does", should_show({"last_seen_version": ""}))
    check("the current feature release has a page of its own",
          RELEASES[0].version == feature_release(__version__))
    check("pages are newest first, one per feature release",
          [r.version for r in RELEASES]
          == sorted({r.version for r in RELEASES}, reverse=True))
    if minor:
        check("from the previous feature release: only this release's page",
              [r.version for r in unseen({"last_seen_version": f"{major}.{minor - 1}.9"})]
              == [(major, minor)])
    check("a downgrade shows nothing", not should_show({"last_seen_version": "99.0.0"}))
    single = WhatsNewDialog(win, unseen({"last_seen_version": f"{major}.{minor - 1}.9"}))
    check("one page: no Back / Next", len(single.pages) == 1
          and single.back_btn.isHidden() and single.next_btn.isHidden())
    check("Help -> What's new: every release", len(WhatsNewDialog(win).pages) == len(RELEASES))

    # an update that already has an own address: no switch to offer
    store.set("share_method", "tunnel")
    store.set("public_url", "https://relay.example.com")
    check("own address: no switch offered", WhatsNewDialog(win).switch_box.isHidden())
    win._quitting = True
    win.close()

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
