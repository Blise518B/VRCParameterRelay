"""Reusable UI pieces: flow layout, control cards, category boxes."""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QMimeData, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QMenu,
    QPushButton, QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget,
    QWidgetItem,
)

SLIDER_STEPS = 1000
MIME_CONTROL = "application/x-vrc-parameter-relay-control"
CONTROL_H = 34  # uniform height for every control widget on a card


class FlowLayout(QLayout):
    """Left-to-right wrap layout (from the Qt flow layout example)."""

    def __init__(self, parent: Optional[QWidget] = None, margin: int = 14, spacing: int = 12) -> None:
        super().__init__(parent)
        self._items: list[QWidgetItem] = []
        self.setContentsMargins(margin, margin, margin, margin)
        self._spacing = spacing

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, dry=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _arrange(self, rect: QRect, dry: bool) -> int:
        m = self.contentsMargins()
        x, y = rect.x() + m.left(), rect.y() + m.top()
        line_height = 0
        right = rect.right() - m.right()
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > right and line_height > 0:
                x = rect.x() + m.left()
                y += line_height + self._spacing
                line_height = 0
            if not dry:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_height = max(line_height, hint.height())
        return y + line_height + m.bottom() - rect.y()


class ControlCard(QFrame):
    """One control on the board. Draggable between categories."""

    set_value = Signal(str, object)          # control_id, value
    edit_requested = Signal(str)             # control_id
    remove_requested = Signal(str)

    def __init__(self, control: dict[str, Any], value: Any = None) -> None:
        super().__init__()
        self.control = control
        self.setObjectName("Card")
        self.setFixedWidth(210)
        self._updating = False
        self._press_pos: QPoint | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 8, 12)
        root.setSpacing(8)

        top = QHBoxLayout()
        grip = QLabel("⠿")
        grip.setObjectName("CardGrip")
        grip.setToolTip("Drag to another category")
        grip.setCursor(Qt.OpenHandCursor)
        top.addWidget(grip)
        self.label = QLabel(control.get("label") or control["param"])
        self.label.setObjectName("CardLabel")
        self.label.setToolTip(f'{control["param"]}  ({control["ptype"]})')
        top.addWidget(self.label, 1)
        menu_btn = QToolButton()
        menu_btn.setObjectName("CardMenu")
        menu_btn.setText("⋯")
        menu_btn.setCursor(Qt.PointingHandCursor)
        menu_btn.clicked.connect(self._show_menu)
        top.addWidget(menu_btn)
        root.addLayout(top)

        kind = control["kind"]
        self.value_label: Optional[QLabel] = None
        if kind == "toggle":
            self._build_toggle(root)
        elif kind == "button":
            self._build_button(root)
        elif kind == "slider":
            self._build_slider(root)
        else:
            self._build_int(root)

        if value is not None:
            self.update_value(value)

    # -- builders -----------------------------------------------------------

    def _build_toggle(self, root: QVBoxLayout) -> None:
        self.switch = QPushButton("OFF")
        self.switch.setObjectName("Switch")
        self.switch.setCheckable(True)
        self.switch.setCursor(Qt.PointingHandCursor)
        self.switch.setFixedHeight(CONTROL_H)
        self.switch.toggled.connect(self._on_toggle)
        root.addWidget(self.switch)

    def _on_toggle(self, checked: bool) -> None:
        self.switch.setText("ON" if checked else "OFF")
        if not self._updating:
            self.set_value.emit(self.control["id"], checked)

    def _build_button(self, root: QVBoxLayout) -> None:
        btn = QPushButton("Hold")
        btn.setObjectName("Push")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(CONTROL_H)
        btn.pressed.connect(lambda: self.set_value.emit(self.control["id"], True))
        btn.released.connect(lambda: self.set_value.emit(self.control["id"], False))
        root.addWidget(btn)

    def _build_slider(self, root: QVBoxLayout) -> None:
        self.vmin = float(self.control.get("min", 0))
        self.vmax = float(self.control.get("max", 1))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, SLIDER_STEPS)
        self.slider.setFixedHeight(CONTROL_H)
        self.value_label = QLabel("–")
        self.value_label.setObjectName("CardValue")
        self.value_label.setFixedWidth(36)
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._throttle = QTimer(self, interval=60, singleShot=True)
        self._throttle.timeout.connect(self._send_slider)
        self.slider.valueChanged.connect(self._on_slider)
        self.slider.sliderReleased.connect(self._send_slider)
        row = QHBoxLayout()  # value sits beside the slider so all cards match height
        row.setSpacing(6)
        row.addWidget(self.slider, 1)
        row.addWidget(self.value_label)
        root.addLayout(row)

    def _slider_float(self) -> float:
        return self.vmin + (self.slider.value() / SLIDER_STEPS) * (self.vmax - self.vmin)

    def _on_slider(self) -> None:
        v = self._slider_float()
        self.value_label.setText(f"{v:.2f}")
        if not self._updating and not self._throttle.isActive():
            self._throttle.start()

    def _send_slider(self) -> None:
        if not self._updating:
            self.set_value.emit(self.control["id"], self._slider_float())

    def _build_int(self, root: QVBoxLayout) -> None:
        row = QHBoxLayout()
        self.spin = QSpinBox()
        self.spin.setRange(int(self.control.get("min", 0)), int(self.control.get("max", 255)))
        self.spin.setButtonSymbols(QSpinBox.PlusMinus)
        self.spin.setFixedHeight(CONTROL_H)
        self._debounce = QTimer(self, interval=150, singleShot=True)
        self._debounce.timeout.connect(
            lambda: self.set_value.emit(self.control["id"], self.spin.value()))
        self.spin.valueChanged.connect(self._on_spin)
        row.addWidget(self.spin)
        root.addLayout(row)

    def _on_spin(self) -> None:
        if not self._updating:
            self._debounce.start()

    # -- inbound updates ------------------------------------------------------

    def update_value(self, value: Any) -> None:
        self._updating = True
        try:
            kind = self.control["kind"]
            if kind == "toggle":
                self.switch.setChecked(bool(value))
            elif kind == "slider":
                if not self.slider.isSliderDown():
                    span = (self.vmax - self.vmin) or 1
                    self.slider.setValue(round((float(value) - self.vmin) / span * SLIDER_STEPS))
                    self.value_label.setText(f"{float(value):.2f}")
            elif kind == "int":
                if not self.spin.hasFocus():
                    self.spin.setValue(int(value))
        except (TypeError, ValueError):
            pass
        finally:
            self._updating = False

    # -- menu ----------------------------------------------------------------

    def _show_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Edit…", lambda: self.edit_requested.emit(self.control["id"]))
        menu.addSeparator()
        menu.addAction("Remove", lambda: self.remove_requested.emit(self.control["id"]))
        menu.exec(self.mapToGlobal(QPoint(self.width() - 10, 30)))

    # -- drag source ------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (self._press_pos is not None
                and (event.position().toPoint() - self._press_pos).manhattanLength()
                >= QApplication.startDragDistance()):
            mime = QMimeData()
            mime.setData(MIME_CONTROL, self.control["id"].encode())
            drag = QDrag(self)
            drag.setMimeData(mime)
            drag.setPixmap(self.grab())
            drag.setHotSpot(self._press_pos)
            self._press_pos = None
            drag.exec(Qt.MoveAction)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._press_pos = None
        super().mouseReleaseEvent(event)


class CategoryBox(QFrame):
    """Named group of controls with a guest lock; drop target for cards."""

    renamed = Signal(str, str)               # cat_id, new name
    lock_toggled = Signal(str, bool)         # cat_id, locked
    delete_requested = Signal(str)           # cat_id
    control_dropped = Signal(str, str, int)  # control_id, cat_id, index

    def __init__(self, category: dict[str, Any]) -> None:
        super().__init__()
        self.category = category
        self.setObjectName("Category")
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 10)
        root.setSpacing(8)

        header = QWidget(objectName="CatHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 5, 6, 5)
        self.name_edit = QLineEdit(category.get("name") or "")
        self.name_edit.setObjectName("CatName")
        self.name_edit.setPlaceholderText("Category name")
        self.name_edit.editingFinished.connect(
            lambda: self.renamed.emit(self.category["id"], self.name_edit.text()))
        hl.addWidget(self.name_edit, 1)

        self.lock_btn = QToolButton(objectName="CatLock")
        self.lock_btn.setCheckable(True)
        self.lock_btn.setChecked(bool(category.get("locked")))
        self.lock_btn.setText("🔒" if category.get("locked") else "🔓")
        self.lock_btn.setToolTip("Lock: guests can't use the controls in this category")
        self.lock_btn.setCursor(Qt.PointingHandCursor)
        self.lock_btn.toggled.connect(self._on_lock)  # connected after setChecked
        hl.addWidget(self.lock_btn)

        menu_btn = QToolButton(objectName="CardMenu")
        menu_btn.setText("⋯")
        menu_btn.setCursor(Qt.PointingHandCursor)
        menu_btn.clicked.connect(self._show_menu)
        hl.addWidget(menu_btn)
        root.addWidget(header)

        self.cards_host = QWidget()
        self.flow = FlowLayout(self.cards_host, margin=2, spacing=10)
        root.addWidget(self.cards_host)

        self.hint = QLabel("Drop controls here")
        self.hint.setObjectName("CatHint")
        self.hint.setAlignment(Qt.AlignCenter)
        root.addWidget(self.hint)

    def add_card(self, card: ControlCard) -> None:
        self.flow.addWidget(card)

    def finalize(self) -> None:
        """Call after all cards are added."""
        self.hint.setVisible(self.flow.count() == 0)
        self.cards_host.setDisabled(self.lock_btn.isChecked())

    def _on_lock(self, checked: bool) -> None:
        self.lock_btn.setText("🔒" if checked else "🔓")
        self.cards_host.setDisabled(checked)
        self.lock_toggled.emit(self.category["id"], checked)

    def _show_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Delete category",
                       lambda: self.delete_requested.emit(self.category["id"]))
        menu.exec(self.mapToGlobal(QPoint(self.width() - 10, 34)))

    # -- drop target -------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_CONTROL):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_CONTROL):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasFormat(MIME_CONTROL):
            return
        control_id = bytes(event.mimeData().data(MIME_CONTROL)).decode()
        pos = self.cards_host.mapFrom(self, event.position().toPoint())
        event.acceptProposedAction()
        self.control_dropped.emit(control_id, self.category["id"], self._insert_index(pos))

    def _insert_index(self, pos: QPoint) -> int:
        """Row-major insertion point among the cards in this box."""
        for i in range(self.flow.count()):
            widget = self.flow.itemAt(i).widget()
            r = widget.geometry()
            if pos.y() < r.top():
                return i
            if pos.y() <= r.bottom() and pos.x() < r.center().x():
                return i
        return self.flow.count()
