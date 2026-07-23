"""App themes.

Two green-on-black looks, switchable at runtime from the header gear:
  * "broker"  (default) — the original shapes and font, recoloured with the
                GPU-broker dashboard palette: near-black blue-tinted
                background, muted hairline borders, and vibrant accents
                (green #4af58c, red #ff5561, amber #ffb454, blue #7aa7ff,
                cyan #56d9f2, purple #d78cff).
  * "neon"    — the original brighter neon-green style, unchanged.

Categories can each carry one of the vibrant colours (the "catcolor"
property on the frame); the variants for both themes are generated below.
"""

# Accent per theme, for the few inline rich-text bits the UI draws itself.
# "neon"  = the brighter, thicker classic look (default).
# "broker" = "Midnight": darker background, thinner/muted borders.
ACCENTS = {"broker": "#4af58c", "neon": "#31f272"}
THEME_LABELS = {"neon": "Neon (default)", "broker": "Midnight"}
THEME_ORDER = ["neon", "broker"]
DEFAULT_THEME = "neon"

# Selectable UI fonts, grouped by general type for the picker. Missing system
# families fall back to Segoe UI, so it's safe to offer Office/Win11 fonts;
# the "bundled" group ships inside the exe and is always present.
# (group title, [(family, one-line note), ...])
DEFAULT_FONT = "Segoe UI"
FONT_GROUPS = [
    ("Clean & rounded", [
        ("Segoe UI", "clean modern sans — the default"),
        ("Century Gothic", "very round, geometric"),
    ]),
    ("Sci-fi & techno (bundled)", [
        ("Orbitron", "wide geometric, slashed zeros"),
        ("Aldrich", "square techno"),
        ("Audiowide", "retro-futuristic, rounded"),
    ]),
]
# flat list of (family, note), for anything that just needs every choice
FONT_CHOICES = [item for _, items in FONT_GROUPS for item in items]

# Vibrant per-category palette (GPU-broker hues): key -> (vibrant, mid, tint)
#   vibrant = text/accent · mid = border tone · tint = dark fill
CATEGORY_PALETTE = {
    "green":  ("#4af58c", "#2f5e3f", "#10291a"),
    "blue":   ("#7aa7ff", "#3a5384", "#131c30"),
    "cyan":   ("#56d9f2", "#2f6b7a", "#0e2229"),
    "yellow": ("#ffb454", "#6b5320", "#291f0c"),
    "red":    ("#ff5561", "#5c2228", "#2a1014"),
    "purple": ("#d78cff", "#5e3f78", "#221430"),
}
CATEGORY_COLOR_LABELS = {
    "green": "Green", "blue": "Blue", "cyan": "Cyan",
    "yellow": "Yellow", "red": "Red", "purple": "Purple",
}


def _broker_cat_variants() -> str:
    """Broker: dark-tinted header bar, vibrant title, hue-toned frame border.

    The control cards inside follow the group colour: card borders take the
    mid tone, grips and ⋯ menus the vibrant hue.
    """
    out = []
    for key, (vib, mid, tint) in CATEGORY_PALETTE.items():
        out.append(f"""
QFrame#Category[catcolor="{key}"] {{ border: 1px solid {mid}; }}
QFrame#Category[catcolor="{key}"] QWidget#CatHeader {{ background: {tint}; }}
QFrame#Category[catcolor="{key}"] QLineEdit#CatName {{ color: {vib}; }}
QFrame#Category[catcolor="{key}"] QLineEdit#CatName:focus {{ background: {mid}; }}
QFrame#Category[catcolor="{key}"] QLabel#CatGrip {{ color: {vib}; }}
QFrame#Category[catcolor="{key}"] QFrame#Card {{ border: 1px solid {mid}; }}
QFrame#Category[catcolor="{key}"] QLabel#CardGrip {{ color: {vib}; }}
QFrame#Category[catcolor="{key}"] QToolButton#CardMenu {{ color: {vib}; }}
QFrame#Category[catcolor="{key}"] QLabel#CatHint {{ border: 1px dashed {mid}; }}
""")
    return "".join(out)


# Neon: bright filled header bars per hue (green == the classic look).
_NEON_CAT_BARS = {
    "green":  ("#1c8a3d", "#17703a", "#36a35c"),
    "blue":   ("#1e5fa0", "#194f85", "#3b6ea3"),
    "cyan":   ("#15808f", "#116a77", "#2e93a3"),
    "yellow": ("#a06a14", "#855812", "#b3813a"),
    "red":    ("#9c2531", "#821f29", "#b04552"),
    "purple": ("#7a3fa8", "#653389", "#9159ba"),
}


def _neon_cat_variants() -> str:
    out = []
    for key, (bar, focus, border) in _NEON_CAT_BARS.items():
        vib = CATEGORY_PALETTE[key][0]
        out.append(f"""
QFrame#Category[catcolor="{key}"] {{ border: 1px solid {border}; }}
QFrame#Category[catcolor="{key}"] QWidget#CatHeader {{ background: {bar}; }}
QFrame#Category[catcolor="{key}"] QLineEdit#CatName:focus {{ background: {focus}; }}
QFrame#Category[catcolor="{key}"] QFrame#Card {{ border: 1px solid {border}; }}
QFrame#Category[catcolor="{key}"] QLabel#CardGrip {{ color: {vib}; }}
QFrame#Category[catcolor="{key}"] QToolButton#CardMenu {{ color: {vib}; }}
QFrame#Category[catcolor="{key}"] QLabel#CatHint {{ border: 1px dashed {border}; }}
""")
    return "".join(out)


# --------------------------------------------------------------------------
# Classic neon (the original look)
# --------------------------------------------------------------------------
_NEON_ACCENT = "#31f272"
_NEON_EDGE = "#36a35c"

NEON_QSS = """
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

QToolButton#GearBtn { border: none; color: #94a698; font-size: 16px; padding: 2px 6px; }
QToolButton#GearBtn:hover { color: #7dffab; }

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
    width: 4px;
    background: %(edge)s;  /* solid line so the pull-tab meets it flush */
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
QTreeWidget::item { height: 26px; padding-left: 6px; }
QTreeWidget::item:selected { background: #1b5a30; }
QHeaderView::section {
    background: #0f120f; border: none; border-bottom: 1px solid %(edge)s;
    padding: 6px 8px; color: #7f8f81;
}
QHeaderView::section:first { padding-left: 14px; }

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

QGroupBox {
    border: 1px solid %(edge)s; border-radius: 10px; margin-top: 12px;
    padding: 14px 12px 8px; font-weight: 700; color: %(accent)s;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 12px; padding: 0 6px;
}
QLabel#SettingsHint { color: #94a698; font-size: 11px; font-weight: 400; }
QLabel#FontGroupHead {
    color: %(accent)s; font-size: 10px; font-weight: 700;
    padding: 8px 0 1px; border-bottom: 1px solid %(edge)s; margin-bottom: 3px;
}
QRadioButton { padding: 4px 2px; color: #e6efe8; }
QRadioButton::indicator { width: 14px; height: 14px; }
""" % {"accent": _NEON_ACCENT, "edge": _NEON_EDGE} + _neon_cat_variants()


# --------------------------------------------------------------------------
# Broker (default) — the neon shapes/font with the GPU-broker palette:
#   bg #07090c · panel #0b0f0c · hairline border #243029 · accent #4af58c
#   red #ff5561 · amber #ffb454 · blue #7aa7ff · cyan #56d9f2 · purple #d78cff
#   text #c9d4cc · dim #5f6f64
# --------------------------------------------------------------------------
BROKER_QSS = """
* { font-family: 'Segoe UI', sans-serif; font-size: 13px; }

QMainWindow, QDialog { background: #07090c; }
QWidget { color: #c9d4cc; }

#Header { background: #090d0a; border-bottom: 1px solid #243029; }
#BoardName {
    background: transparent; border: none; font-size: 19px; font-weight: 600;
    padding: 2px 4px; color: #4af58c;
}
#BoardName:focus { background: #0e1712; border-radius: 6px; }
#AvatarId { color: #5f6f64; font-size: 11px; }

QLabel#Chip {
    background: #0b0f0c; border: 1px solid #243029; border-radius: 10px;
    padding: 0 12px; color: #7c8a80; font-size: 12px;
}
QLabel#Chip[state="ok"] { color: #4af58c; border-color: #2f5e3f; }
QLabel#Chip[state="bad"] { color: #ff5561; border-color: #5c2228; }

QPushButton {
    background: transparent; border: 1px solid #243029; border-radius: 8px;
    padding: 7px 14px; color: #c9d4cc;
}
QPushButton:hover { background: #0d1712; border-color: #4af58c; color: #4af58c; }
QPushButton:pressed { background: #0b110d; }
QPushButton#Primary {
    background: #4af58c; border-color: #4af58c;
    font-weight: 650; color: #04120a;
}
QPushButton#Primary:hover { background: #7dffb0; border-color: #7dffb0; color: #04120a; }
QPushButton#Danger { color: #ff5561; border-color: #5c2228; }
QPushButton#Danger:hover { background: #170a0c; border-color: #ff5561; color: #ff5561; }

QPushButton#SyncBtn {
    font-size: 13px; font-weight: 600; padding: 5px 14px;
    color: #9fb3a6; border-color: #243029;
}
QPushButton#SyncBtn:hover { background: #0d1712; color: #4af58c; border-color: #4af58c; }

QLabel#LiveBadge {
    border-radius: 9px; padding: 1px 9px; font-size: 11px; font-weight: 700;
}
QLabel#LiveBadge[state="live"] {
    background: #10291a; color: #4af58c; border: 1px solid #2f5e3f;
}
QLabel#LiveBadge[state="offline"] {
    background: #2a1014; color: #ff5561; border: 1px solid #5c2228;
}

QPushButton#PauseBtn {
    font-size: 13px; font-weight: 700; padding: 6px 18px; letter-spacing: 1px;
    color: #ff5561; border-color: #5c2228; background: transparent;
}
QPushButton#PauseBtn:hover { background: #170a0c; }
QPushButton#PauseBtn[state="active"] {
    color: #ffe1e5; border-color: #ff5561; background: #6e141f;
}
QPushButton#PauseBtn[state="active"]:hover { background: #86202c; }
QPushButton#PauseBtn[state="paused"] {
    color: #ffb454; border-color: #6b5320; background: #241a0a;
}
QPushButton#PauseBtn[state="paused"]:hover { background: #322611; }
QPushButton#PauseBtn:disabled { color: #3f4a42; border-color: #1c2620; background: transparent; }

QPushButton#ChipBtn {
    background: #0b0f0c; border: 1px solid #243029; border-radius: 10px;
    padding: 0 12px; color: #7c8a80; font-size: 12px;
}
QPushButton#ChipBtn:hover { background: #0d1712; color: #4af58c; border-color: #4af58c; }

QPushButton#HelpBtn { font-weight: 700; padding: 7px 0; }

QToolButton#AvatarMenu {
    border: none; color: #4af58c; font-size: 13px; font-weight: 700;
    padding: 2px 4px;
}
QToolButton#AvatarMenu:hover { color: #7dffb0; }

QToolButton#GearBtn { border: none; color: #7c8a80; font-size: 16px; padding: 2px 6px; }
QToolButton#GearBtn:hover { color: #4af58c; }

QComboBox#PresetCombo { padding: 5px 10px; }

QPushButton#YoloToggle {
    border: 1px solid #4a3a1c; color: #b0895a; font-weight: 600; padding: 7px 12px;
}
QPushButton#YoloToggle:hover { background: #1a1408; border-color: #ffb454; color: #ffb454; }
QPushButton#YoloToggle:checked {
    background: #6b4310; border-color: #ffb454; color: #ffe6c2;
}
QPushButton#YoloToggle:checked:hover { background: #7c4f14; }

QMainWindow::separator {
    width: 4px;
    background: #243029;  /* solid line so the pull-tab meets it flush */
}
QMainWindow::separator:hover { background: #4af58c; }

QPushButton#DockToggle {
    border: 1px solid #2f5e3f; border-right: none;
    border-top-left-radius: 10px; border-bottom-left-radius: 10px;
    border-top-right-radius: 0; border-bottom-right-radius: 0;
    background: #10291a; color: #4af58c; font-weight: 800; font-size: 15px;
    padding: 0;
}
QPushButton#DockToggle:hover { background: #1c5f3a; color: #dffbe9; }

#DockTitle { background: #090d0a; border-bottom: 1px solid #243029; }
QLabel#DockTitleText { font-weight: 600; font-size: 14px; color: #4af58c; }

QScrollArea { border: none; background: transparent; }
#BoardArea { background: #07090c; }

QFrame#Card {
    background: #0b0f0c; border: 1px solid #243029; border-radius: 12px;
}
QLabel#CardLabel { font-weight: 600; font-size: 13px; }
QLabel#CardValue { color: #5f6f64; font-size: 11px; }
QLabel#CardGrip { color: #4a8f66; font-size: 14px; }
QLabel#CardInv { color: #ffb454; font-size: 13px; font-weight: 700; }
QToolButton#CardMenu { border: none; color: #4af58c; font-size: 16px; padding: 0 4px; }
QToolButton#CardMenu:hover { color: #7dffb0; }

QFrame#Category {
    background: #090c0a; border: 1px solid #2f5e3f; border-radius: 14px;
}
QWidget#CatHeader { background: #10291a; border-radius: 9px; }
QLabel#CatGrip { color: #4af58c; font-size: 14px; padding: 0 2px; }
QLineEdit#CatName {
    background: transparent; border: none; font-size: 14px; font-weight: 600;
    padding: 2px 4px; color: #4af58c;
}
QLineEdit#CatName:focus { background: #2f5e3f; border-radius: 6px; }
QToolButton#CatLock { border: none; font-size: 14px; padding: 0 6px; }
QLabel#CatHint {
    color: #5d7a63; font-size: 12px; padding: 16px;
    border: 1px dashed #243029; border-radius: 9px;
}

QPushButton#Switch {
    background: #a32633; border: none; border-radius: 14px;
    min-width: 120px; font-weight: 650; color: #ffe1e5;
}
QPushButton#Switch:checked { background: #4af58c; color: #04120a; font-weight: 650; }
QPushButton#Switch:disabled { color: #8a6d72; }

QPushButton#Push {
    background: rgba(74, 245, 140, 0.15); border: 1px solid #4af58c;
    border-radius: 9px; font-weight: 600; color: #4af58c;
}
QPushButton#Push:pressed { background: #4af58c; color: #04120a; }

QSlider::groove:horizontal { height: 5px; background: #1b3326; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; background: #4af58c;
}
QSlider::sub-page:horizontal { background: #35b56a; border-radius: 2px; }

QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox {
    background: #0a0e0b; border: 1px solid #243029; border-radius: 7px; padding: 5px 8px;
    selection-background-color: #4af58c;
    selection-color: #04120a;
}
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: #0a0e0b; border: 1px solid #243029; selection-background-color: #14311f;
}

QDockWidget { color: #7c8a80; }

QTreeWidget {
    background: #090d0a; border: none; alternate-background-color: #0c110e;
}
QTreeWidget::item { height: 26px; padding-left: 6px; }
QTreeWidget::item:selected { background: #14311f; }
QHeaderView::section {
    background: #090d0a; border: none; border-bottom: 1px solid #243029;
    padding: 6px 8px; color: #6f8074;
}
QHeaderView::section:first { padding-left: 14px; }

QStatusBar {
    background: #090d0a; color: #5f6f64; font-size: 11px;
    border-top: 1px solid #243029;
}
QStatusBar::item { border: none; }
QMenu { background: #0b0f0c; border: 1px solid #243029; padding: 4px; }
QMenu::item { padding: 6px 22px; border-radius: 5px; }
QMenu::item:selected { background: #14311f; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #2f4436; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #4af58c; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QProgressBar {
    background: #0a0e0b; border: 1px solid #243029; border-radius: 6px;
    text-align: center; color: #c9d4cc;
}
QProgressBar::chunk { background: #4af58c; border-radius: 5px; }

QGroupBox {
    border: 1px solid #2f5e3f; border-radius: 10px; margin-top: 12px;
    padding: 14px 12px 8px; font-weight: 700; color: #4af58c;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 12px; padding: 0 6px;
}
QLabel#SettingsHint { color: #7c8a80; font-size: 11px; font-weight: 400; }
QLabel#FontGroupHead {
    color: #4af58c; font-size: 10px; font-weight: 700;
    padding: 8px 0 1px; border-bottom: 1px solid #243029; margin-bottom: 3px;
}
QRadioButton { padding: 4px 2px; color: #c9d4cc; }
QRadioButton::indicator { width: 14px; height: 14px; }
""" + _broker_cat_variants()


THEMES = {"broker": BROKER_QSS, "neon": NEON_QSS}


def build_qss(theme: str = DEFAULT_THEME, font: str = "") -> str:
    """Full stylesheet for a theme, optionally overriding the UI font family.

    The font rule is appended last: in Qt style sheets a later rule of equal
    specificity wins per-property, so it overrides only font-family and leaves
    the theme's font sizes intact.
    """
    qss = THEMES.get(theme, THEMES[DEFAULT_THEME])
    if font:
        qss += "\n* { font-family: '%s', 'Segoe UI', sans-serif; }\n" % font
    return qss


def accent_of(theme: str = DEFAULT_THEME) -> str:
    """Accent colour for a theme, for inline rich-text the UI draws itself."""
    return ACCENTS.get(theme, ACCENTS[DEFAULT_THEME])


def load_bundled_fonts() -> None:
    """Register the app's bundled .ttf fonts with Qt.

    Fonts shipped in assets/fonts (e.g. Orbitron) don't depend on the user
    having them installed. Call once, after the QApplication exists.
    """
    from PySide6.QtGui import QFontDatabase

    from .. import resource_path
    font_dir = resource_path("assets/fonts")
    try:
        files = sorted(font_dir.glob("*.ttf")) + sorted(font_dir.glob("*.otf"))
    except OSError:
        return
    for path in files:
        QFontDatabase.addApplicationFont(str(path))


# Backwards-compat: some tools import QSS directly (defaults to the broker look).
QSS = build_qss()
