"""Floating timer cards (Frost console): one small glass card per timer, draggable, always on top.

Mono label and digits, and a 10-segment bar that fills as time passes (the stopwatch's single lit
segment runs round once a minute). The card pops in, flashes on a pomodoro phase change and turns
mint when a countdown is done. Hover for pause/resume, reset and stop.
TimerBoard keeps one bubble per timer in the shared TimerManager (the AI starts timers there
from the backend thread) and reports phase changes so the app can chime and notify.
"""

from __future__ import annotations

import math
import threading
import time

from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QConicalGradient, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

from ..timers import COUNTDOWN, FOCUS, LONG_BREAK, POMODORO, SHORT_BREAK, STOPWATCH, Timer, TimerManager
from . import theme

# ----------------------------------------------------------------------------
# Look
# ----------------------------------------------------------------------------

ICON = theme.ICONS

W, H, M = 176, 104, 8  # window; the card is (W - 2M) x (H - 2M), the rest is room for the shadow
COLORS = {  # label / bar colour per kind, from the Frost palette
    FOCUS: theme.ACCENT,
    SHORT_BREAK: theme.VIOLET,
    LONG_BREAK: theme.ACCENT_2,
    STOPWATCH: theme.ACCENT_2,
    COUNTDOWN: theme.VIOLET,
    "done": theme.SUCCESS,
}
PHASE_LABEL = {FOCUS: "FOCUS", SHORT_BREAK: "BREAK", LONG_BREAK: "LONG BREAK"}


# ----------------------------------------------------------------------------
# Sound
#   A soft two-note bell on its own audio stream, so it never cuts off speech.
# ----------------------------------------------------------------------------

def chime(kind: str = "done") -> None:
    """A soft two-note bell on its own audio stream (so it never cuts off speech)."""
    def play():
        try:
            import numpy as np
            import sounddevice as sd

            rate = 44100
            notes = {"done": (659.25, 987.77), "wake": (880.0, 1318.5)}.get(kind, (523.25, 783.99))
            lengths = (0.09, 0.16) if kind == "wake" else (0.35, 0.55)  # wake: a short "I'm listening" blip
            parts = []
            for i, f in enumerate(notes):
                t = np.arange(int(rate * lengths[i])) / rate
                tone = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)
                parts.append(tone * np.exp(-t * 5) * 0.18)
            wave = np.concatenate(parts).astype(np.float32)
            with sd.OutputStream(samplerate=rate, channels=1, dtype="float32") as out:
                out.write(wave.reshape(-1, 1))
        except Exception as exc:
            print(f"  ⚠  chime failed: {exc}")
    threading.Thread(target=play, daemon=True).start()


def _ease_out(x: float) -> float:
    return 1 - (1 - x) ** 3


# ----------------------------------------------------------------------------
# One timer card
# ----------------------------------------------------------------------------

class TimerBubble(QWidget):
    def __init__(self, timer: Timer, manager: TimerManager):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(W, H)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.timer, self.manager = timer, manager
        self.shown = 0.0  # progress actually drawn; glides toward the real one
        self.hover = 0.0
        self._hovering = False
        self.flash_at = 0.0  # time of the last phase change, for the burst ring
        self.closing = False
        self._press: QPoint | None = None
        self._dragged = False
        self.scale = 0.0
        self._intro = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=650,
                                        easingCurve=QEasingCurve.Type.OutBack)
        self._intro.valueChanged.connect(self._set_scale)

    def _set_scale(self, v) -> None:
        self.scale = float(v)
        self.update()

    def pop_in(self) -> None:
        self.show()
        self._intro.start()

    def pop_out(self) -> None:
        self.closing = True
        out = QVariantAnimation(self, startValue=self.scale, endValue=0.0, duration=280,
                                easingCurve=QEasingCurve.Type.InBack)
        out.valueChanged.connect(self._set_scale)
        out.finished.connect(self.deleteLater)
        out.start()

    def flash(self) -> None:
        self.flash_at = time.monotonic()

    # ------------------------------------------------------------------------
    # Per frame (called by TimerBoard)
    # ------------------------------------------------------------------------

    def step(self) -> None:
        view = self.timer.view(self.manager.clock())
        target = view.progress
        if target < self.shown - 0.5:  # wrapped (new phase, stopwatch minute): restart the sweep
            self.shown = 0.0
        self.shown += (target - self.shown) * 0.18
        self.hover += ((1.0 if self._hovering else 0.0) - self.hover) * 0.25
        self.update()

    # ------------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------------

    def _color(self, view) -> QColor:
        key = "done" if view.done else view.phase if self.timer.mode == POMODORO else self.timer.mode
        return QColor(COLORS.get(key, COLORS[COUNTDOWN]))

    def paintEvent(self, _event) -> None:
        now = time.monotonic()
        view = self.timer.view(self.manager.clock())
        col = self._color(view)
        paused = not self.timer.running and not view.done
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        center = QPointF(W / 2, H / 2)
        p.translate(center)
        p.scale(max(self.scale, 0.01), max(self.scale, 0.01))
        p.translate(-center)
        card = QRectF(M, M, W - 2 * M, H - 2 * M)

        p.setPen(Qt.PenStyle.NoPen)
        for i in range(6, 0, -2):  # soft shadow
            p.setBrush(QColor(20, 40, 60, 12))
            p.drawRoundedRect(card.adjusted(-i, -i + 3, i, i + 3), 14 + i, 14 + i)
        pulse = (0.5 + 0.5 * math.sin(now * 4)) if view.done else 0.0
        burst = max(0.0, 1 - (now - self.flash_at) / 0.9) if self.flash_at else 0.0
        p.setBrush(QColor(theme.ACCENT_SOFT) if view.done else QColor("white"))
        edge = QColor(col) if (view.done or burst > 0.05) else QColor(theme.LINE)
        if view.done:
            edge.setAlphaF(0.5 + 0.5 * pulse)
        p.setPen(QPen(edge, 1.2 + burst))
        p.drawRoundedRect(card, 14, 14)

        x0, y0, cw = card.left() + 12, card.top() + 9, card.width() - 24
        label = "DONE ✓" if view.done else PHASE_LABEL.get(view.phase, "") or self.timer.mode.upper()
        if self.timer.mode == POMODORO and not view.done:
            label += f" {view.round}/{view.rounds}"
        if paused:
            label = "PAUSED"
        p.setPen(QColor(theme.MUTED) if paused else col)
        p.setFont(theme.mono(7))
        p.drawText(QRectF(x0, y0, cw, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        name = self.timer.name if len(self.timer.name) <= 14 else self.timer.name[:13] + "…"
        p.setOpacity(1 - self.hover)
        p.setPen(QColor(theme.MUTED))
        p.setFont(theme.font(8))
        p.drawText(QRectF(x0, y0, cw, 14), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, name)
        p.setOpacity(1.0)

        digits = QColor(theme.SUCCESS) if view.done else QColor(theme.INK)
        if paused:
            digits.setAlphaF(0.45 + 0.25 * math.sin(now * 3))
        p.setPen(digits)
        p.setFont(theme.mono(19 if len(view.text) <= 5 else 15))
        p.drawText(QRectF(x0, y0 + 14, cw, 34), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, view.text)

        bar_y = card.bottom() - 16
        if view.done:
            p.setPen(QColor(theme.MUTED))
            p.setFont(theme.font(8))
            p.drawText(QRectF(x0, bar_y - 6, cw, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"{self.timer.name} is ready, Sir")
        else:  # 10 segments; the stopwatch lights one that runs round once a minute
            gap, segs = 3, 10
            seg_w = (cw - gap * (segs - 1)) / segs
            frac = self.shown * _ease_out(min(1.0, self.scale))
            lit = int(frac * segs) % segs if self.timer.mode == STOPWATCH else round(frac * segs)
            off = QColor(col)
            off.setAlphaF(0.16)
            for k in range(segs):
                on = (k == lit) if self.timer.mode == STOPWATCH else k < lit
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.FAINT) if (on and paused) else col if on else off)
                p.drawRoundedRect(QRectF(x0 + k * (seg_w + gap), bar_y, seg_w, 5), 2, 2)

        if self.hover > 0.02:  # controls where the name was: pause/resume, reset, stop
            p.setOpacity(self.hover)
            for icon, rect in self._buttons(view).items():
                p.setPen(QPen(QColor(theme.LINE), 1))
                p.setBrush(QColor(theme.SURFACE_2))
                p.drawRoundedRect(rect, 5, 5)
                p.setPen(QColor(theme.INK))
                p.setFont(theme.icon_font(7))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, icon)
            p.setOpacity(1.0)
        p.end()

    def _buttons(self, view) -> dict[str, QRectF]:
        icons = [ICON["close"]] if view.done else \
            [ICON["pause"] if self.timer.running else ICON["play"], ICON["reset"], ICON["close"]]
        d, gap = 18, 4
        right = W - M - 10
        return {icon: QRectF(right - (len(icons) - i) * (d + gap) + gap, M + 7, d, d) for i, icon in enumerate(icons)}

    # ------------------------------------------------------------------------
    # Mouse: drag, pause/resume, close
    # ------------------------------------------------------------------------

    def enterEvent(self, _e) -> None:
        self._hovering = True

    def leaveEvent(self, _e) -> None:
        self._hovering = False

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragged = False

    def mouseMoveEvent(self, e) -> None:
        if self._press is not None and e.buttons() & Qt.MouseButton.LeftButton:
            target = e.globalPosition().toPoint() - self._press
            if (target - self.pos()).manhattanLength() > 3 or self._dragged:
                self._dragged = True
                self.move(target)

    def mouseReleaseEvent(self, e) -> None:
        if self._press is None:
            return
        self._press = None
        if self._dragged:
            return
        view = self.timer.view(self.manager.clock())
        for icon, rect in self._buttons(view).items():
            if rect.contains(e.position()):
                if icon == ICON["close"]:
                    self.manager.stop(self.timer)
                elif icon == ICON["reset"]:
                    self.manager.reset(self.timer)
                    self.shown = 0.0
                elif self.timer.running:
                    self.manager.pause(self.timer)
                else:
                    self.manager.resume(self.timer)
                return


# ----------------------------------------------------------------------------
# The board
#   One bubble per timer, stacked above the pill; forwards phase changes to the app.
# ----------------------------------------------------------------------------

class TimerBoard(QObject):
    """Keeps one bubble per timer and forwards phase changes (timer, phase) to the app."""

    phase_changed = Signal(object, str)

    def __init__(self, manager: TimerManager, anchor=lambda: None):
        super().__init__()
        self.manager = manager
        self.anchor = anchor  # () -> QRect of the pill, to stack new bubbles above it
        self.bubbles: dict[str, TimerBubble] = {}
        self._tick = QTimer(self, interval=33, timeout=self._on_tick)
        self._tick.start()

    def _on_tick(self) -> None:
        for timer, phase in self.manager.poll():
            if timer.id in self.bubbles:
                self.bubbles[timer.id].flash()
            self.phase_changed.emit(timer, phase)
        with self.manager.lock:
            live = {t.id: t for t in self.manager.timers}
        for tid, bubble in list(self.bubbles.items()):
            if tid not in live:
                del self.bubbles[tid]
                bubble.pop_out()
        for tid, timer in live.items():
            if tid not in self.bubbles:
                bubble = TimerBubble(timer, self.manager)
                bubble.move(self._spot(len(self.bubbles)))
                self.bubbles[tid] = bubble
                bubble.pop_in()
        for bubble in self.bubbles.values():
            bubble.step()

    def _spot(self, index: int) -> QPoint:
        """Above the pill, stacking upward; bottom-right of the screen if there's no pill."""
        pill = self.anchor()
        if pill is not None:
            x = pill.center().x() - W // 2
            y = pill.top() - (H - 4) * (index + 1)
            return QPoint(x, max(0, y))
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen().availableGeometry()
        return QPoint(screen.right() - W - 24, screen.bottom() - (H - 4) * (index + 1) - 24)
