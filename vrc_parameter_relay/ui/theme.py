"""Green-on-black theme shared by the whole app."""

ACCENT = "#31f272"
EDGE = "#36a35c"  # neon green border family

QSS = """
* { font-family: 'Segoe UI', sans-serif; font-size: 13px; }

QMainWindow, QDialog { background: #0a0c0a; }
QWidget { color: #e6efe8; }

#Header { background: #0f120f; border-bottom: 1px solid %(edge)s; }
#BoardName {
    background: transparent; border: none; font-size: 19px; font-weight: 600;
    padding: 2px 4px; color: #f2f8f3;
}
#BoardName:focus { background: #16211a; border-radius: 6px; }
#AvatarId { color: #5f6f63; font-size: 11px; }

QLabel#Chip {
    background: #131a15; border: 1px solid %(edge)s; border-radius: 10px;
    padding: 0 12px; color: #94a698; font-size: 12px;
}
QLabel#Chip[state="ok"] { color: #3af0a0; border-color: #22c55e; }
QLabel#Chip[state="bad"] { color: #f87171; border-color: #542a2a; }

QPushButton {
    background: transparent; border: 1px solid %(edge)s; border-radius: 8px;
    padding: 7px 14px; color: #e6efe8;
}
QPushButton:hover { background: #142418; }
QPushButton:pressed { background: #0f150f; }
QPushButton#Primary {
    background: %(accent)s; border-color: %(accent)s;
    font-weight: 650; color: #04150b;
}
QPushButton#Primary:hover { background: #62ff97; }
QPushButton#Danger { color: #f87171; border-color: #542a2a; }

QPushButton#SyncBtn {
    font-size: 13px; font-weight: 600; padding: 5px 14px;
    color: #cfe3d4; border-color: %(edge)s;
}
QPushButton#SyncBtn:hover { background: #142418; color: #eafff0; }

QLabel#LiveBadge {
    border-radius: 9px; padding: 1px 9px; font-size: 11px; font-weight: 700;
}
QLabel#LiveBadge[state="live"] {
    background: #10331d; color: #3af0a0; border: 1px solid #22c55e;
}
QLabel#LiveBadge[state="offline"] {
    background: #331014; color: #ff6b81; border: 1px solid #a52a38;
}

QPushButton#PauseBtn {
    font-size: 13px; font-weight: 700; padding: 6px 18px; letter-spacing: 1px;
    color: #ff6b81; border-color: #a52a38; background: transparent;
}
QPushButton#PauseBtn:hover { background: #2a1216; }
QPushButton#PauseBtn[state="active"] {
    color: #ffdfe4; border-color: #d43048; background: #7a1226;
}
QPushButton#PauseBtn[state="active"]:hover { background: #8f1730; }
QPushButton#PauseBtn[state="paused"] {
    color: #facc15; border-color: #8a6d1a; background: #2a2410;
}
QPushButton#PauseBtn[state="paused"]:hover { background: #3a3216; }
QPushButton#PauseBtn:disabled { color: #4a3f42; border-color: #2d2226; background: transparent; }

QPushButton#ChipBtn {
    background: #131a15; border: 1px solid %(edge)s; border-radius: 10px;
    padding: 0 12px; color: #94a698; font-size: 12px;
}
QPushButton#ChipBtn:hover { background: #142418; color: #b8ccbb; }

QPushButton#HelpBtn { font-weight: 700; padding: 7px 0; }

QToolButton#AvatarMenu {
    border: none; color: #3fce74; font-size: 13px; font-weight: 700;
    padding: 2px 4px;
}
QToolButton#AvatarMenu:hover { color: #7dffab; }

QComboBox#PresetCombo { padding: 5px 10px; }

QPushButton#YoloToggle {
    border: 1px solid #3a2f1c; color: #8f7f60; font-weight: 600; padding: 7px 12px;
}
QPushButton#YoloToggle:hover { background: #1d1810; }
QPushButton#YoloToggle:checked {
    background: #7a4410; border-color: #c96a10; color: #ffd9a8;
}
QPushButton#YoloToggle:checked:hover { background: #8a4d12; }

QMainWindow::separator {
    width: 7px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #0a0c0a, stop:0.42 #0a0c0a, stop:0.5 %(edge)s,
        stop:0.58 #0a0c0a, stop:1 #0a0c0a);
}
QMainWindow::separator:hover { background: %(accent)s; }

QPushButton#DockToggle {
    border: 1px solid %(edge)s; border-right: none;
    border-top-left-radius: 10px; border-bottom-left-radius: 10px;
    border-top-right-radius: 0; border-bottom-right-radius: 0;
    background: #143a22; color: %(accent)s; font-weight: 800; font-size: 15px;
    padding: 0;
}
QPushButton#DockToggle:hover { background: #1c8a3d; color: #eafff0; }

#DockTitle { background: #0f120f; border-bottom: 1px solid %(edge)s; }
QLabel#DockTitleText { font-weight: 600; font-size: 14px; color: #cfe3d4; }

QScrollArea { border: none; background: transparent; }
#BoardArea { background: #0a0c0a; }

QFrame#Card {
    background: #121712; border: 1px solid %(edge)s; border-radius: 12px;
}
QLabel#CardLabel { font-weight: 600; font-size: 13px; }
QLabel#CardValue { color: #7f8f81; font-size: 11px; }
QLabel#CardGrip { color: #3fce74; font-size: 14px; }
QLabel#CardInv { color: #f0a35e; font-size: 13px; font-weight: 700; }
QToolButton#CardMenu { border: none; color: #3fce74; font-size: 16px; padding: 0 4px; }
QToolButton#CardMenu:hover { color: #7dffab; }

QFrame#Category {
    background: #0e120e; border: 1px solid %(edge)s; border-radius: 14px;
}
QWidget#CatHeader { background: #1c8a3d; border-radius: 9px; }
QLabel#CatGrip { color: #eafaee; font-size: 14px; padding: 0 2px; }
QLineEdit#CatName {
    background: transparent; border: none; font-size: 14px; font-weight: 600;
    padding: 2px 4px; color: #eafaee;
}
QLineEdit#CatName:focus { background: #17703a; border-radius: 6px; }
QToolButton#CatLock { border: none; font-size: 14px; padding: 0 6px; }
QLabel#CatHint {
    color: #5d7a63; font-size: 12px; padding: 16px;
    border: 1px dashed %(edge)s; border-radius: 9px;
}

QPushButton#Switch {
    background: #a51830; border: none; border-radius: 14px;
    min-width: 120px; font-weight: 650; color: #ffd9de;
}
QPushButton#Switch:checked { background: #1cf26b; color: #04150b; font-weight: 650; }
QPushButton#Switch:disabled { color: #8a6d72; }

QPushButton#Push {
    background: rgba(49, 242, 114, 0.15); border: 1px solid %(accent)s;
    border-radius: 9px; font-weight: 600;
}
QPushButton#Push:pressed { background: %(accent)s; color: #04150b; }

QSlider::groove:horizontal { height: 5px; background: #1d5a32; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; background: %(accent)s;
}
QSlider::sub-page:horizontal { background: #22d968; border-radius: 2px; }

QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox {
    background: #121a13; border: 1px solid %(edge)s; border-radius: 7px; padding: 5px 8px;
    selection-background-color: %(accent)s;
    selection-color: #04150b;
}
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #121a13; border: 1px solid %(edge)s; }

QDockWidget { color: #94a698; }

QTreeWidget {
    background: #0f120f; border: none; alternate-background-color: #121712;
}
QTreeWidget::item { height: 26px; }
QTreeWidget::item:selected { background: #1b5a30; }
QHeaderView::section {
    background: #0f120f; border: none; border-bottom: 1px solid %(edge)s;
    padding: 6px; color: #7f8f81;
}

QStatusBar {
    background: #0f120f; color: #5f6f63; font-size: 11px;
    border-top: 1px solid %(edge)s;
}
QStatusBar::item { border: none; }
QMenu { background: #121712; border: 1px solid %(edge)s; padding: 4px; }
QMenu::item { padding: 6px 22px; border-radius: 5px; }
QMenu::item:selected { background: #16311e; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #245c38; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: %(accent)s; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QProgressBar {
    background: #121a13; border: 1px solid %(edge)s; border-radius: 6px;
    text-align: center; color: #e6efe8;
}
QProgressBar::chunk { background: %(accent)s; border-radius: 5px; }
""" % {"accent": ACCENT, "edge": EDGE}
