"""The nudge pop-up: a small Frost card that appears just above the pill when Ken speaks up on
his own ("Youssef asked twice · 3 h ago"). It never takes focus, hides by itself after a while
(not while the mouse is on it), and a new nudge replaces the old one.

Buttons:
  draft reply   -> asks Ken for a draft to that chat (an AI request, only on click)
  snooze 2 h    -> not about this chat for two hours
  mute chat     -> never nudge about this chat again
  ok            -> (calendar alerts) just closes it
"""

from __future__ import annotations

from html import escape as html_escape

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import theme
from .panel import Card, crumb

WIDTH = 360
SHOW_MS = 45_000


# ----------------------------------------------------------------------------
# The window: a translucent frame (room for a soft shadow) holding one Card
# ----------------------------------------------------------------------------

class NudgeToast(QWidget):
    draft = Signal(object)  # Nudge
    snooze = Signal(object)
    mute = Signal(object)

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(WIDTH)
        self.nudge = None
        self._hide = QTimer(self, singleShot=True, interval=SHOW_MS, timeout=self._maybe_hide)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(10, 8, 10, 12)
        self._card: Card | None = None

    # ------------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------------

    def show_nudge(self, nudge, anchor: QRect | None) -> None:
        """Replace whatever is shown with this nudge and place it above the pill (or bottom-right)."""
        self.nudge = nudge
        if self._card is not None:
            self._card.hide()
            self._card.deleteLater()
        event = nudge.kind == "event"
        card = Card(theme.CHANNEL["calendar"] if event else theme.CHANNEL["whatsapp"])
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 12, 10)
        lay.setSpacing(5)
        lay.addWidget(crumb("ken › heads up" + (" › calendar" if event else " › whatsapp"), card.color.name()))
        title = QLabel(f"<b>{html_escape(nudge.title)}</b>")
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setWordWrap(True)
        lay.addWidget(title)
        if nudge.body:
            body = QLabel(("“" + nudge.body + "”") if not event else nudge.body)
            body.setObjectName("muted")
            body.setWordWrap(True)
            if any("؀" <= ch <= "ۿ" for ch in nudge.body):
                body.setAlignment(Qt.AlignmentFlag.AlignRight)
            lay.addWidget(body)

        row = QHBoxLayout()
        row.setSpacing(5)
        if event:
            row.addStretch(1)
            ok = QPushButton("ok")
            ok.setObjectName("primary")
            ok.clicked.connect(self.hide)
            row.addWidget(ok)
        else:
            mute = QPushButton("mute chat")
            mute.setObjectName("ghost")
            mute.setToolTip("Never nudge me about this chat again")
            mute.clicked.connect(lambda: (self.mute.emit(self.nudge), self.hide()))
            snooze = QPushButton("snooze 2 h")
            snooze.setObjectName("ghost")
            snooze.clicked.connect(lambda: (self.snooze.emit(self.nudge), self.hide()))
            draft = QPushButton("draft reply")
            draft.setObjectName("primary")
            draft.setCursor(Qt.CursorShape.PointingHandCursor)
            draft.clicked.connect(lambda: (self.draft.emit(self.nudge), self.hide()))
            row.addWidget(mute)
            row.addStretch(1)
            row.addWidget(snooze)
            row.addWidget(draft)
        lay.addLayout(row)
        self._outer.addWidget(card)
        self._card = card
        self.adjustSize()
        self._place(anchor)
        self.show()
        self.raise_()
        self._hide.start()

    # ------------------------------------------------------------------------
    # Placement and hiding
    # ------------------------------------------------------------------------

    def _place(self, anchor: QRect | None) -> None:
        screen = (self.screen() or self.window().screen()).availableGeometry()
        if anchor is not None:
            x = anchor.center().x() - self.width() // 2
            y = anchor.top() - self.height() + 6
        else:
            x, y = screen.right() - self.width() - 16, screen.bottom() - self.height() - 16
        x = max(screen.left() + 4, min(x, screen.right() - self.width() - 4))
        y = max(screen.top() + 4, y)
        self.move(QPoint(x, y))

    def _maybe_hide(self) -> None:
        if self.underMouse():
            self._hide.start(5_000)  # reading it: wait a little longer
        else:
            self.hide()

    def paintEvent(self, e) -> None:
        """A soft shadow under the card (the card paints itself)."""
        if self._card is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        r = self._card.geometry()
        for i in range(8, 0, -2):
            p.setBrush(QColor(20, 40, 60, 12))
            p.drawRoundedRect(r.adjusted(-i, -i + 4, i, i + 4), 12 + i, 12 + i)
        p.end()
