"""Dialogs: share/link settings and the add/edit-control form."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..webserver import compose_public_url, lan_ip, normalize_public_url

KIND_CHOICES = {
    "Bool": [("Toggle switch", "toggle"), ("Hold button", "button")],
    "Int": [("Number stepper", "int"), ("Slider", "slider"),
            ("Toggle switch", "toggle"), ("Hold button", "button")],
    "Float": [("Slider", "slider")],
}

# Int toggles/buttons send a value instead of true/false; the labels say
# which moment each value belongs to.
_SWITCH_VALUE_LABELS = {
    "toggle": ("Value when ON", "Value when OFF"),
    "button": ("Value while held", "Value on release"),
}


class ControlDialog(QDialog):
    """Add or edit a control for a parameter."""

    def __init__(self, parent: QWidget, param: str, ptype: str,
                 existing: Optional[dict] = None,
                 categories: Optional[list[dict]] = None,
                 suggested_on: int = 1) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit control" if existing else "Add control")
        self.setMinimumWidth(340)
        form = QFormLayout(self)
        form.setSpacing(10)

        form.addRow(QLabel(f"<b>{param}</b> <span style='color:#8b8b9e'>({ptype})</span>"))

        self.label_edit = QLineEdit((existing or {}).get("label") or param.split("/")[-1])
        form.addRow("Label", self.label_edit)

        self.kind_combo = QComboBox()
        for text, kind in KIND_CHOICES.get(ptype, [("Slider", "slider")]):
            self.kind_combo.addItem(text, kind)
        if existing:
            idx = self.kind_combo.findData(existing.get("kind"))
            if idx >= 0:
                self.kind_combo.setCurrentIndex(idx)
        form.addRow("Control", self.kind_combo)

        self.cat_combo: Optional[QComboBox] = None
        if categories and not existing:  # existing cards move via drag & drop
            self.cat_combo = QComboBox()
            for cat in categories:
                self.cat_combo.addItem(cat.get("name") or "Category", cat["id"])
            form.addRow("Category", self.cat_combo)

        self.invert_check: Optional[QCheckBox] = None
        if ptype == "Bool":
            self.invert_check = QCheckBox("Invert — ON sends the parameter OFF (and vice versa)")
            self.invert_check.setChecked(bool((existing or {}).get("invert")))
            form.addRow("", self.invert_check)

        self.min_spin = QDoubleSpinBox(decimals=2, minimum=-9999, maximum=9999)
        self.max_spin = QDoubleSpinBox(decimals=2, minimum=-9999, maximum=9999)
        self.min_spin.setValue(float((existing or {}).get("min", 0)))
        self.max_spin.setValue(float((existing or {}).get("max", 1 if ptype == "Float" else 255)))
        form.addRow("Min", self.min_spin)
        form.addRow("Max", self.max_spin)

        self._ptype = ptype
        self._form = form
        self.on_spin = QSpinBox(minimum=0, maximum=255)
        self.off_spin = QSpinBox(minimum=0, maximum=255)
        self.on_spin.setValue(int((existing or {}).get("on", suggested_on)))
        self.off_spin.setValue(int((existing or {}).get("off", 0)))
        self.on_spin.setToolTip(f"What {param} is set to when the control is on.\n"
                                "Several toggles on one number work like VRChat's "
                                "menu: Outfit = 1, Outfit = 2, …")
        self.off_spin.setToolTip(f"What {param} goes back to (usually 0).")
        form.addRow(_SWITCH_VALUE_LABELS["toggle"][0], self.on_spin)
        form.addRow(_SWITCH_VALUE_LABELS["toggle"][1], self.off_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self.kind_combo.currentIndexChanged.connect(self._update_range_enabled)
        self._update_range_enabled()

    def _int_switch(self) -> bool:
        return self._ptype == "Int" and self.kind_combo.currentData() in ("toggle", "button")

    def _update_range_enabled(self) -> None:
        kind = self.kind_combo.currentData()
        ranged = kind in ("slider", "int")
        self.min_spin.setEnabled(ranged)
        self.max_spin.setEnabled(ranged)
        switch = self._int_switch()
        self._form.setRowVisible(self.on_spin, switch)
        self._form.setRowVisible(self.off_spin, switch)
        if switch:
            on_text, off_text = _SWITCH_VALUE_LABELS[kind]
            self._form.labelForField(self.on_spin).setText(on_text)
            self._form.labelForField(self.off_spin).setText(off_text)
        self.adjustSize()

    def result_dict(self) -> dict:
        kind = self.kind_combo.currentData()
        out = {"label": self.label_edit.text().strip(), "kind": kind}
        if kind in ("slider", "int"):
            out["min"] = self.min_spin.value()
            out["max"] = self.max_spin.value()
        if self._int_switch():
            out["on"] = self.on_spin.value()
            out["off"] = self.off_spin.value()
        if self.cat_combo is not None:
            out["category"] = self.cat_combo.currentData()
        if self.invert_check is not None:
            out["invert"] = self.invert_check.isChecked()
        return out


class _EnterLineEdit(QLineEdit):
    """Enter applies the field — without also pressing a dialog button."""

    def keyPressEvent(self, event) -> None:
        super().keyPressEvent(event)
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            event.accept()


class ShareDialog(QDialog):
    def __init__(self, main) -> None:  # main: MainWindow
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Share remote control")
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        self.intro = QLabel()
        self.intro.setWordWrap(True)
        self.intro.setStyleSheet("color:#94a698;")
        lay.addWidget(self.intro)

        # Your own domain instead of a tunnel: the one field it takes. Whatever
        # routes the address to this PC is the user's business (port forward,
        # reverse proxy, Cloudflare Tunnel) — see docs/REMOTE-ACCESS.md.
        own = QVBoxLayout()
        own.setSpacing(3)
        own_row = QHBoxLayout()
        own_row.addWidget(QLabel("Your own address"))
        self.own_edit = _EnterLineEdit(main.own_address())
        self.own_edit.setPlaceholderText("optional — e.g. https://relay.yourdomain.com")
        self.own_edit.setClearButtonEnabled(True)
        self.own_edit.editingFinished.connect(self._apply_own_address)
        own_row.addWidget(self.own_edit, 1)
        own.addLayout(own_row)
        self.own_hint = QLabel()
        self.own_hint.setWordWrap(True)
        self.own_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        own.addWidget(self.own_hint)
        lay.addLayout(own)

        self.state_label = QLabel("Sharing is off.")
        lay.addWidget(self.state_label)

        self.progress = QProgressBar(maximum=100)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        row = QHBoxLayout()
        self.url_edit = QLineEdit(readOnly=True, placeholderText="Share link appears here")
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.clicked.connect(self._copy)
        row.addWidget(self.url_edit, 1)
        row.addWidget(self.copy_btn)
        self.oneclick_btn = QPushButton("Copy one-click link")
        self.oneclick_btn.setToolTip(
            "Carries the token, so it skips the password. The app strips it "
            "from the address bar on arrival — but it is a live secret while "
            "it sits in a chat.")
        self.oneclick_btn.clicked.connect(self._copy_oneclick)
        row.addWidget(self.oneclick_btn)
        lay.addLayout(row)

        # Own address: it carries no secret — guests type this once and their
        # browser stays signed in (docs/REMOTE-ACCESS.md).
        self.pw_box = QWidget()
        pw_row = QHBoxLayout(self.pw_box)
        pw_row.setContentsMargins(0, 0, 0, 0)
        pw_row.addWidget(QLabel("Password"))
        self.pw_label = QLabel()
        self.pw_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.pw_label.setStyleSheet("font-weight:700; letter-spacing:0.5px;")
        pw_row.addWidget(self.pw_label, 1)
        pw_copy = QPushButton("Copy")
        pw_copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(
            self.main.core.share_password()))
        pw_row.addWidget(pw_copy)
        pw_new = QPushButton("New password")
        pw_new.setToolTip("Future sign-ins need the new one; browsers already "
                          "signed in stay in — “Reset link” is what kicks them.")
        pw_new.clicked.connect(self._rotate_password)
        pw_row.addWidget(pw_new)
        lay.addWidget(self.pw_box)

        self.autostart = QCheckBox("Resume sharing automatically when the app starts")
        self.autostart.setChecked(bool(main.core.store.settings.get("share_autostart")))
        self.autostart.toggled.connect(
            lambda on: main.core.store.set("share_autostart", bool(on)))
        lay.addWidget(self.autostart)

        self.require_names = QCheckBox("Guests must enter a name before they can use the board")
        self.require_names.setToolTip(
            "Nameless guests see a name prompt and their inputs are ignored\n"
            "until they introduce themselves. Applies to guests already connected.")
        self.require_names.setChecked(bool(main.core.store.settings.get("require_guest_names")))
        self.require_names.toggled.connect(
            lambda on: main.core.set_require_names(bool(on)))
        lay.addWidget(self.require_names)

        btns = QHBoxLayout()
        self.start_btn = QPushButton("Start sharing", objectName="Primary")
        self.start_btn.clicked.connect(self._toggle)
        self.reset_btn = QPushButton("Reset link", objectName="Danger")
        self.reset_btn.setToolTip(
            "Invalidates every link you've handed out and kicks all guests.\n"
            "Cloudflare: also picks a new random URL. ngrok and your own "
            "address: the address stays, the secret changes.")
        self.reset_btn.clicked.connect(self._reset)
        self.settings_btn = QPushButton("Link settings…")
        self.settings_btn.clicked.connect(self._open_settings)
        btns.addWidget(self.start_btn)
        btns.addWidget(self.reset_btn)
        btns.addWidget(self.settings_btn)
        btns.addStretch(1)
        self.guests_label = QLabel("")
        btns.addWidget(self.guests_label)
        lay.addLayout(btns)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#5f6f63; font-size:11px;")
        lay.addWidget(self.note)

        self.local_label = QLineEdit(readOnly=True)
        self.local_label.setStyleSheet("color:#5f6f63; font-size:11px;")
        lay.addWidget(self.local_label)

        # -- admin panel (edit the board from a browser) ----------------------
        admin_box = QGroupBox("Admin panel")
        al = QVBoxLayout(admin_box)
        al.setSpacing(6)
        admin_hint = QLabel(
            "Edit the board from a browser — the same avatar, presets, categories "
            "and controls as this window. Useful when the relay runs headless or "
            "on another machine. This link is <b>not</b> the guest link: it grants "
            "full control, so share it with nobody.")
        admin_hint.setWordWrap(True)
        admin_hint.setStyleSheet("color:#94a698; font-size:12px;")
        al.addWidget(admin_hint)
        arow = QHBoxLayout()
        self.admin_edit = QLineEdit(readOnly=True)
        admin_copy = QPushButton("Copy")
        admin_copy.clicked.connect(self._copy_admin)
        admin_open = QPushButton("Open")
        admin_open.clicked.connect(self._open_admin)
        admin_reset = QPushButton("New link", objectName="Danger")
        admin_reset.setToolTip("Invalidate the current admin link and make a new one")
        admin_reset.clicked.connect(self._reset_admin)
        arow.addWidget(self.admin_edit, 1)
        arow.addWidget(admin_copy)
        arow.addWidget(admin_open)
        arow.addWidget(admin_reset)
        al.addLayout(arow)

        self.admin_public = QCheckBox("Allow the admin panel over the internet")
        self.admin_public.setToolTip(
            "Off (default): the admin panel is reachable only on this PC and "
            "your LAN — anyone coming in through your tunnel or reverse proxy "
            "is refused, even with the admin link.\n"
            "On: the admin panel is reachable from anywhere with the admin "
            "link. Only turn this on to administer a headless relay remotely.")
        self.admin_public.setChecked(bool(main.core.store.settings.get("admin_public")))
        self.admin_public.toggled.connect(
            lambda on: main.core.store.set("admin_public", bool(on)))
        al.addWidget(self.admin_public)
        self.admin_public_note = QLabel(
            "🔒 Admin stays on your PC and LAN; the public guest link can’t reach it.")
        self.admin_public_note.setWordWrap(True)
        self.admin_public_note.setStyleSheet("color:#5f6f63; font-size:11px;")
        al.addWidget(self.admin_public_note)
        self.admin_public.toggled.connect(self._refresh_admin_scope_note)
        self._refresh_admin_scope_note(self.admin_public.isChecked())

        lay.addWidget(admin_box)

        self._refresh()

    def _refresh_admin_scope_note(self, public: bool) -> None:
        self.admin_public_note.setText(
            "⚠ Admin panel is reachable from the internet with the admin link — "
            "keep that link secret." if public else
            "🔒 Admin stays on your PC and LAN; the public guest link can’t reach it.")

    # -- actions ---------------------------------------------------------------

    def _toggle(self) -> None:
        # the actual start/stop lives on the main window, shared with the
        # board's STOP/RESUME button and the tray menu
        if not self.main.core.sharing_enabled:
            if not self.main.start_sharing():
                self.state_label.setText("Web server not ready yet — try again in a second.")
        else:
            self.main.stop_sharing()
        self._refresh()

    def _reset(self) -> None:
        self.main.core.regenerate_token()
        tunnel = self.main.tunnel
        if tunnel.provider() == "cloudflare" and tunnel.state in ("starting", "online"):
            tunnel.restart(self.main.web.port)  # quick tunnels: new random URL too
        self._refresh()

    def _open_settings(self) -> None:
        dlg = LinkSettingsDialog(self.main)
        dlg.exec()
        self._refresh()

    def admin_url(self) -> str:
        port = self.main.web.port or self.main.web.requested_port
        return f"http://127.0.0.1:{port}/admin?k={self.main.core.admin_token()}"

    def _copy_admin(self) -> None:
        QGuiApplication.clipboard().setText(self.admin_url())

    def _open_admin(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(self.admin_url()))

    def _reset_admin(self) -> None:
        if QMessageBox.question(
                self, "New admin link",
                "Make a new admin link? Any browser tab using the old one "
                "loses access.") == QMessageBox.Yes:
            self.main.core.regenerate_admin_token()
            self._refresh()

    def _oneclick_url(self) -> str:
        """The link WITH the token — skips the password; a secret while shared."""
        token = self.main.core.guest_token()
        if self.main.own_address_active():
            return compose_public_url(self.main.own_address(), token)
        tunnel = self.main.tunnel
        return f"{tunnel.url}/?k={token}" if tunnel.state == "online" else ""

    def _copy_oneclick(self) -> None:
        url = self._oneclick_url()
        if url:
            QGuiApplication.clipboard().setText(url)

    def _apply_own_address(self) -> None:
        raw = self.own_edit.text().strip()
        if raw and not normalize_public_url(raw):
            self._show_own_hint("⚠ That isn't a web address — it should look "
                                "like https://relay.yourdomain.com", warn=True)
            return
        self.own_edit.setText(self.main.set_own_address(raw))  # cleaned form
        self._refresh()

    def _rotate_password(self) -> None:
        self.main.core.regenerate_share_password()
        self._refresh()

    def _copy(self) -> None:
        if self.url_edit.text():
            QGuiApplication.clipboard().setText(self.url_edit.text())
            self.copy_btn.setText("Copied!")
            self.copy_btn.setEnabled(False)
            from PySide6.QtCore import QTimer
            QTimer.singleShot(1200, lambda: (self.copy_btn.setText("Copy"),
                                             self.copy_btn.setEnabled(True)))

    # -- state ------------------------------------------------------------------

    def update_tunnel(self, st: dict) -> None:
        self._refresh()

    def refresh_state(self) -> None:
        self._refresh()

    def _show_own_hint(self, text: str, warn: bool = False) -> None:
        self.own_hint.setText(text)
        self.own_hint.setStyleSheet(
            f"color:{'#b3813a' if warn else '#5f6f63'}; font-size:11px;")

    def _refresh_own_hint(self) -> None:
        address = self.main.own_address()
        if not self.own_edit.hasFocus():  # never yank text out from under typing
            self.own_edit.setText(address)
        if not address:
            self._show_own_hint(
                "Leave empty to share through a free tunnel. Have your own "
                "domain? Enter it here and it becomes the link — the same one "
                "every session.")
            return
        port = self.main.web.port or self.main.web.requested_port
        ip = lan_ip() or "this PC"
        text = (f"Point {address.split('://', 1)[1]} at http://{ip}:{port} — with "
                f"a router port forward, a reverse proxy or a Cloudflare Tunnel. "
                f"Empty the field to go back to the tunnel.")
        if address.startswith("http://"):
            self._show_own_hint(
                text + "\n⚠ Plain http:// — the password crosses the internet "
                "unencrypted. Use https:// if anything in front of the app "
                "can do it.", warn=True)
        else:
            self._show_own_hint(text)

    def _refresh(self) -> None:
        tunnel = self.main.tunnel
        sharing = self.main.core.sharing_enabled
        state = tunnel.state
        provider = tunnel.provider()
        token = self.main.core.guest_token()

        self._refresh_admin()
        self._refresh_own_hint()
        self.pw_box.setVisible(False)
        self.oneclick_btn.setVisible(False)
        if self.main.own_address_active():
            self.intro.setText(
                "Sharing on your own address — no tunnel. Pausing keeps everyone "
                "connected but shows them a “paused” screen and blocks their "
                "inputs; “Reset link” logs everyone out.")
            self._refresh_own_address(sharing, token)
            return
        self.intro.setText(
            "Creates a public link so friends can use your board from anywhere — no "
            "port forwarding needed. Pausing keeps everyone connected but shows them a "
            "“paused” screen and blocks their inputs; the link keeps working until you "
            "hit “Reset link”.")

        self.progress.setVisible(state == "downloading")
        if state == "downloading":
            self.progress.setValue(int(100 * tunnel.progress))

        if state == "downloading":
            name = "ngrok" if provider == "ngrok" else "cloudflared"
            self.state_label.setText(f"Downloading {name} (one-time)…")
            self.start_btn.setText("Cancel")
        elif state == "starting":
            self.state_label.setText("Starting tunnel…")
            self.start_btn.setText("Cancel")
        elif state == "online" and sharing:
            self.state_label.setText("✅ Sharing is live — send this link to your friends:")
            self.start_btn.setText("Pause sharing")
        elif state == "online" and not sharing:
            self.state_label.setText("⏸ Paused — guests are blocked, the link below stays valid.")
            self.start_btn.setText("Resume sharing")
        elif state == "error":
            self.state_label.setText(f"⚠️ {tunnel.error or 'Tunnel error'}")
            self.start_btn.setText("Start sharing")
        else:
            self.state_label.setText("Sharing is off.")
            self.start_btn.setText("Start sharing")

        self.url_edit.setText(f"{tunnel.url}/?k={token}" if state == "online" else "")
        port = self.main.web.port or self.main.web.requested_port
        self.local_label.setText(f"local test: http://127.0.0.1:{port}/?k={token}")
        if provider == "ngrok":
            self.note.setText("Static link (ngrok): the URL survives app restarts. "
                              "Guests may see a one-time ngrok interstitial page.")
        else:
            self.note.setText("Quick tunnel (no account): the link stays the same while "
                              "the app runs; a new one is made after a restart or Reset. "
                              "For more options, open Link settings.")

    def _refresh_admin(self) -> None:
        if hasattr(self, "admin_edit"):
            self.admin_edit.setText(self.admin_url())

    def _refresh_own_address(self, sharing: bool, token: str) -> None:
        """Own address: no tunnel — the link is the user's own domain, live
        the moment sharing is on (there is nothing to start or wait for)."""
        self.progress.setVisible(False)
        if sharing:
            self.state_label.setText("✅ Sharing is live — give your friends this "
                                     "address and the password:")
            self.start_btn.setText("Pause sharing")
        else:
            self.state_label.setText("⏸ Not sharing — guests are blocked, the "
                                     "address below stays yours.")
            self.start_btn.setText("Start sharing")

        # What you HAND OUT is the bare address — no secret in it. The token
        # link is the separate "one-click" button.
        self.url_edit.setText(self.main.own_address())
        self.pw_label.setText(self.main.core.share_password())
        self.pw_box.setVisible(True)
        self.oneclick_btn.setVisible(True)
        port = self.main.web.port or self.main.web.requested_port
        # to test from a phone on the same Wi-Fi, before the domain works
        ip = lan_ip()
        local = f"local test: http://127.0.0.1:{port}/?k={token}"
        if ip:
            local += f"    ·    on this network: http://{ip}:{port}/?k={token}"
        self.local_label.setText(local)
        self.note.setText("Your own address never changes. Guests type the "
                          "password once and their browser remembers it.")

    def update_guests(self, n: int, names: Optional[list] = None) -> None:
        text = f"{n} guest{'s' if n != 1 else ''} connected"
        if names:
            text += " — " + ", ".join(names)
        self.guests_label.setText(text)
        self.guests_label.setToolTip(", ".join(names) if names else "")


class LinkSettingsDialog(QDialog):
    def __init__(self, main) -> None:  # main: MainWindow
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Link settings")
        self.setMinimumWidth(500)
        settings = main.core.store.settings
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # -- simple view: what you get with zero setup ------------------------
        simple = QLabel(
            "Sharing uses a free <b>Cloudflare quick tunnel</b> — zero setup, "
            "nothing to configure. The link changes each session.<br>"
            "Have your own domain? Enter it as <b>Your own address</b> in the "
            "Share window instead — no tunnel needed.")
        simple.setWordWrap(True)
        lay.addWidget(simple)

        self.adv_check = QCheckBox(
            "Enable advanced settings (permanent ngrok link)")
        self.adv_check.setChecked(bool(settings.get("advanced_link")))
        self.adv_check.toggled.connect(self._update_visible)
        lay.addWidget(self.adv_check)

        # -- advanced: provider choice + per-provider fields ------------------
        self.adv_box = QWidget()
        adv = QVBoxLayout(self.adv_box)
        adv.setContentsMargins(0, 4, 0, 0)
        adv.setSpacing(10)

        self.cf_radio = QRadioButton("Cloudflare quick tunnel — zero setup, new URL per session")
        self.ngrok_radio = QRadioButton("ngrok static domain — the same URL forever")
        (self.ngrok_radio if settings.get("tunnel_provider") == "ngrok"
         else self.cf_radio).setChecked(True)
        adv.addWidget(self.cf_radio)
        adv.addWidget(self.ngrok_radio)

        # ngrok section
        self.ngrok_box = QGroupBox("ngrok static domain")
        nl = QVBoxLayout(self.ngrok_box)
        help_label = QLabel(
            'Free: create an account at '
            '<a href="https://ngrok.com" style="color:#3af08b;">ngrok.com</a>, copy your '
            '<b>authtoken</b> and your assigned <b>*.ngrok-free.app domain</b> '
            '(Dashboard → Domains), and paste both below. If the free limits are '
            'ever exceeded the tunnel just stops — nothing is billed.')
        help_label.setWordWrap(True)
        help_label.setOpenExternalLinks(True)
        help_label.setStyleSheet("color:#94a698; font-size:12px;")
        nl.addWidget(help_label)
        form = QFormLayout()
        self.token_edit = QLineEdit(settings.get("ngrok_authtoken") or "")
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.domain_edit = QLineEdit(settings.get("ngrok_domain") or "")
        self.domain_edit.setPlaceholderText("yourname.ngrok-free.app")
        form.addRow("Authtoken", self.token_edit)
        form.addRow("Static domain", self.domain_edit)
        nl.addLayout(form)
        adv.addWidget(self.ngrok_box)

        lay.addWidget(self.adv_box)

        note = QLabel("Changes apply the next time you start sharing.")
        note.setStyleSheet("color:#5f6f63; font-size:11px;")
        lay.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        for radio in (self.cf_radio, self.ngrok_radio):
            radio.toggled.connect(self._update_visible)
        self._update_visible()

    def _update_visible(self) -> None:
        advanced = self.adv_check.isChecked()
        self.adv_box.setVisible(advanced)
        self.ngrok_box.setVisible(advanced and self.ngrok_radio.isChecked())
        self.adjustSize()

    def _save(self) -> None:
        store = self.main.core.store
        advanced = self.adv_check.isChecked()

        if advanced and self.ngrok_radio.isChecked():
            provider = "ngrok"
            if not (self.token_edit.text().strip() and self.domain_edit.text().strip()):
                QMessageBox.warning(self, "Missing info",
                                    "ngrok needs both the authtoken and the static domain.")
                return
        else:
            provider = "cloudflare"  # hidden options never stay active

        store.set("advanced_link", advanced)
        store.set("tunnel_provider", provider)
        store.set("ngrok_authtoken", self.token_edit.text().strip())
        store.set("ngrok_domain", self.domain_edit.text().strip())
        self.accept()
