"""The floating pill: Ken's plexus (see plexus.py) in a glass capsule. Idle it's a small orb of
linked points; it grows while listening, thinking and speaking. Drag it anywhere; a click
(without dragging) opens the panel."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import Property, QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import plexus as px
from . import theme

# ----------------------------------------------------------------------------
# States and sizes
# ----------------------------------------------------------------------------

IDLE, HOVER, LISTENING, WORKING, SPEAKING = "idle", "hover", "listening", "working", "speaking"
SIZES = {IDLE: QSize(46, 46), HOVER: QSize(262, 46), LISTENING: QSize(150, 54), WORKING: QSize(150, 54),
         SPEAKING: QSize(150, 54)}
LABEL = {LISTENING: "listening_", WORKING: "thinking_"}
PLEXUS_STATE = {IDLE: px.IDLE, HOVER: px.IDLE, LISTENING: px.LISTEN, WORKING: px.THINK, SPEAKING: px.SPEAK}
SLEEP_AFTER = 10 * 60  # seconds idle before Ken dozes off
MARGIN = 10  # room for the soft shadow around the pill
DRAG_THRESHOLD = 4


# ----------------------------------------------------------------------------
# The pill
#   The window stays one size; the capsule inside animates between sizes. 25 fps while active,
#   about 12 when resting, so it costs almost nothing when idle.
# ----------------------------------------------------------------------------

class Pill(QWidget):
    clicked = Signal()
    moved = Signal(QPoint)  # top-left after a drag, to remember the position
    menu_requested = Signal(QPoint)  # right-click: the app shows its menu (with Quit) here

    def __init__(self, hotkey_label: str):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.hotkey_label = hotkey_label
        self.state = IDLE
        self.state_since = time.monotonic()
        self.unread = 0
        self.attention = False  # a draft/event/link is waiting in the closed panel
        self.level_source = lambda: 0.0
        self._size = QSize(SIZES[IDLE])
        self._press: QPointF | None = None
        self._dragging = False
        self._anim = QPropertyAnimation(self, b"pillSize", self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self.plexus = px.Plexus(48, seed=5)
        self.done_until = 0.0  # a "done" check mark shows briefly after a reply
        self.follow_until = 0.0  # a follow-up window is open until then (monotonic time)
        self.follow_total = 0.0  # its length, for the shrinking bar
        self._tick = QTimer(self, interval=40, timeout=self._on_tick)  # 25 fps; 12 when resting
        self._tick.start()
        self.setFixedSize(max(s.width() for s in SIZES.values()) + 2 * MARGIN,
                          max(s.height() for s in SIZES.values()) + 2 * MARGIN)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def place(self, saved: QPoint | None = None) -> None:
        """At the saved spot if it's still on a screen, else bottom-centre of the main screen."""
        if saved is not None and any(s.availableGeometry().contains(saved + QPoint(self.width() // 2, self.height() // 2))
                                     for s in self.screen().virtualSiblings()):
            self.move(saved)
            return
        screen = self.screen().availableGeometry()
        self.move(screen.center().x() - self.width() // 2, screen.bottom() - self.height() - 6)

    # animated size of the pill itself (the window stays one size)
    def _get_size(self) -> QSize:
        return self._size

    def _set_size(self, size: QSize) -> None:
        self._size = size
        self.update()

    pillSize = Property(QSize, _get_size, _set_size)

    def flash_done(self) -> None:
        self.done_until = time.monotonic() + 1.6

    # ------------------------------------------------------------------------
    # Follow-up window
    #   While it's open the pill shows "still listening_" and a thin bar along its bottom edge
    #   that shrinks to nothing as the seconds run out. set_follow(0) ends it early.
    # ------------------------------------------------------------------------

    def set_follow(self, seconds: float) -> None:
        self.follow_total = seconds
        self.follow_until = time.monotonic() + seconds if seconds > 0 else 0.0
        self.update()

    @property
    def following(self) -> bool:
        return time.monotonic() < self.follow_until

    def jump(self) -> None:
        """The "Ken!" moment."""
        self.plexus.jump()

    def plexus_state(self) -> str:
        """What Ken's plexus shows now (the panel's avatar mirrors it)."""
        now = time.monotonic()
        if self.state in (IDLE, HOVER) and now < self.done_until:
            return px.DONE
        if self.state == IDLE and now - self.state_since > SLEEP_AFTER:
            return px.SLEEP
        return PLEXUS_STATE[self.state]

    def set_state(self, state: str) -> None:
        if state == self.state:
            return
        self.state, self.state_since = state, time.monotonic()
        self._anim.stop()
        self._anim.setStartValue(self._size)
        self._anim.setEndValue(SIZES[state])
        self._anim.start()

    def pill_rect(self) -> QRectF:
        w, h = self._size.width(), self._size.height()
        return QRectF((self.width() - w) / 2, self.height() - MARGIN - h, w, h)

    # ------------------------------------------------------------------------
    # Mouse: click opens, drag moves
    # ------------------------------------------------------------------------

    def enterEvent(self, event) -> None:
        if self.state == IDLE:
            self.set_state(HOVER)

    def leaveEvent(self, event) -> None:
        if self.state == HOVER and not self._dragging:
            self.set_state(IDLE)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton and self.pill_rect().contains(event.position()):
            self.menu_requested.emit(event.globalPosition().toPoint())
            return
        if event.button() == Qt.MouseButton.LeftButton and self.pill_rect().contains(event.position()):
            self._press = event.globalPosition() - QPointF(self.pos())
            self._dragging = False

    def mouseMoveEvent(self, event) -> None:
        if self._press is None:
            return
        target = event.globalPosition() - self._press
        if not self._dragging and (target - QPointF(self.pos())).manhattanLength() > DRAG_THRESHOLD:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._dragging:
            self.move(target.toPoint())

    def mouseReleaseEvent(self, event) -> None:
        if self._press is None:
            return
        self._press = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if self._dragging:
            self._dragging = False
            self.moved.emit(self.pos())
        else:
            self.clicked.emit()

    def _on_tick(self) -> None:
        self.plexus.set_state(self.plexus_state())
        self.plexus.level = self.level_source() if self.state == LISTENING else 0.0
        resting = self.plexus.state in (px.IDLE, px.SLEEP) and not self.attention
        self._frame = getattr(self, "_frame", 0) + 1
        if resting and self._frame % 2:  # ~15 fps when nothing happens: it turns slowly anyway
            return
        self.plexus.step()
        self.update(self.pill_rect().adjusted(-10, -10, 10, 10).toAlignedRect())

    # ------------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.pill_rect()
        radius = r.height() / 2
        state_col = QColor(*[int(c) for c in self.plexus.color])
        active = self.state in (LISTENING, WORKING, SPEAKING)

        p.setPen(Qt.PenStyle.NoPen)
        for i in range(6, 0, -2):  # soft shadow
            p.setBrush(QColor(20, 40, 60, 14))
            p.drawRoundedRect(r.adjusted(-i, -i + 3, i, i + 3), radius + i, radius + i)
        if active:  # a soft halo in the state's colour
            halo = QColor(state_col)
            halo.setAlpha(40)
            p.setPen(QPen(halo, 4))
            p.drawRoundedRect(r.adjusted(-2, -2, 2, 2), radius + 2, radius + 2)
        path = QPainterPath()
        path.addRoundedRect(r, radius, radius)
        p.fillPath(path, QColor(255, 255, 255, 240))
        p.setPen(QPen(state_col if active else QColor(255, 255, 255), 1.2))
        p.drawPath(path)

        h = r.height()
        orb = QRectF(r.left() + 3, r.top() + 3, h - 6, h - 6)
        if self.plexus.state == px.SPEAK:  # the ribbon runs the full width of the capsule
            self.plexus.paint(p, r.adjusted(radius * 0.5, 2, -radius * 0.5, -2), small=True, wide=True)
        elif self.state in LABEL or (self.state == HOVER and r.width() > 200):
            self.plexus.paint(p, orb, small=True)
            text = LABEL.get(self.state) or f'{self.hotkey_label} · "Ken"'
            if self.state == LISTENING and self.following:
                text = "still listening_"
            p.setPen(state_col if self.state in LABEL else QColor(theme.MUTED))
            p.setFont(theme.mono(8.5))
            p.drawText(r.adjusted(h + 2, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        else:
            self.plexus.paint(p, QRectF(r.center().x() - (h - 6) / 2, r.top() + 3, h - 6, h - 6), small=True)

        if self.state == LISTENING and self.following and self.follow_total > 0:  # time left to speak
            left = max(0.0, (self.follow_until - time.monotonic()) / self.follow_total)
            bar = QColor(state_col)
            bar.setAlpha(170)
            p.setPen(QPen(bar, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            inner = r.width() - 2 * radius
            p.drawLine(QPointF(r.left() + radius, r.bottom() - 4), QPointF(r.left() + radius + inner * left, r.bottom() - 4))

        if self.attention:  # something waits for a click: a soft pulsing ring
            pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 3)
            ring = QColor(theme.ACCENT)
            ring.setAlphaF(0.35 + 0.45 * pulse)
            p.setPen(QPen(ring, 2 + pulse))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-4, -4, 4, 4), radius + 4, radius + 4)
        if self.unread:  # new messages: a dot on the top-right
            p.setPen(QPen(QColor("white"), 2))
            p.setBrush(QColor(theme.DANGER))
            p.drawEllipse(QPointF(r.right() - radius * 0.3, r.top() + radius * 0.3), 5, 5)
        p.end()

    def set_attention(self, on: bool) -> None:
        self.attention = on
        self.update()

    def set_unread(self, n: int) -> None:
        self.unread = n
        self.update()
