"""Dialogs: share/link settings and the add/edit-control form."""
from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..upnp import map_async
from ..webserver import compose_selfhost_url, lan_ip

KIND_CHOICES = {
    "Bool": [("Toggle switch", "toggle"), ("Hold button", "button")],
    "Int": [("Number stepper", "int"), ("Slider", "slider")],
    "Float": [("Slider", "slider")],
}


class ControlDialog(QDialog):
    """Add or edit a control for a parameter."""

    def __init__(self, parent: QWidget, param: str, ptype: str,
                 existing: Optional[dict] = None,
                 categories: Optional[list[dict]] = None) -> None:
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

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self.kind_combo.currentIndexChanged.connect(self._update_range_enabled)
        self._update_range_enabled()

    def _update_range_enabled(self) -> None:
        ranged = self.kind_combo.currentData() in ("slider", "int")
        self.min_spin.setEnabled(ranged)
        self.max_spin.setEnabled(ranged)

    def result_dict(self) -> dict:
        kind = self.kind_combo.currentData()
        out = {"label": self.label_edit.text().strip(), "kind": kind}
        if kind in ("slider", "int"):
            out["min"] = self.min_spin.value()
            out["max"] = self.max_spin.value()
        if self.cat_combo is not None:
            out["category"] = self.cat_combo.currentData()
        if self.invert_check is not None:
            out["invert"] = self.invert_check.isChecked()
        return out


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
        lay.addLayout(row)

        self.autostart = QCheckBox("Resume sharing automatically when the app starts")
        self.autostart.setChecked(bool(main.core.store.settings.get("share_autostart")))
        self.autostart.toggled.connect(
            lambda on: main.core.store.set("share_autostart", bool(on)))
        lay.addWidget(self.autostart)

        btns = QHBoxLayout()
        self.start_btn = QPushButton("Start sharing", objectName="Primary")
        self.start_btn.clicked.connect(self._toggle)
        self.reset_btn = QPushButton("Reset link", objectName="Danger")
        self.reset_btn.setToolTip(
            "Invalidates every link you've handed out and kicks all guests.\n"
            "Cloudflare: also picks a new random URL. ngrok: keeps your domain, "
            "changes the secret.")
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
        lay.addWidget(admin_box)

        self._refresh()

    # -- actions ---------------------------------------------------------------

    def _toggle(self) -> None:
        core, tunnel = self.main.core, self.main.tunnel
        selfhost = self.main.selfhost_active()
        if not core.sharing_enabled:
            if not self.main.web.port:
                self.state_label.setText("Web server not ready yet — try again in a second.")
                return
            core.set_sharing(True)
            if selfhost:
                if core.store.settings.get("selfhost_upnp"):
                    self._upnp = None  # "in progress"
                    map_async(core.store.settings, self.main.web.port, core.emit)
            elif tunnel.state in ("off", "error"):
                tunnel.start(self.main.web.port)
        else:
            core.set_sharing(False)
            if not selfhost and tunnel.state in ("downloading", "starting"):
                tunnel.stop()  # nothing shared yet — full cancel
            # online tunnel / opened port stays up so the link survives the pause
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

    _upnp = False  # False = never tried, None = in progress, dict = result

    def set_upnp_status(self, event: dict) -> None:
        self._upnp = event
        self._refresh()

    def _refresh(self) -> None:
        tunnel = self.main.tunnel
        sharing = self.main.core.sharing_enabled
        state = tunnel.state
        provider = tunnel.provider()
        token = self.main.core.guest_token()

        self._refresh_admin()
        if self.main.selfhost_active():
            self.intro.setText(
                "Self-hosted sharing: the link is your own address, served straight "
                "from this machine. Pausing keeps everyone connected but shows them a "
                "“paused” screen and blocks their inputs; the link keeps working until "
                "you hit “Reset link”.")
            self._refresh_selfhost(sharing, token)
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

    def _refresh_selfhost(self, sharing: bool, token: str) -> None:
        """Self-hosted mode: no tunnel — the link is the user's own address."""
        settings = self.main.core.store.settings
        self.progress.setVisible(False)
        if sharing:
            self.state_label.setText("✅ Self-hosting — send this link to your friends:")
            self.start_btn.setText("Pause sharing")
        else:
            self.state_label.setText("⏸ Not sharing — the address stays yours, "
                                     "guests are blocked until you start.")
            self.start_btn.setText("Start sharing")
        url = compose_selfhost_url(settings.get("selfhost_host", ""),
                                   settings.get("selfhost_port")
                                   or settings.get("web_port"), token)
        self.url_edit.setText(url)
        port = self.main.web.port or self.main.web.requested_port
        # self-hosters need the machine's own address (reverse proxy, router
        # forward, or just testing from another device on the same network)
        ip = lan_ip()
        local = f"local test: http://127.0.0.1:{port}/?k={token}"
        if ip:
            local += f"    ·    on this network: http://{ip}:{port}/?k={token}"
        self.local_label.setText(local)

        ext_port = settings.get("selfhost_port") or settings.get("web_port")
        lines = [f"Self-hosted: guests connect straight to this machine over plain "
                 f"HTTP — make sure port {ext_port} reaches this PC."]
        if self._upnp is None:
            lines.append("UPnP: asking the router to open the port…")
        elif isinstance(self._upnp, dict):
            if self._upnp.get("ok"):
                ip = self._upnp.get("info") or "?"
                lines.append(f"UPnP: port {self._upnp.get('port')} opened ✓ "
                             f"(public IP {ip})")
            else:
                lines.append(f"UPnP failed: {self._upnp.get('info')} — "
                             "forward the port on your router manually.")
        self.note.setText("\n".join(lines))

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
            "nothing to configure. The link changes each session.")
        simple.setWordWrap(True)
        lay.addWidget(simple)

        self.adv_check = QCheckBox(
            "Enable advanced settings (permanent ngrok link, self-hosting)")
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
        self.selfhost_radio = QRadioButton("Self-hosted — your own port and domain, no tunnel")
        provider = settings.get("tunnel_provider")
        {"ngrok": self.ngrok_radio, "selfhost": self.selfhost_radio}.get(
            provider, self.cf_radio).setChecked(True)
        adv.addWidget(self.cf_radio)
        adv.addWidget(self.ngrok_radio)
        adv.addWidget(self.selfhost_radio)

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

        # self-host section
        self.selfhost_box = QGroupBox("Self-hosting")
        sl = QVBoxLayout(self.selfhost_box)
        sh_help = QLabel(
            "The app serves the guest page directly on this machine and your "
            "link is your own address — e.g. a free dynamic-DNS name "
            "(FreeDNS/mooo.com, DuckDNS, …) kept up to date by your router.")
        sh_help.setWordWrap(True)
        sh_help.setStyleSheet("color:#94a698; font-size:12px;")
        sl.addWidget(sh_help)
        sform = QFormLayout()
        self.listen_spin = QSpinBox()
        self.listen_spin.setRange(1, 65535)
        self.listen_spin.setValue(int(settings.get("web_port") or 3080))
        self.host_edit = QLineEdit(settings.get("selfhost_host") or "")
        self.host_edit.setPlaceholderText("myname.mooo.com   (or https://relay.example.com)")
        self.public_spin = QSpinBox()
        self.public_spin.setRange(1, 65535)
        self.public_spin.setValue(int(settings.get("selfhost_port")
                                      or settings.get("web_port") or 3080))
        sform.addRow("Listen port", self.listen_spin)
        sform.addRow("Public address", self.host_edit)
        sform.addRow("Public port", self.public_spin)
        sl.addLayout(sform)
        self.upnp_check = QCheckBox("Open the port on the router automatically (UPnP)")
        self.upnp_check.setChecked(bool(settings.get("selfhost_upnp")))
        sl.addWidget(self.upnp_check)
        warn = QLabel(
            "⚠ Self-hosting exposes the guest page beyond this PC and serves it "
            "over plain HTTP. Anyone with the link can use your board — use "
            "“Reset link” if it leaks. For HTTPS, put a reverse proxy in front "
            "and enter its https:// address above.")
        warn.setWordWrap(True)
        warn.setStyleSheet("color:#b3813a; font-size:12px;")
        sl.addWidget(warn)
        adv.addWidget(self.selfhost_box)

        lay.addWidget(self.adv_box)

        note = QLabel("Changes apply the next time you start sharing.")
        note.setStyleSheet("color:#5f6f63; font-size:11px;")
        lay.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        for radio in (self.cf_radio, self.ngrok_radio, self.selfhost_radio):
            radio.toggled.connect(self._update_visible)
        self._update_visible()

    def _update_visible(self) -> None:
        advanced = self.adv_check.isChecked()
        self.adv_box.setVisible(advanced)
        self.ngrok_box.setVisible(advanced and self.ngrok_radio.isChecked())
        self.selfhost_box.setVisible(advanced and self.selfhost_radio.isChecked())
        self.adjustSize()

    def _save(self) -> None:
        store = self.main.core.store
        web = self.main.web
        advanced = self.adv_check.isChecked()

        if not advanced:
            provider = "cloudflare"  # everything else is hidden -> don't keep it active
        elif self.ngrok_radio.isChecked():
            provider = "ngrok"
            if not (self.token_edit.text().strip() and self.domain_edit.text().strip()):
                QMessageBox.warning(self, "Missing info",
                                    "ngrok needs both the authtoken and the static domain.")
                return
        elif self.selfhost_radio.isChecked():
            provider = "selfhost"
            if not self.host_edit.text().strip():
                QMessageBox.warning(
                    self, "Missing info",
                    "Self-hosting needs a public address — the domain or DDNS "
                    "name your friends will reach you under.")
                return
        else:
            provider = "cloudflare"

        store.set("advanced_link", advanced)
        store.set("tunnel_provider", provider)
        store.set("ngrok_authtoken", self.token_edit.text().strip())
        store.set("ngrok_domain", self.domain_edit.text().strip())
        store.set("selfhost_host", self.host_edit.text().strip())
        store.set("selfhost_port", int(self.public_spin.value()))
        store.set("selfhost_upnp", self.upnp_check.isChecked())
        new_listen = int(self.listen_spin.value())
        store.set("web_port", new_listen)

        # re-bind live if the listener needs to move (port or loopback vs LAN)
        if (web.bind_host != (web._bind_host() or "*")
                or (web.port or 0) != new_listen):
            web.rebind(new_listen)
        self.accept()
