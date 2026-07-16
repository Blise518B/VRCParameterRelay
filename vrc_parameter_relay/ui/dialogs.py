"""Dialogs: share/link settings and the add/edit-control form."""
from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
    QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

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
        return out


class ShareDialog(QDialog):
    def __init__(self, main) -> None:  # main: MainWindow
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Share remote control")
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        intro = QLabel(
            "Creates a public link so friends can use your board from anywhere — no "
            "port forwarding needed. The link keeps working (even across pauses) "
            "until you hit “Reset link”. Pausing kicks guests but keeps the same link.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#94a698;")
        lay.addWidget(intro)

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
        self._refresh()

    # -- actions ---------------------------------------------------------------

    def _toggle(self) -> None:
        core, tunnel = self.main.core, self.main.tunnel
        if not core.sharing_enabled:
            if not self.main.web.port:
                self.state_label.setText("Web server not ready yet — try again in a second.")
                return
            core.set_sharing(True)
            if tunnel.state in ("off", "error"):
                tunnel.start(self.main.web.port)
        else:
            core.set_sharing(False)
            if tunnel.state in ("downloading", "starting"):
                tunnel.stop()  # nothing shared yet — full cancel
            # online tunnel stays up so the link survives the pause
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

    def _refresh(self) -> None:
        tunnel = self.main.tunnel
        sharing = self.main.core.sharing_enabled
        state = tunnel.state
        provider = tunnel.provider()

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

        token = self.main.core.guest_token()
        self.url_edit.setText(f"{tunnel.url}/?k={token}" if state == "online" else "")
        port = self.main.web.port or self.main.web.requested_port
        self.local_label.setText(f"local test: http://127.0.0.1:{port}/?k={token}")
        if provider == "ngrok":
            self.note.setText("Static link (ngrok): the URL survives app restarts. "
                              "Guests may see a one-time ngrok interstitial page.")
        else:
            self.note.setText("Quick tunnel (no account): the link stays the same while "
                              "the app runs; a new one is made after a restart or Reset. "
                              "For a permanent link, open Link settings.")

    def update_guests(self, n: int) -> None:
        self.guests_label.setText(f"{n} guest{'s' if n != 1 else ''} connected")


class LinkSettingsDialog(QDialog):
    def __init__(self, main) -> None:  # main: MainWindow
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Link settings")
        self.setMinimumWidth(500)
        settings = main.core.store.settings
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        self.cf_radio = QRadioButton("Cloudflare quick tunnel — zero setup, new URL per session")
        self.ngrok_radio = QRadioButton("ngrok static domain — the same URL forever")
        (self.ngrok_radio if settings.get("tunnel_provider") == "ngrok"
         else self.cf_radio).setChecked(True)
        lay.addWidget(self.cf_radio)
        lay.addWidget(self.ngrok_radio)

        help_label = QLabel(
            'For a permanent link: create a free account at '
            '<a href="https://ngrok.com" style="color:#3af08b;">ngrok.com</a>, copy your '
            '<b>authtoken</b>, claim your free <b>static domain</b> '
            '(Dashboard → Domains), and paste both below.')
        help_label.setWordWrap(True)
        help_label.setOpenExternalLinks(True)
        help_label.setStyleSheet("color:#94a698; font-size:12px;")
        lay.addWidget(help_label)

        form = QFormLayout()
        self.token_edit = QLineEdit(settings.get("ngrok_authtoken") or "")
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.domain_edit = QLineEdit(settings.get("ngrok_domain") or "")
        self.domain_edit.setPlaceholderText("yourname.ngrok-free.app")
        form.addRow("Authtoken", self.token_edit)
        form.addRow("Static domain", self.domain_edit)
        lay.addLayout(form)

        note = QLabel("Changes apply the next time you start sharing.")
        note.setStyleSheet("color:#5f6f63; font-size:11px;")
        lay.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        for radio in (self.cf_radio, self.ngrok_radio):
            radio.toggled.connect(self._update_enabled)
        self._update_enabled()

    def _update_enabled(self) -> None:
        use_ngrok = self.ngrok_radio.isChecked()
        self.token_edit.setEnabled(use_ngrok)
        self.domain_edit.setEnabled(use_ngrok)

    def _save(self) -> None:
        store = self.main.core.store
        use_ngrok = self.ngrok_radio.isChecked()
        if use_ngrok and not (self.token_edit.text().strip() and self.domain_edit.text().strip()):
            QMessageBox.warning(self, "Missing info",
                                "ngrok needs both the authtoken and the static domain.")
            return
        store.set("tunnel_provider", "ngrok" if use_ngrok else "cloudflare")
        store.set("ngrok_authtoken", self.token_edit.text().strip())
        store.set("ngrok_domain", self.domain_edit.text().strip())
        self.accept()
