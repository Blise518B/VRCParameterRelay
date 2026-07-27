"""Main window: control board, live parameter dock, share dialog."""
from __future__ import annotations

import json
import re
import sys
import threading
import urllib.request
from typing import Any, Optional

from PySide6.QtCore import QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDockWidget, QFrame, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton,
    QRadioButton, QScrollArea, QSystemTrayIcon, QTextBrowser, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import APP_NAME, AUTHOR, GITHUB_URL, __version__, resource_path
from ..webserver import compose_selfhost_url
from .dialogs import ControlDialog, ShareDialog
from .theme import (
    DEFAULT_FONT, DEFAULT_THEME, FONT_GROUPS, THEME_LABELS, THEME_ORDER,
    accent_of, build_qss,
)
from .widgets import CategoryBox, ControlCard, ParamTree, drag_ghost

RELEASES_URL = f"{GITHUB_URL}/VRCParameterRelay/releases"
LATEST_API = "https://api.github.com/repos/Blise518B/VRCParameterRelay/releases/latest"


class CoreBridge(QObject):
    """Marshals AppCore events (worker threads) onto the Qt main thread."""
    event = Signal(object)


HEADER_H = 58          # header and dock title share one height so borders align
ONE_COLUMN_BELOW = 540  # board viewport width where it collapses to one column


DOCK_TITLE_H = 42


class _TitleBar(QWidget):
    """QDockWidget sizes custom title bars by sizeHint, not fixed height."""

    def sizeHint(self) -> QSize:
        return QSize(200, DOCK_TITLE_H)

    def minimumSizeHint(self) -> QSize:
        return QSize(120, DOCK_TITLE_H)


class MainWindow(QMainWindow):
    update_found = Signal(str)   # newer version tag available on GitHub
    activate_requested = Signal()  # another start was attempted (from a thread)

    def __init__(self, core, tunnel, web) -> None:
        super().__init__()
        self.core = core
        self.tunnel = tunnel
        self.web = web
        self.cards: dict[str, ControlCard] = {}      # control id -> card
        self.param_items: dict[str, QTreeWidgetItem] = {}
        self.guest_count = 0
        self.guest_names: list[str] = []

        self.setWindowTitle(APP_NAME)
        self._restore_window_size()

        self.bridge = CoreBridge()
        self.bridge.event.connect(self._on_event)
        core.add_listener(self.bridge.event.emit)
        tunnel.on_change = lambda st: core.emit({"t": "tunnel", **st})

        self._build_header()
        self._build_board_area()
        self._build_params_dock()
        self.share_dialog = ShareDialog(self)
        self.statusBar().showMessage("Starting…")
        self.update_label = QLabel()
        self.update_label.setOpenExternalLinks(True)
        self.update_label.setVisible(False)
        self.statusBar().addWidget(self.update_label)

        credit = QLabel(
            f'{APP_NAME} v{__version__} · made by <a href="{GITHUB_URL}" '
            f'style="color:#3af08b; text-decoration:none;">{AUTHOR}</a>&nbsp;')
        credit.setOpenExternalLinks(True)
        self.statusBar().addPermanentWidget(credit)

        self.update_found.connect(self._show_update)
        # queued across threads: the guard fires from its listener thread
        self.activate_requested.connect(self._tray_show)

        self._build_tray()
        self._rebuild_board()
        self._refresh_params(core.param_snapshot())
        self._refresh_pause_btn()
        self._start_update_check()

    # -- system tray -------------------------------------------------------------

    def _build_tray(self) -> None:
        self._quitting = False
        self._tray_notice_shown = False
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon_file = resource_path("assets/icon.ico")
        icon = QIcon(str(icon_file)) if icon_file.exists() else self.windowIcon()
        self.tray = QSystemTrayIcon(icon, self)
        self._tray_menu = QMenu()
        self._tray_menu.addAction("Open", self._tray_show)
        self._tray_pause = self._tray_menu.addAction("Pause sharing",
                                                     self._toggle_pause)
        self._tray_menu.addAction("Copy share link", self._tray_copy_link)
        self._tray_menu.addAction("Open board in browser", self._tray_open_browser)
        self._tray_menu.addSeparator()
        self._tray_menu.addAction("Quit", self.quit_app)
        self.tray.setContextMenu(self._tray_menu)
        self.tray.setToolTip(APP_NAME)
        self.tray.activated.connect(
            lambda reason: self._tray_show()
            if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _tray_show(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _tray_copy_link(self) -> None:
        url = self.current_share_url()
        if url:
            QGuiApplication.clipboard().setText(url)
            if self.tray:
                self.tray.showMessage(APP_NAME, "Share link copied.",
                                      QSystemTrayIcon.Information, 2000)

    def _tray_open_browser(self) -> None:
        """Open the guest board here — the tray equivalent of the window."""
        url = self.current_share_url()
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def current_share_url(self) -> str:
        """The link guests should use right now (tunnel, self-host or local)."""
        token = self.core.guest_token()
        if self.selfhost_active():
            settings = self.core.store.settings
            url = compose_selfhost_url(
                settings.get("selfhost_host", ""),
                settings.get("selfhost_port") or settings.get("web_port"), token)
            if url:
                return url
        if self.tunnel.state == "online" and self.tunnel.url:
            return f"{self.tunnel.url}/?k={token}"
        port = self.web.port or self.web.requested_port
        return f"http://127.0.0.1:{port}/?k={token}"

    def quit_app(self) -> None:
        self._quitting = True
        self.close()

    # -- window geometry ---------------------------------------------------------

    #: fresh-install window size (no saved geometry yet) — a comfortable
    #: two-column fit
    DEFAULT_SIZE = (1280, 750)

    def _restore_window_size(self) -> None:
        saved = self.core.store.settings.get("window_size")
        try:
            w, h = int(saved[0]), int(saved[1])
            if w < 480 or h < 360:
                raise ValueError
            self.resize(w, h)
        except (TypeError, ValueError, IndexError):
            self.resize(*self.DEFAULT_SIZE)
        if self.core.store.settings.get("window_maximized"):
            self.setWindowState(Qt.WindowMaximized)

    def closeEvent(self, event) -> None:
        size = self.normalGeometry().size()  # pre-maximize size, not the screen's
        self.core.store.set("window_size", [size.width(), size.height()])
        self.core.store.set("window_maximized", self.isMaximized())
        # closing keeps the relay alive in the tray (unless quitting for real)
        if (not self._quitting and self.tray is not None
                and self.core.store.settings.get("close_to_tray", True)):
            event.ignore()
            self.hide()
            if not self._tray_notice_shown:
                self._tray_notice_shown = True
                self.tray.showMessage(
                    APP_NAME,
                    "Still running here — sharing stays up. "
                    "Right-click the icon for options.",
                    QSystemTrayIcon.Information, 4000)
            return
        super().closeEvent(event)

    # -- layout -----------------------------------------------------------------

    def _build_header(self) -> None:
        header = QWidget(objectName="Header")
        header.setFixedHeight(HEADER_H)
        lay = QHBoxLayout(header)
        lay.setContentsMargins(16, 6, 16, 6)

        left = QVBoxLayout()
        left.setSpacing(0)
        name_row = QHBoxLayout()
        name_row.setSpacing(2)
        self.board_name = QLineEdit(objectName="BoardName")
        self.board_name.setPlaceholderText("Avatar name")
        self.board_name.setToolTip("Name this avatar — named avatars are kept "
                                   "for offline editing")
        self.board_name.setMinimumWidth(110)
        self.board_name.setMaximumWidth(300)
        self.board_name.editingFinished.connect(
            lambda: self.core.rename_avatar(self.board_name.text()))
        name_row.addWidget(self.board_name)
        self.avatar_menu_btn = QToolButton(objectName="AvatarMenu")
        self.avatar_menu_btn.setText("▾")
        self.avatar_menu_btn.setToolTip("Open a saved avatar (● = worn in VRChat)")
        self.avatar_menu_btn.setCursor(Qt.PointingHandCursor)
        self.avatar_menu_btn.clicked.connect(self._show_avatar_menu)
        name_row.addWidget(self.avatar_menu_btn)
        name_row.addSpacing(6)
        self.live_badge = QLabel("", objectName="LiveBadge")
        self.live_badge.setFixedHeight(20)
        self.live_badge.setVisible(False)
        name_row.addWidget(self.live_badge, 0, Qt.AlignVCenter)
        name_row.addStretch(1)
        self.avatar_label = QLabel("waiting for VRChat…", objectName="AvatarId")
        self.avatar_label.setMinimumWidth(60)  # allow clipping instead of forcing width
        left.addLayout(name_row)
        left.addWidget(self.avatar_label)
        lay.addLayout(left, 1)

        self.preset_combo = QComboBox(objectName="PresetCombo")
        self.preset_combo.setToolTip("Preset — a separate board layout for this avatar")
        self.preset_combo.setMinimumWidth(110)
        self.preset_combo.setMaximumWidth(160)
        self.preset_combo.activated.connect(self._preset_chosen)
        lay.addWidget(self.preset_combo)
        preset_menu_btn = QToolButton(objectName="CardMenu")
        preset_menu_btn.setText("⋯")
        preset_menu_btn.setToolTip("Add, rename or delete presets")
        preset_menu_btn.setCursor(Qt.PointingHandCursor)
        preset_menu_btn.clicked.connect(self._show_preset_menu)
        lay.addWidget(preset_menu_btn)

        self.vrc_chip = QLabel("VRChat: searching", objectName="Chip")
        self.guest_chip = QPushButton("guests: 0", objectName="ChipBtn")
        self.guest_chip.setCursor(Qt.PointingHandCursor)
        self.guest_chip.setToolTip("Click to see who's connected")
        self.guest_chip.clicked.connect(self._show_guest_list)
        lay.addWidget(self.vrc_chip)
        lay.addWidget(self.guest_chip)

        self.yolo_btn = QPushButton("⚡ YOLO", objectName="YoloToggle")
        self.yolo_btn.setCheckable(True)
        self.yolo_btn.setChecked(self.core.yolo_enabled)
        self.yolo_btn.setToolTip(
            "YOLO mode: guests get the full live parameter list and can set anything,\n"
            "ignoring category locks. Stays on until you turn it off — even after restarts.")
        self.yolo_btn.toggled.connect(lambda on: self.request_yolo(on, self.yolo_btn))

        sync_btn = QPushButton("⟳ Resync", objectName="SyncBtn")
        sync_btn.setToolTip("Re-sync avatar && parameters from VRChat now")
        sync_btn.clicked.connect(lambda: self.core.link.refetch())
        self.sync_btn = sync_btn  # placed in the parameter panel's title bar

        self.cat_btn = QPushButton("＋ Category")
        self.cat_btn.setToolTip("Add another category box to the board")
        self.cat_btn.clicked.connect(lambda: self.core.add_category())
        self.pause_btn = QPushButton("■ STOP", objectName="PauseBtn")  # in board toolbar
        self.pause_btn.setCursor(Qt.PointingHandCursor)
        self.pause_btn.clicked.connect(self._toggle_pause)
        share_btn = QPushButton("Share", objectName="Primary")
        share_btn.clicked.connect(self._open_share)
        help_btn = QPushButton("?", objectName="HelpBtn")
        help_btn.setFixedWidth(34)
        help_btn.setToolTip("Help")
        help_btn.clicked.connect(lambda: HelpDialog(self).exec())
        #  = Segoe MDL2 "Setting" — a flat monochrome pictogram, so it
        # matches the other header buttons instead of a colour emoji gear
        #  is Segoe MDL2 (Windows only); elsewhere use the plain
        # gear with a text-presentation selector so it is not a colour emoji
        gear_glyph = "" if sys.platform == "win32" else "⚙︎"
        self.gear_btn = QPushButton(gear_glyph, objectName="GearBtn")
        self.gear_btn.setFixedWidth(34)
        self.gear_btn.setToolTip("Settings — design && font")
        self.gear_btn.setCursor(Qt.PointingHandCursor)
        self.gear_btn.clicked.connect(self._open_settings)
        lay.addWidget(share_btn)
        lay.addWidget(help_btn)
        lay.addWidget(self.gear_btn)

        # chips are QLabels that would otherwise stretch to the header's height
        for widget in (self.vrc_chip, self.guest_chip, share_btn,
                       help_btn, self.gear_btn, self.preset_combo, preset_menu_btn):
            widget.setFixedHeight(34)

        # full-window header (above the dock too) so its width requirement
        # doesn't stack on top of the parameter panel's
        self.setMenuWidget(header)

        wrapper = QWidget()
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)

        # slim toolbar above the categories — board-level actions live here so
        # the header stays avatar/sharing state only
        self.board_toolbar = QWidget()
        bar = QHBoxLayout(self.board_toolbar)
        bar.setContentsMargins(14, 10, 14, 0)
        bar.setSpacing(8)
        bar.addWidget(self.cat_btn)
        bar.addWidget(self.yolo_btn)
        bar.addWidget(self.pause_btn)
        bar.addStretch(1)
        for widget in (self.cat_btn, self.yolo_btn, self.pause_btn):
            widget.setFixedHeight(32)
        left.addWidget(self.board_toolbar)

        self.board_scroll = QScrollArea(widgetResizable=True)
        self.board_container = QWidget(objectName="BoardArea")
        self.board_cols_layout = QHBoxLayout(self.board_container)
        self.board_cols_layout.setContentsMargins(14, 14, 14, 14)
        self.board_cols_layout.setSpacing(12)
        self.col_layouts: list[QVBoxLayout] = [QVBoxLayout(), QVBoxLayout()]
        for col in self.col_layouts:
            col.setSpacing(12)
            col.setAlignment(Qt.AlignTop)
            self.board_cols_layout.addLayout(col, 1)
        self.board_scroll.setWidget(self.board_container)
        left.addWidget(self.board_scroll, 1)

        self.empty_label = QLabel(
            "Waiting for VRChat…\n\nStart VRChat with OSC enabled and load into an avatar.",
            alignment=Qt.AlignCenter)
        self.empty_label.setStyleSheet("color:#6d6d82; font-size:14px;")
        left.addWidget(self.empty_label, 2)
        body.addLayout(left, 1)

        # pull-tab: < opens the parameter panel, > tucks it away; the dock
        # separator right of it is the full-height resize line
        self.dock_toggle = QPushButton(">", objectName="DockToggle")
        self.dock_toggle.setFixedSize(26, 84)
        self.dock_toggle.setCursor(Qt.PointingHandCursor)
        self.dock_toggle.setToolTip("Show/hide the avatar parameters panel\n"
                                    "(double-click a parameter there to add it)")
        self.dock_toggle.clicked.connect(
            lambda: self.params_dock.setVisible(not self.params_dock.isVisible()))
        tab_wrap = QVBoxLayout()
        tab_wrap.setContentsMargins(0, 0, 0, 0)
        tab_wrap.addStretch(1)
        tab_wrap.addWidget(self.dock_toggle)
        tab_wrap.addStretch(1)
        body.addLayout(tab_wrap)

        wl.addLayout(body, 1)
        self.setCentralWidget(wrapper)

    def _build_board_area(self) -> None:
        pass  # built inside _build_header's wrapper

    def _build_params_dock(self) -> None:
        self.params_dock = QDockWidget("Avatar parameters", self)
        self.params_dock.setFeatures(QDockWidget.NoDockWidgetFeatures)

        # custom title bar matching the header so the top border runs
        # continuously across the whole window
        title_bar = _TitleBar(objectName="DockTitle")
        tl = QHBoxLayout(title_bar)
        tl.setContentsMargins(14, 0, 14, 0)
        tl.addWidget(QLabel("Avatar parameters", objectName="DockTitleText"))
        tl.addStretch(1)
        self.sync_btn.setFixedHeight(30)
        self.sync_btn.setCursor(Qt.PointingHandCursor)
        tl.addWidget(self.sync_btn)
        self.params_dock.setTitleBarWidget(title_bar)

        inner = QWidget()
        lay = QVBoxLayout(inner)
        # no side margins: the parameter table runs edge to edge; the search
        # box and the hints below get their own inset instead
        lay.setContentsMargins(0, 10, 0, 10)
        lay.setSpacing(8)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(12, 0, 12, 0)
        self.search = QLineEdit(placeholderText="Filter parameters…")
        self.search.textChanged.connect(self._apply_filter)
        search_row.addWidget(self.search)
        lay.addLayout(search_row)

        self.tree = ParamTree()
        self.tree.setHeaderLabels(["Parameter", "Type", "Value"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setTextElideMode(Qt.ElideRight)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QTreeWidget.DragOnly)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)  # name column absorbs resizes
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        self.tree.setColumnWidth(1, 52)
        self.tree.setColumnWidth(2, 76)
        self.tree.itemDoubleClicked.connect(self._add_from_item)
        lay.addWidget(self.tree)

        hint = QLabel("Double-click or drag the parameters into a group.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8ba58f; font-size:12px; padding:0 12px;")
        lay.addWidget(hint)
        values_note = QLabel("Values update live from VRChat.")
        values_note.setStyleSheet("color:#6d6d82; font-size:11px; padding:0 12px;")
        lay.addWidget(values_note)

        self.params_dock.setWidget(inner)
        self.params_dock.setMinimumWidth(300)
        self.addDockWidget(Qt.RightDockWidgetArea, self.params_dock)
        self.params_dock.visibilityChanged.connect(
            lambda visible: self.dock_toggle.setText(">" if visible else "<"))

    # -- responsive board columns -------------------------------------------------

    _board_cols = 2

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        target = 1 if self.board_scroll.viewport().width() < ONE_COLUMN_BELOW else 2
        if target != self._board_cols:
            self._board_cols = target
            self._rebuild_board()

    # -- core events --------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        t = event.get("t")
        if t == "param":
            self._update_param_row(event["name"], event["ptype"], event["value"])
            for card in self.cards.values():
                if card.control["param"] == event["name"]:
                    card.update_value(event["value"])
        elif t == "params_reset":
            self._refresh_params(event["params"])
        elif t in ("avatar", "board"):
            self._rebuild_board()
        elif t == "vrc_status":
            self._update_vrc_chip(event)
        elif t == "tunnel":
            self.share_dialog.update_tunnel(event)
            self._refresh_pause_btn()
        elif t == "guests":
            self.guest_count = event["count"]
            self.guest_names = event.get("names") or []
            self.guest_chip.setText(f"guests: {self.guest_count}")
            self.guest_chip.setToolTip(
                "Connected: " + ", ".join(self.guest_names) if self.guest_names
                else "Click to see who's connected")
            self.share_dialog.update_guests(self.guest_count, self.guest_names)
        elif t == "token":
            self.share_dialog.refresh_state()
        elif t == "sharing":
            self.share_dialog.refresh_state()
            self._refresh_pause_btn()
        elif t == "upnp":
            self.share_dialog.set_upnp_status(event)
        elif t == "yolo":
            self.yolo_btn.blockSignals(True)
            self.yolo_btn.setChecked(event["enabled"])
            self.yolo_btn.blockSignals(False)
            self.share_dialog.refresh_state()

    def _update_vrc_chip(self, st: dict) -> None:
        if st.get("vrchat_found") or st.get("receiving"):
            self.vrc_chip.setText("VRChat: connected")
            self.vrc_chip.setProperty("state", "ok")
        elif st.get("advertised"):
            self.vrc_chip.setText("VRChat: searching")
            self.vrc_chip.setProperty("state", "")
        else:
            self.vrc_chip.setText("VRChat: mDNS error")
            self.vrc_chip.setProperty("state", "bad")
        self.vrc_chip.style().unpolish(self.vrc_chip)
        self.vrc_chip.style().polish(self.vrc_chip)
        self.statusBar().showMessage(
            f"OSC in: 127.0.0.1:{st.get('osc_port', '?')} (via OSCQuery)   |   "
            f"OSC out: {st.get('send_target', '?')}   |   "
            f"advertised: {'yes' if st.get('advertised') else 'no'}   |   "
            f"web: 127.0.0.1:{self.web.port or '…'}")

    # -- board ---------------------------------------------------------------------

    def _rebuild_board(self) -> None:
        board = self.core.board
        self._refresh_avatar_header()

        self._cat_ghost = None  # cleared with the layouts below
        for col in self.col_layouts:
            while col.count():
                item = col.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
        self.cards.clear()
        self.boxes: dict[str, CategoryBox] = {}

        has_avatar = self.core.avatar_id is not None
        self.empty_label.setVisible(not has_avatar)
        self.board_scroll.setVisible(has_avatar)
        self.board_toolbar.setVisible(has_avatar)
        if not has_avatar:
            return

        other_presets = [(p["id"], p["name"]) for p in self.core.list_presets()
                         if p["id"] != board.get("id")]
        values = self.core.board_values()
        for i, category in enumerate(board["categories"]):
            box = CategoryBox(category)
            box.renamed.connect(self.core.rename_category)
            box.lock_toggled.connect(self.core.set_category_locked)
            box.delete_requested.connect(self._delete_category)
            box.control_dropped.connect(self.core.move_control_to_category)
            box.param_dropped.connect(self._param_dropped)
            box.category_dropped.connect(self.core.move_category)
            box.category_drag_over.connect(self._show_cat_ghost)
            box.category_drag_done.connect(self._clear_cat_ghost)
            box.set_copy_targets(other_presets)
            box.copy_to_preset.connect(self.core.copy_category_to_preset)
            box.color_changed.connect(self.core.set_category_color)
            self.boxes[category["id"]] = box
            for control in board["controls"]:
                if control.get("cat") != category["id"]:
                    continue
                card = ControlCard(control, values.get(control["param"]))
                card.set_value.connect(self.core.set_control_value)
                card.remove_requested.connect(self.core.remove_control)
                card.edit_requested.connect(self._edit_control)
                box.add_card(card)
                self.cards[control["id"]] = card
            box.finalize()
            self.col_layouts[i % self._board_cols].addWidget(box)

        # in single-column mode the empty second column must not eat width
        self.board_cols_layout.setStretch(0, 1)
        self.board_cols_layout.setStretch(1, 1 if self._board_cols == 2 else 0)

    # -- avatar library + presets ---------------------------------------------------

    def _show_avatar_menu(self) -> None:
        menu = QMenu(self)
        for entry in self.core.list_avatars():
            live = entry["avatar_id"] == self.core.live_avatar_id
            label = ("● " if live else "   ") + entry["name"]
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(entry["avatar_id"] == self.core.avatar_id)
            action.triggered.connect(
                lambda _=False, aid=entry["avatar_id"]: self.core.open_avatar(aid))
        if menu.isEmpty():
            menu.addAction("No saved avatars yet").setEnabled(False)
        menu.exec(self.avatar_menu_btn.mapToGlobal(
            self.avatar_menu_btn.rect().bottomLeft()))

    def _preset_chosen(self, index: int) -> None:
        preset_id = self.preset_combo.itemData(index)
        if preset_id:
            self.core.switch_preset(preset_id)

    def _show_preset_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("＋ New preset", lambda: self.core.add_preset())
        menu.addAction("Rename preset…", self._rename_preset)
        menu.addSeparator()
        menu.addAction("Delete preset", self._delete_preset)
        menu.exec(self.cursor().pos())

    def _rename_preset(self) -> None:
        current = self.core.board
        name, ok = QInputDialog.getText(self, "Rename preset", "Preset name:",
                                        text=current.get("name", ""))
        if ok and name.strip():
            self.core.rename_preset(current["id"], name.strip())

    def _delete_preset(self) -> None:
        current = self.core.board
        if len(self.core.list_presets()) <= 1:
            QMessageBox.information(self, "Can't delete",
                                    "The avatar needs at least one preset.")
            return
        answer = QMessageBox.question(
            self, "Delete preset",
            f"Delete preset “{current.get('name')}” and everything on it?")
        if answer == QMessageBox.Yes:
            self.core.remove_preset(current["id"])

    def _refresh_avatar_header(self) -> None:
        self.board_name.setText(self.core.avatar_name)
        live = self.core.is_live
        avatar_id = self.core.avatar_id
        self.live_badge.setVisible(avatar_id is not None)
        if avatar_id is not None:
            self.live_badge.setText("● LIVE" if live else "✎ OFFLINE")
            self.live_badge.setProperty("state", "live" if live else "offline")
            self.live_badge.setToolTip(
                "You're wearing this avatar in VRChat — controls are live"
                if live else
                "Editing only — you're not wearing this avatar,\n"
                "nothing is sent to VRChat")
            self.live_badge.style().unpolish(self.live_badge)
            self.live_badge.style().polish(self.live_badge)
        if live:
            accent = accent_of(self.core.store.settings.get("theme", DEFAULT_THEME))
            self.avatar_label.setText(
                f'<span style="color:{accent}">●</span> {avatar_id}')
        elif avatar_id is not None:
            self.avatar_label.setText(
                f'<span style="color:#c9525f">{avatar_id}</span>')
        else:
            self.avatar_label.setText("waiting for VRChat…")
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for preset in self.core.list_presets():
            self.preset_combo.addItem(preset["name"], preset["id"])
        idx = self.preset_combo.findData(self.core.board.get("id"))
        if idx >= 0:
            self.preset_combo.setCurrentIndex(idx)
        self.preset_combo.blockSignals(False)

    # -- category drag ghost (half-transparent landing preview) --------------------

    _cat_ghost: Optional[QLabel] = None

    def _show_cat_ghost(self, dragged_id: str, target_id: str) -> None:
        if dragged_id == target_id:
            self._clear_cat_ghost()
            return
        pixmap = drag_ghost()
        target_box = self.boxes.get(target_id)
        if pixmap is None or target_box is None:
            return
        for col in self.col_layouts:
            for i in range(col.count()):
                if col.itemAt(i).widget() is target_box:
                    if (self._cat_ghost is not None
                            and col.indexOf(self._cat_ghost) == i - 1):
                        return  # already previewing this slot
                    self._clear_cat_ghost()
                    label = QLabel()
                    label.setPixmap(pixmap)
                    col.insertWidget(i, label)
                    self._cat_ghost = label
                    return

    def _clear_cat_ghost(self) -> None:
        if self._cat_ghost is not None:
            try:
                self._cat_ghost.setParent(None)
                self._cat_ghost.deleteLater()
            except RuntimeError:
                pass  # already deleted by a board rebuild
            self._cat_ghost = None

    def _delete_category(self, cat_id: str) -> None:
        board = self.core.board
        has_controls = any(c.get("cat") == cat_id for c in board["controls"])
        if has_controls:
            answer = QMessageBox.question(
                self, "Delete category",
                "This category still has controls — they'll move to the first "
                "category. Delete it?")
            if answer != QMessageBox.Yes:
                return
        if not self.core.remove_category(cat_id):
            QMessageBox.information(self, "Can't delete",
                                    "The board needs at least one category.")

    def _edit_control(self, control_id: str) -> None:
        control = next((c for c in self.core.board["controls"] if c["id"] == control_id), None)
        if not control:
            return
        dlg = ControlDialog(self, control["param"], control["ptype"], existing=control)
        if dlg.exec() == QDialog.Accepted:
            self.core.update_control(control_id, **dlg.result_dict())

    # -- parameter dock ---------------------------------------------------------------

    def _refresh_params(self, params: dict[str, dict]) -> None:
        self.tree.clear()
        self.param_items.clear()
        for name in sorted(params):
            self._update_param_row(name, params[name]["ptype"], params[name]["value"])
        self._apply_filter()

    def _update_param_row(self, name: str, ptype: str, value: Any) -> None:
        item = self.param_items.get(name)
        if item is None:
            item = QTreeWidgetItem([name, ptype, _fmt(value)])
            item.setToolTip(0, name)  # long names elide in the stretch column
            item.setData(0, Qt.UserRole, name)
            item.setData(1, Qt.UserRole, ptype)
            self.tree.addTopLevelItem(item)
            self.param_items[name] = item
            self._apply_filter_item(item)
        else:
            item.setText(1, ptype)
            item.setText(2, _fmt(value))
            item.setData(1, Qt.UserRole, ptype)

    def _apply_filter(self) -> None:
        for item in self.param_items.values():
            self._apply_filter_item(item)

    def _apply_filter_item(self, item: QTreeWidgetItem) -> None:
        needle = self.search.text().lower().strip()
        item.setHidden(bool(needle) and needle not in item.text(0).lower())

    def _add_from_item(self, item: QTreeWidgetItem) -> None:
        """Double-click: add instantly with defaults to the last-used category.

        Fine-tuning (control type, range, invert, label) is done afterwards
        via the card's ⋯ → Edit — the defaults are right almost every time.
        """
        name = item.data(0, Qt.UserRole)
        ptype = item.data(1, Qt.UserRole) or "Float"
        if not self.core.avatar_id:
            QMessageBox.information(
                self, "No avatar", "Waiting for VRChat — load into an avatar first.")
            return
        self.core.add_control(name, self.core.default_kind(ptype),
                              category=self._last_category())

    def _param_dropped(self, param: str, ptype: str, cat_id: str, index: int) -> None:
        self._last_cat = cat_id
        self.core.add_control(param, self.core.default_kind(ptype),
                              category=cat_id, index=index)

    def _last_category(self) -> str:
        cats = self.core.board["categories"]
        ids = {c["id"] for c in cats}
        if getattr(self, "_last_cat", None) in ids:
            return self._last_cat
        return cats[0]["id"]

    # -- update check ---------------------------------------------------------------

    def _start_update_check(self) -> None:
        threading.Thread(target=self._check_update, name="update-check", daemon=True).start()

    def _check_update(self) -> None:
        try:
            req = urllib.request.Request(
                LATEST_API, headers={"Accept": "application/vnd.github+json",
                                     "User-Agent": "VRCParameterRelay"})
            with urllib.request.urlopen(req, timeout=6) as res:
                tag = json.loads(res.read().decode("utf-8")).get("tag_name", "")
        except Exception:
            return  # offline / rate-limited / API down — silently skip
        if tag and _is_newer(tag, __version__):
            self.update_found.emit(tag)

    def _show_update(self, tag: str) -> None:
        # user chose to stay on their version and silence reminders for this one
        if self.core.store.settings.get("skip_update_version") == tag:
            return
        self.update_label.setText(
            f'<a href="{RELEASES_URL}" style="color:#facc15; text-decoration:none;">'
            f'⬆ Update available: {tag} — get it here</a>')
        self.update_label.setVisible(True)
        UpdateDialog(self, tag).exec()

    def open_releases(self) -> None:
        QDesktopServices.openUrl(QUrl(RELEASES_URL))

    def skip_update(self, tag: str) -> None:
        self.core.store.set("skip_update_version", tag)
        self.update_label.setVisible(False)

    # -- sharing ------------------------------------------------------------------------

    def _show_guest_list(self) -> None:
        menu = QMenu(self)
        if not self.guest_names:
            menu.addAction("No guests connected").setEnabled(False)
        else:
            for name in self.guest_names:
                menu.addAction(name)
        menu.exec(self.guest_chip.mapToGlobal(self.guest_chip.rect().bottomLeft()))

    def selfhost_active(self) -> bool:
        """True when advanced settings put sharing in self-hosted (no-tunnel) mode."""
        settings = self.core.store.settings
        return bool(settings.get("advanced_link")
                    and settings.get("tunnel_provider") == "selfhost")

    def _toggle_pause(self) -> None:
        if self.core.sharing_enabled:
            self.core.set_sharing(False)   # emergency stop — guests stay connected
        elif (self.tunnel.state in ("downloading", "starting", "online")
                or self.selfhost_active()):
            self.core.set_sharing(True)
        else:
            self._open_share()             # nothing to pause — offer to start

    def _refresh_pause_btn(self) -> None:
        # self-hosted mode has no tunnel: the address is always reachable,
        # so pausing/resuming is available whenever sharing has a link
        tunnel_up = (self.tunnel.state in ("downloading", "starting", "online")
                     or self.selfhost_active())
        if self.core.sharing_enabled:
            self.pause_btn.setText("■ STOP")  # filled red — the emergency stop
            self.pause_btn.setProperty("state", "active")
            self.pause_btn.setToolTip(
                "Emergency stop — pause sharing.\n"
                "Guests stay connected but all their input is blocked.")
        elif tunnel_up:
            self.pause_btn.setText("▶ RESUME")
            self.pause_btn.setProperty("state", "paused")
            self.pause_btn.setToolTip("Sharing is paused — click to resume")
        else:
            self.pause_btn.setText("■ STOP")
            self.pause_btn.setProperty("state", "")
            self.pause_btn.setToolTip("Sharing is off")
        self.pause_btn.setEnabled(self.core.sharing_enabled or tunnel_up)
        self.pause_btn.style().unpolish(self.pause_btn)
        self.pause_btn.style().polish(self.pause_btn)
        if self.tray is not None:
            self._tray_pause.setText("Pause sharing" if self.core.sharing_enabled
                                     else "Resume sharing")
            self._tray_pause.setEnabled(self.core.sharing_enabled or tunnel_up)

    # -- settings / theme + font ----------------------------------------------------

    def _open_settings(self) -> None:
        # non-modal so the board keeps live-previewing behind it
        if getattr(self, "settings_dialog", None) is None:
            self.settings_dialog = SettingsDialog(self)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def _apply_style(self) -> None:
        theme = self.core.store.settings.get("theme", DEFAULT_THEME)
        font = self.core.store.settings.get("font") or DEFAULT_FONT
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_qss(theme, font))  # re-polishes every widget live
        self._refresh_avatar_header()  # the live dot uses the theme accent inline
        self.core.emit({"t": "style"})  # guests follow the host's look

    def _set_theme(self, key: str) -> None:
        if key == self.core.store.settings.get("theme"):
            return
        self.core.store.set("theme", key)
        self._apply_style()

    def _set_font(self, family: str) -> None:
        if family == self.core.store.settings.get("font"):
            return
        self.core.store.set("font", family)
        self._apply_style()

    def _open_share(self) -> None:
        self.share_dialog.show()
        self.share_dialog.raise_()
        self.share_dialog.activateWindow()

    def request_yolo(self, on: bool, widget) -> None:
        """Shared confirm flow for the header toggle and the Share dialog checkbox."""
        if on and not self.core.yolo_enabled:
            answer = QMessageBox.warning(
                self, "Enable YOLO mode?",
                "Everyone with the link will be able to see and change EVERY avatar "
                "parameter — not just your board, and category locks won't apply.\n\n"
                "It stays on until you turn it off, even after restarting the app. "
                "Enable?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                widget.blockSignals(True)
                widget.setChecked(False)
                widget.blockSignals(False)
                return
        self.core.set_yolo(on)


HELP_HTML = f"""
<h2 style="margin-top:0">Getting started</h2>
<p>Start VRChat with OSC enabled (<i>Action Menu → Options → OSC</i>). The app
finds it automatically and lists every parameter of your avatar in the right
panel (open/close with the green pull-tab). <b>Double-click</b> a parameter or
<b>drag it onto a category</b> to make a control — the type is picked
automatically, and you can fine-tune it via the card's ⋯ → Edit (label,
range, invert ⇄ for backwards logic).</p>

<h2>Boards, categories &amp; presets</h2>
<p>Controls live in categories. Drag cards by their ⠿ grip to rearrange or
move them; drag a whole category by the grip in its header. The 🔒 on a
category blocks <i>guests</i> from using it (you still can). Each category
can have its own accent colour — pick one under ⋯ → <i>Color</i>. Each
avatar can have several <b>presets</b> (separate board layouts) — switch
with the dropdown in the header, manage with the ⋯ next to it, and copy a
category to another preset via the category's ⋯ menu.</p>

<h2>Avatar library</h2>
<p>Give an avatar a name (the big field top-left) and it's kept for offline
editing: click ▾ to open any saved avatar without VRChat running. The badge
next to the name shows the state — green <b>● LIVE</b> means you're wearing
this avatar and controls are live; red <b>✎ OFFLINE</b> means you're only
editing, and nothing is sent to VRChat.</p>

<h2>Sharing</h2>
<p><b>Share → Start sharing</b> creates a public link (no port forwarding —
a Cloudflare quick tunnel; downloads itself on first use). Guests see only
your board — in the same theme and category colours you're using — and can
set a display name so you know who's connected — click
the <b>guests</b> chip to see the list (unnamed guests show as
<i>Anonymous&nbsp;#N</i>). The red <b>STOP</b> button above the board is an
emergency stop: it pauses everyone instantly without disconnecting them,
then turns into <b>RESUME</b>. <b>Reset link</b> invalidates every link you've
handed out. <i>Link settings</i> hides more under <b>advanced settings</b>:
a free permanent ngrok link, or fully <b>self-hosting</b> the guest page on
your own port and domain/DDNS name — with optional automatic port opening
(UPnP). Everything there is free; nothing ever bills you.</p>

<h2>⚡ YOLO mode</h2>
<p>The ⚡ YOLO button above the board gives guests the <i>full</i> parameter
list and control over everything, ignoring category locks (values stay
clamped to safe ranges). It stays on until you turn it off.</p>

<h2>Appearance</h2>
<p>The ⚙ gear in the top-right opens <b>Settings</b>, with two sections:
<b>Design</b> — the colour scheme (<i>Neon</i>, the brighter default, or
<i>Midnight</i>, a darker look with thinner borders) — and <b>Font</b>, a
live typeface picker (each row is shown in its own font). Changes apply
instantly and are remembered.</p>

<h2>Admin panel (edit from a browser)</h2>
<p><b>Share → Admin panel</b> gives you a link that opens the board editor in
a browser: the same avatars, presets, categories and controls as this
window, updating live in both directions. Handy on a second screen, from
the couch, or when the relay runs <code>--headless</code>. It has its own
secret, separate from the guest link — the guest link can never edit
anything, and this one grants full control, so keep it to yourself.
<b>New link</b> revokes it everywhere.</p>

<h2>Tray &amp; autostart</h2>
<p>Closing the window keeps the relay running in the <b>system tray</b> —
sharing stays up, and the tray menu has Open / Pause / Copy&nbsp;link /
Open&nbsp;board&nbsp;in&nbsp;browser / Quit (turn this off in ⚙ Settings).
There you can also enable <b>Start with Windows</b> or <b>Launch with
SteamVR</b>, both starting minimized to the tray. <code>--tray</code>
starts minimized manually; on systems without a tray it just opens the
window instead.</p>

<h2>Headless mode</h2>
<p>Start the exe with <code>--headless</code> and it runs without a window:
it connects to VRChat, loads your saved boards and starts sharing
automatically with whatever link you configured here (tunnel or
self-hosted, incl. UPnP). Perfect for auto-starting with Windows. Progress
goes to the log file in the data folder; board edits made in a normally
started window are picked up live.</p>

<h2>Data &amp; updates</h2>
<p>Boards and settings live in <code>%APPDATA%\\VRCParameterRelay</code>.
On startup the app checks GitHub for a newer release and offers a download
link — never installs anything by itself.</p>
"""


class HelpDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — Help")
        self.resize(560, 520)
        lay = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(HELP_HTML)
        lay.addWidget(browser)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignRight)


class SettingsDialog(QDialog):
    """App settings — design, font, startup behaviour; applied live."""

    def __init__(self, main: "MainWindow") -> None:
        super().__init__(main)
        self.main = main
        self.setWindowTitle(f"{APP_NAME} — Settings")
        self.setMinimumWidth(440)
        settings = main.core.store.settings

        lay = QVBoxLayout(self)
        lay.setSpacing(16)
        lay.setContentsMargins(16, 16, 16, 16)

        # -- Design (theme) --------------------------------------------------
        design = QGroupBox("Design")
        dl = QVBoxLayout(design)
        dl.setSpacing(6)
        dl.setContentsMargins(12, 8, 12, 10)
        hint_d = QLabel("The overall colour scheme and shapes.", objectName="SettingsHint")
        dl.addWidget(hint_d)
        self._theme_group = QButtonGroup(self)
        current_theme = settings.get("theme", DEFAULT_THEME)
        for key in THEME_ORDER:
            radio = QRadioButton(THEME_LABELS.get(key, key))
            radio.setChecked(key == current_theme)
            radio.toggled.connect(lambda on, k=key: on and self.main._set_theme(k))
            self._theme_group.addButton(radio)
            dl.addWidget(radio)
        lay.addWidget(design)

        # -- Font ------------------------------------------------------------
        font_box = QGroupBox("Font")
        fl = QVBoxLayout(font_box)
        fl.setSpacing(6)
        fl.setContentsMargins(12, 8, 12, 10)
        hint_f = QLabel("Typeface for the whole app — each row is shown in its own font.",
                        objectName="SettingsHint")
        hint_f.setWordWrap(True)
        fl.addWidget(hint_f)

        # the list is long — scroll it so the dialog stays a sane height
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMaximumHeight(300)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent;")
        scroll.viewport().setStyleSheet("background: transparent;")
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        self._font_group = QButtonGroup(self)
        current_font = settings.get("font") or DEFAULT_FONT
        checked_radio = None
        for group_title, items in FONT_GROUPS:
            head = QLabel(group_title.upper(), objectName="FontGroupHead")
            hl.addWidget(head)
            for family, note in items:
                radio = QRadioButton(f"{family}  —  {note}")
                radio.setChecked(family == current_font)
                if family == current_font:
                    checked_radio = radio
                # each row previews its own font; a widget stylesheet outranks
                # the app-wide one, so the preview survives a font change
                radio.setStyleSheet(
                    "QRadioButton { font-family: '%s'; font-size: 13px; }" % family)
                radio.toggled.connect(lambda on, f=family: on and self.main._set_font(f))
                self._font_group.addButton(radio)
                hl.addWidget(radio)
        hl.addStretch(1)
        scroll.setWidget(holder)
        fl.addWidget(scroll)
        if checked_radio is not None:
            QTimer.singleShot(0, lambda: scroll.ensureWidgetVisible(checked_radio))
        lay.addWidget(font_box)

        # -- Startup & tray ---------------------------------------------------
        from .. import autostart, steamvr

        startup = QGroupBox("Startup && tray")
        sl = QVBoxLayout(startup)
        sl.setSpacing(6)
        sl.setContentsMargins(12, 8, 12, 10)

        tray_check = QCheckBox("Keep running in the tray when the window is closed")
        tray_check.setChecked(bool(settings.get("close_to_tray", True)))
        tray_check.toggled.connect(
            lambda on: main.core.store.set("close_to_tray", bool(on)))
        if main.tray is None:
            tray_check.setEnabled(False)
            tray_check.setToolTip("No system tray available on this system")
        sl.addWidget(tray_check)

        self._startup_hint = QLabel("", objectName="SettingsHint")
        self._startup_hint.setWordWrap(True)

        def _mode_combo(current: str) -> QComboBox:
            combo = QComboBox()
            combo.addItem("minimized to tray", "tray")
            combo.addItem("with the window open", "window")
            idx = combo.findData(current if current in ("tray", "window") else "tray")
            combo.setCurrentIndex(max(0, idx))
            combo.setMinimumWidth(170)
            return combo

        system_name = "Windows" if sys.platform == "win32" else "the desktop"
        win_row = QHBoxLayout()
        win_check = QCheckBox(f"Start with {system_name}")
        win_check.setChecked(autostart.is_enabled())
        self.win_mode = _mode_combo(settings.get("autostart_mode", "tray"))
        win_row.addWidget(win_check)
        win_row.addWidget(self.win_mode)
        win_row.addStretch(1)

        def _apply_winstart() -> None:
            mode = self.win_mode.currentData()
            main.core.store.set("autostart_mode", mode)
            error = autostart.set_enabled(win_check.isChecked(), mode)
            self._startup_hint.setText(error)
        win_check.toggled.connect(lambda _on: _apply_winstart())
        # changing the mode only rewrites the entry when it's actually on
        self.win_mode.activated.connect(
            lambda _i: _apply_winstart() if win_check.isChecked()
            else main.core.store.set("autostart_mode", self.win_mode.currentData()))
        sl.addLayout(win_row)

        vr_row = QHBoxLayout()
        vr_check = QCheckBox("Launch with SteamVR")
        vr_check.setChecked(bool(settings.get("steamvr_autolaunch")))
        self.vr_mode = _mode_combo(settings.get("steamvr_mode", "tray"))
        vr_row.addWidget(vr_check)
        vr_row.addWidget(self.vr_mode)
        vr_row.addStretch(1)

        def _apply_steamvr() -> None:
            mode = self.vr_mode.currentData()
            main.core.store.set("steamvr_mode", mode)
            on = vr_check.isChecked()
            main.core.store.set("steamvr_autolaunch", on)
            error = steamvr.apply(on, mode)
            self._startup_hint.setText(
                error if error else
                ("Registered with SteamVR." if on else "Removed from SteamVR."))
        vr_check.toggled.connect(lambda _on: _apply_steamvr())
        self.vr_mode.activated.connect(
            lambda _i: _apply_steamvr() if vr_check.isChecked()
            else main.core.store.set("steamvr_mode", self.vr_mode.currentData()))
        sl.addLayout(vr_row)
        sl.addWidget(self._startup_hint)
        lay.addWidget(startup)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignRight)


class UpdateDialog(QDialog):
    """Startup notification when a newer release is on GitHub."""

    def __init__(self, main: "MainWindow", tag: str) -> None:
        super().__init__(main)
        self.main = main
        self.tag = tag
        self.setWindowTitle("Update available")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        msg = QLabel(
            f"<b>{APP_NAME} {tag}</b> is available.<br>"
            f"You're on v{__version__}.")
        msg.setTextFormat(Qt.RichText)
        lay.addWidget(msg)

        self.dont_remind = QCheckBox("Don't remind me again for this version")
        lay.addWidget(self.dont_remind)

        buttons = QDialogButtonBox()
        self.update_btn = buttons.addButton("Take me to the update",
                                            QDialogButtonBox.AcceptRole)
        self.update_btn.setObjectName("Primary")
        buttons.addButton("Remind me later", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._go)
        buttons.rejected.connect(self._later)
        lay.addWidget(buttons)

    def _apply_skip(self) -> None:
        if self.dont_remind.isChecked():
            self.main.skip_update(self.tag)

    def _go(self) -> None:
        self._apply_skip()
        self.main.open_releases()
        self.accept()

    def _later(self) -> None:
        self._apply_skip()
        self.reject()


def _fmt(value: Any) -> str:
    if value is None:
        return "–"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _version_tuple(v: str) -> tuple:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums) if nums else (0,)


def _is_newer(remote: str, local: str) -> bool:
    return _version_tuple(remote) > _version_tuple(local)
