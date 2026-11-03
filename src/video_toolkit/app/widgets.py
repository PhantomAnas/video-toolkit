"""Reusable pieces: wallpaper backdrop, glass card, pill buttons, segmented
control, labelled slider, file picker, time entry, region preview, clip list."""

from __future__ import annotations

import os
import random

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, QRunnable, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..ffmpeg import fmt_time, parse_time
from . import icons


# ----------------------------------------------------------------- backdrop

def generate_wallpaper(width: int, height: int, seed: int = 11) -> QPixmap:
    pm = QPixmap(max(16, width), max(16, height))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    base = QLinearGradient(0, 0, width, height)
    base.setColorAt(0.0, QColor("#0f1a2e"))
    base.setColorAt(0.5, QColor("#141229"))
    base.setColorAt(1.0, QColor("#0a1f2a"))
    p.fillRect(pm.rect(), QBrush(base))
    rng = random.Random(seed)
    palette = ["#2563eb", "#0ea5e9", "#7c3aed", "#db2777", "#0d9488", "#f59e0b"]
    for i in range(6):
        cx, cy = rng.uniform(0, width), rng.uniform(0, height)
        r = rng.uniform(0.25, 0.55) * max(width, height)
        g = QRadialGradient(cx, cy, r)
        c = QColor(palette[i % len(palette)])
        c.setAlphaF(0.28)
        g.setColorAt(0.0, c)
        c2 = QColor(c)
        c2.setAlphaF(0.0)
        g.setColorAt(1.0, c2)
        p.setBrush(QBrush(g))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(cx - r), int(cy - r), int(2 * r), int(2 * r))
    p.end()
    return pm


class Backdrop(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source: QPixmap | None = None
        self._scaled: QPixmap | None = None

    def set_image(self, path: str | None) -> bool:
        if path and os.path.isfile(path):
            pm = QPixmap(path)
            if not pm.isNull():
                self._source, self._scaled = pm, None
                self.update()
                return True
        self._source, self._scaled = None, None
        self.update()
        return False

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        w, h = self.width(), self.height()
        if self._scaled is None or self._scaled.size() != self.size():
            if self._source is None:
                self._scaled = generate_wallpaper(w, h)
            else:
                scaled = self._source.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                             Qt.TransformationMode.SmoothTransformation)
                self._scaled = scaled.copy((scaled.width() - w) // 2, (scaled.height() - h) // 2, w, h)
        p.drawPixmap(0, 0, self._scaled)
        if self._source is not None:
            p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        p.end()


# ---------------------------------------------------------------------- card

class GlassCard(QFrame):
    def __init__(self, title: str | None = None, icon_name: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 18)
        self.body.setSpacing(12)
        self.head = QHBoxLayout()
        self.head.setSpacing(8)
        if icon_name:
            ic = QLabel()
            ic.setPixmap(icons.pixmap(icon_name, 18, "#ffffff", dpr=self.devicePixelRatioF()))
            self.head.addWidget(ic)
        self.title_lbl = QLabel(title or "")
        self.title_lbl.setObjectName("CardTitle")
        self.head.addWidget(self.title_lbl)
        self.head.addStretch(1)
        if title:
            self.body.addLayout(self.head)


def hint(text: str, faint: bool = False) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Faint" if faint else "Hint")
    lbl.setWordWrap(True)
    return lbl


# ------------------------------------------------------------------- buttons

def pill(text: str, icon_name: str | None = None, kind: str = "", tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    if icon_name:
        color = "#06230f" if kind == "Go" else "#ffffff"
        b.setIcon(icons.icon(icon_name, color, filled=(icon_name == "play")))
        b.setIconSize(icons.ICON_SIZE)
    if kind:
        b.setObjectName(kind)
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def round_button(icon_name: str, tooltip: str = "") -> QPushButton:
    b = QPushButton()
    b.setObjectName("Round")
    b.setIcon(icons.icon(icon_name))
    b.setIconSize(QSize(18, 18))
    b.setToolTip(tooltip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class Segmented(QFrame):
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SegGroup")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for key, label in options:
            b = QPushButton(label)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(b)
            lay.addWidget(b)
            self._buttons[key] = b
            b.toggled.connect(lambda on, k=key: on and self.changed.emit(k))
        if options:
            next(iter(self._buttons.values())).setChecked(True)

    def set_value(self, key: str) -> None:
        if key in self._buttons:
            self._buttons[key].setChecked(True)

    def value(self) -> str:
        for k, b in self._buttons.items():
            if b.isChecked():
                return k
        return next(iter(self._buttons))


class LabeledSlider(QWidget):
    valueChanged = Signal(float)

    def __init__(self, caption: str, lo: float, hi: float, step: float, fmt: str = "{:.1f}",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.lo, self.step, self.fmt = lo, step, fmt
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        top = QHBoxLayout()
        self.caption = hint(caption)
        self.value_lbl = hint("")
        top.addWidget(self.caption)
        top.addStretch(1)
        top.addWidget(self.value_lbl)
        lay.addLayout(top)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, int(round((hi - lo) / step)))
        self.slider.valueChanged.connect(self._on_change)
        lay.addWidget(self.slider)
        self._on_change(0)

    def _on_change(self, _raw: int) -> None:
        v = self.value()
        self.value_lbl.setText(self.fmt.format(v))
        self.valueChanged.emit(v)

    def value(self) -> float:
        return self.lo + self.slider.value() * self.step

    def set_value(self, v: float) -> None:
        self.slider.setValue(int(round((v - self.lo) / self.step)))


class FilePicker(QWidget):
    changed = Signal(str)

    def __init__(self, caption: str, icon_name: str, mode: str = "file", filter_: str = "",
                 placeholder: str = "None", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mode, self.filter_, self.placeholder = mode, filter_, placeholder
        self._path = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(icons.pixmap(icon_name, 18, "#ffffff", dpr=self.devicePixelRatioF()))
        lay.addWidget(ic)
        text = QVBoxLayout()
        text.setSpacing(0)
        self.cap = hint(caption)
        self.name = hint(placeholder, faint=True)
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text.addWidget(self.cap)
        text.addWidget(self.name)
        lay.addLayout(text, 1)
        self.choose = pill("Choose")
        self.clear_btn = round_button("x", "Clear")
        self.clear_btn.setVisible(False)
        lay.addWidget(self.choose)
        lay.addWidget(self.clear_btn)
        self.choose.clicked.connect(self._pick)
        self.clear_btn.clicked.connect(lambda: self.set_path(""))

    def _pick(self) -> None:
        start = os.path.dirname(self._path) if self._path else os.path.expanduser("~")
        if self.mode == "dir":
            path = QFileDialog.getExistingDirectory(self, self.cap.text(), start)
        else:
            path, _ = QFileDialog.getOpenFileName(self, self.cap.text(), start, self.filter_)
        if path:
            self.set_path(os.path.normpath(path))

    def set_path(self, path: str) -> None:
        self._path = path or ""
        self.name.setText(os.path.basename(self._path) or self._path or self.placeholder)
        self.name.setObjectName("Hint" if self._path else "Faint")
        self.name.setToolTip(self._path)
        self.name.style().unpolish(self.name)
        self.name.style().polish(self.name)
        self.clear_btn.setVisible(bool(self._path))
        self.changed.emit(self._path)

    def path(self) -> str:
        return self._path


# ------------------------------------------------------------------- time

class TimeEdit(QLineEdit):
    """Accepts 90, 1:30, 0:01:30.250 or 1h2m3s; shows m:ss.mmm; clamps to the clip."""

    timeChanged = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._seconds = 0.0
        self._max = 0.0
        self.setFixedWidth(118)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setPlaceholderText("0:00.000")
        self.editingFinished.connect(self._commit)
        self.set_seconds(0.0)

    def set_max(self, seconds: float) -> None:
        self._max = max(0.0, seconds)
        self.set_seconds(self._seconds)

    def seconds(self) -> float:
        return self._seconds

    def set_seconds(self, s: float) -> None:
        s = max(0.0, s)
        if self._max:
            s = min(s, self._max)
        changed = abs(s - self._seconds) > 1e-6
        self._seconds = s
        self.setText(fmt_time(s, ms=True))
        self.setStyleSheet("")
        if changed:
            self.timeChanged.emit(s)

    def _commit(self) -> None:
        try:
            self.set_seconds(parse_time(self.text()))
        except ValueError:
            self.setStyleSheet("border: 1px solid #ff453a;")
            self.setToolTip("Use seconds (90), m:ss (1:30) or h:mm:ss.mmm")


# ---------------------------------------------------------- region preview

class RegionPreview(QWidget):
    """Shows a video frame; optionally lets the user drag/resize a rectangle on it.

    The rectangle is kept in *video* pixel coordinates; `regionChanged(x, y, w, h)`
    fires on every drag.
    """

    regionChanged = Signal(int, int, int, int)
    clicked = Signal()

    HANDLE = 9

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._frame: QPixmap | None = None
        self._video_size = QSize(0, 0)
        self._region: QRect | None = None
        self._region_enabled = False
        self._message = "Open a video to see it here"
        self._drag: str | None = None
        self._drag_start = QPoint()
        self._drag_region = QRect()
        self.setMinimumSize(320, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    # ---- state
    def set_frame(self, png: bytes | None, video_size: tuple[int, int]) -> None:
        if png:
            img = QImage.fromData(png)
            self._frame = QPixmap.fromImage(img) if not img.isNull() else None
        else:
            self._frame = None
        self._video_size = QSize(*video_size)
        self.update()

    def set_message(self, text: str) -> None:
        self._message = text
        self._frame = None
        self.update()

    def enable_region(self, enabled: bool) -> None:
        self._region_enabled = enabled
        self.update()

    def set_region(self, x: int, y: int, w: int, h: int) -> None:
        self._region = QRect(x, y, max(2, w), max(2, h))
        self.update()

    def region(self) -> tuple[int, int, int, int] | None:
        if self._region is None:
            return None
        r = self._region
        return r.x(), r.y(), r.width(), r.height()

    # ---- geometry
    def _frame_rect(self) -> QRectF:
        if self._frame is None or self._frame.width() == 0:
            return QRectF()
        fw, fh = self._frame.width(), self._frame.height()
        scale = min(self.width() / fw, self.height() / fh)
        w, h = fw * scale, fh * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _scale(self) -> float:
        fr = self._frame_rect()
        if fr.isEmpty() or self._video_size.width() == 0:
            return 1.0
        return fr.width() / self._video_size.width()

    def _to_widget(self, r: QRect) -> QRectF:
        fr, s = self._frame_rect(), self._scale()
        return QRectF(fr.x() + r.x() * s, fr.y() + r.y() * s, r.width() * s, r.height() * s)

    def _to_video(self, p: QPoint) -> QPoint:
        fr, s = self._frame_rect(), self._scale()
        return QPoint(int((p.x() - fr.x()) / s), int((p.y() - fr.y()) / s))

    # ---- painting
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bg = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 110))
        p.drawRoundedRect(bg, 16, 16)
        if self._frame is None:
            p.setPen(QColor(245, 245, 247, 130))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
            p.end()
            return
        fr = self._frame_rect()
        p.drawPixmap(fr.toRect(), self._frame)
        if self._region_enabled and self._region is not None:
            rr = self._to_widget(self._region)
            # darken outside
            p.setBrush(QColor(0, 0, 0, 120))
            p.setPen(Qt.PenStyle.NoPen)
            outer = QRectF(fr)
            for part in (QRectF(outer.left(), outer.top(), outer.width(), rr.top() - outer.top()),
                         QRectF(outer.left(), rr.bottom(), outer.width(), outer.bottom() - rr.bottom()),
                         QRectF(outer.left(), rr.top(), rr.left() - outer.left(), rr.height()),
                         QRectF(rr.right(), rr.top(), outer.right() - rr.right(), rr.height())):
                if part.width() > 0 and part.height() > 0:
                    p.drawRect(part)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#0a84ff"), 2))
            p.drawRect(rr)
            p.setPen(QPen(QColor(255, 255, 255, 90), 1))
            for f in (1 / 3, 2 / 3):
                p.drawLine(QRectF(rr.left() + rr.width() * f, rr.top(), 0, rr.height()).topLeft(),
                           QRectF(rr.left() + rr.width() * f, rr.bottom(), 0, 0).topLeft())
                p.drawLine(QRectF(rr.left(), rr.top() + rr.height() * f, 0, 0).topLeft(),
                           QRectF(rr.right(), rr.top() + rr.height() * f, 0, 0).topLeft())
            p.setBrush(QColor("#ffffff"))
            p.setPen(QPen(QColor("#0a84ff"), 1.5))
            for hp in self._handles(rr).values():
                p.drawEllipse(hp, self.HANDLE / 2, self.HANDLE / 2)
            p.setPen(QColor("#ffffff"))
            r = self._region
            p.drawText(rr.adjusted(6, 4, 0, 0).toRect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                       f"{r.width()}x{r.height()} @ {r.x()},{r.y()}")
        p.end()

    def _handles(self, rr: QRectF) -> dict:
        return {"tl": rr.topLeft(), "tr": rr.topRight(), "bl": rr.bottomLeft(), "br": rr.bottomRight(),
                "t": QRectF(rr.center().x(), rr.top(), 0, 0).topLeft(), "b": QRectF(rr.center().x(), rr.bottom(), 0, 0).topLeft(),
                "l": QRectF(rr.left(), rr.center().y(), 0, 0).topLeft(), "r": QRectF(rr.right(), rr.center().y(), 0, 0).topLeft()}

    # ---- mouse
    def _hit(self, pos) -> str | None:
        if not (self._region_enabled and self._region is not None and self._frame is not None):
            return None
        rr = self._to_widget(self._region)
        for name, hp in self._handles(rr).items():
            if (hp - pos).manhattanLength() <= self.HANDLE + 3:
                return name
        if rr.contains(pos):
            return "move"
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._hit(event.position())
        if hit is None:
            self.clicked.emit()
            return
        self._drag = hit
        self._drag_start = event.position().toPoint()
        self._drag_region = QRect(self._region)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is None:
            hit = self._hit(event.position())
            cursors = {"move": Qt.CursorShape.SizeAllCursor, "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
                       "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
                       "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
                       "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor}
            self.setCursor(cursors.get(hit, Qt.CursorShape.ArrowCursor))
            return
        s = self._scale() or 1.0
        d = event.position().toPoint() - self._drag_start
        dx, dy = int(d.x() / s), int(d.y() / s)
        r = QRect(self._drag_region)
        vw, vh = self._video_size.width(), self._video_size.height()
        if self._drag == "move":
            r.translate(dx, dy)
            r.moveLeft(max(0, min(vw - r.width(), r.left())))
            r.moveTop(max(0, min(vh - r.height(), r.top())))
        else:
            if "l" in self._drag:
                r.setLeft(max(0, min(r.right() - 8, r.left() + dx)))
            if "r" in self._drag:
                r.setRight(min(vw, max(r.left() + 8, r.right() + dx)))
            if "t" in self._drag:
                r.setTop(max(0, min(r.bottom() - 8, r.top() + dy)))
            if "b" in self._drag:
                r.setBottom(min(vh, max(r.top() + 8, r.bottom() + dy)))
        self._region = r
        self.update()
        self.regionChanged.emit(r.x(), r.y(), r.width(), r.height())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag = None


# ---------------------------------------------------------------- clip list

class ClipList(QListWidget):
    """Ordered list of files for the Join tool, with drag reordering."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.model().rowsMoved.connect(lambda *_: self.changed.emit())
        self.model().rowsRemoved.connect(lambda *_: self.changed.emit())

    def paths(self) -> list[str]:
        return [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]

    def add_paths(self, paths: list[str]) -> None:
        for p in paths:
            item = QListWidgetItem(icons.icon("film", "#c7cbe0"), os.path.basename(p))
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setToolTip(p)
            self.addItem(item)
        if paths:
            self.changed.emit()

    def remove_selected(self) -> None:
        for it in self.selectedItems():
            self.takeItem(self.row(it))
        self.changed.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_selected()
            return
        super().keyPressEvent(event)
