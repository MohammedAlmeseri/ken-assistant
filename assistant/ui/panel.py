"""The chat panel that opens above the pill, in the Frost console style.

Console paper (soft light and a faint dot grid), Ken's live avatar in the header (it mirrors the
pill: listening, thinking, speaking), messages that fly in as particles, typed-out replies with a
`ken ›` prompt, white cards with a coloured edge per channel and a mono breadcrumb, a terminal-
style status line, and keyboard hints.
"""

from __future__ import annotations

import math
import random
import re
import time
from html import escape as html_escape

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPainterPath, QPen, QPixmap, QTextOption
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QTextEdit, QVBoxLayout, QWidget,
)

from ..agent import Draft
from ..voice.tts import detect_language
from . import theme
from .plexus import DONE, IDLE, LISTEN, SLEEP, SPEAK, THINK, Plexus

PANEL_W, PANEL_H, SHADOW = 448, 660, 22
MARKDOWN_MARKS = re.compile(r"\*\*|__|`")
I = theme.ICONS


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------

def is_rtl(text: str) -> bool:
    return detect_language(text) == "ar"


def crumb(text: str, color: str) -> QLabel:
    """Mono breadcrumb, e.g. DRAFT › WHATSAPP › YOUSSEF."""
    label = QLabel(text.upper())
    label.setObjectName("crumb")
    label.setStyleSheet(f"color: {color};")
    return label


def kbd(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("kbd")
    return label


# ----------------------------------------------------------------------------
# Ken's avatar
#   The live plexus in the header and on the empty state.
# ----------------------------------------------------------------------------

class Avatar(QWidget):
    """A live plexus (see plexus.py). Animates only while visible."""

    def __init__(self, size: int, n: int, fps: int = 20):
        super().__init__()
        self.setFixedSize(size, size)
        self.plexus = Plexus(n, seed=size)
        self.small = size < 80
        if self.small:
            fps = min(fps, 12)  # tiny avatars: their motion reads fine at 12 fps
        self._timer = QTimer(self, interval=1000 // fps, timeout=self._tick)

    def set_state(self, state: str) -> None:
        self.plexus.set_state(state)

    def _tick(self) -> None:
        self.plexus.step()
        self.update()

    def showEvent(self, e) -> None:
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        self.plexus.paint(p, QRectF(self.rect()), small=self.small)
        p.end()


# ----------------------------------------------------------------------------
# Messages
#   Bubbles, feed rows that fade in, and the particle overlay that flies text between places.
# ----------------------------------------------------------------------------

class Bubble(QLabel):
    def __init__(self, text: str, role: str):
        super().__init__()
        self.role, self.full = role, text
        self.setObjectName("bubbleUser" if role == "user" else "bubbleAssistant")
        self.setTextFormat(Qt.TextFormat.MarkdownText if role == "assistant" else Qt.TextFormat.PlainText)
        self.setText(text)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.setOpenExternalLinks(True)
        # A wrapped QLabel in a layout tends to pick a narrow width, so size it to the text:
        # one line if it fits, otherwise the max bubble width with the height following.
        max_w = int(PANEL_W * 0.78)
        longest = max((self.fontMetrics().horizontalAdvance(line) for line in text.splitlines() or [""]), default=0)
        self.setFixedWidth(min(max_w, longest + 32))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        if is_rtl(text):
            self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            self.setAlignment(Qt.AlignmentFlag.AlignRight)

    def type_out(self, ms_per_char: float = 9, max_ms: int = 900) -> None:
        """Reveal the reply as if typed (plain while typing, then the formatted text). Steps every
        40 ms, not every frame: each change re-measures the chat's layout."""
        n = len(self.full)
        if n < 4:
            return
        self.setTextFormat(Qt.TextFormat.PlainText)
        plain = MARKDOWN_MARKS.sub("", self.full)  # no ** or ` showing while it types
        n = len(plain)
        steps = max(1, int(min(max_ms, n * ms_per_char) / 40))
        shown = [0]

        def tick():
            shown[0] += 1
            if shown[0] >= steps:
                timer.stop()
                self.setTextFormat(Qt.TextFormat.MarkdownText)
                self.setText(self.full)
                return
            self.setText(plain[: n * shown[0] // steps] + "▍")

        timer = QTimer(self, interval=40, timeout=tick)
        tick()
        timer.start()


class Row(QWidget):
    """One feed entry. Fades and slides in. Ken's messages get a `ken ›` prompt above them,
    yours a mono time below."""

    def __init__(self, widget: QWidget, align: Qt.AlignmentFlag, caption: str = ""):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 12, 0, 0)
        outer.setSpacing(2)
        self.outer = outer
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        if align == Qt.AlignmentFlag.AlignLeft and caption:
            top = QLabel(caption)
            top.setObjectName("prompt")
            outer.addWidget(top)
        if align == Qt.AlignmentFlag.AlignRight:
            line.addStretch(1)
            line.addWidget(widget)
        elif align == Qt.AlignmentFlag.AlignLeft:
            line.addWidget(widget)
            line.addStretch(1)
        else:
            line.addWidget(widget, 1)
        outer.addLayout(line)
        if align == Qt.AlignmentFlag.AlignRight and caption:
            when = QLabel(caption)
            when.setObjectName("meta")
            when.setAlignment(Qt.AlignmentFlag.AlignRight)
            outer.addWidget(when)
        self.effect = QGraphicsOpacityEffect(self)
        self.effect.setOpacity(0.0)
        self.setGraphicsEffect(self.effect)

    def appear(self, delay: int = 0) -> None:
        def run():
            anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=300,
                                     easingCurve=QEasingCurve.Type.OutCubic)
            anim.valueChanged.connect(lambda v: (self.effect.setOpacity(v),
                                                 self.outer.setContentsMargins(0, int(12 + 12 * (1 - v)), 0, 0)))
            anim.finished.connect(lambda: self.setGraphicsEffect(None))  # effects slow down text editing
            anim.start()
        QTimer.singleShot(delay, run)


class FlyOverlay(QWidget):
    """Particles flying from one place to another with links between them: a sent message
    leaves the input box this way, and Ken's reply arrives from his avatar."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.flights: list[dict] = []
        self._dirty = QRectF()
        self._timer = QTimer(self, interval=16, timeout=self._tick)

    def fly(self, src: QRectF, dst: QRectF, color: str, on_arrive=None, n: int = 22, ms: int = 520) -> None:
        pts = []
        for _ in range(n):
            a = QPointF(src.left() + random.random() * src.width(), src.top() + random.random() * src.height())
            b = QPointF(dst.left() + random.random() * dst.width(), dst.top() + random.random() * dst.height())
            bend = QPointF((random.random() - 0.5) * 80, -30 - random.random() * 60)
            pts.append((a, b, bend, random.random() * 0.25))
        self.flights.append({"pts": pts, "start": time.monotonic(), "ms": ms, "color": QColor(color),
                             "arrive": on_arrive, "arrived": False})
        self.raise_()
        self._timer.start()

    def _tick(self) -> None:
        now = time.monotonic()
        for f in self.flights:
            if not f["arrived"] and (now - f["start"]) * 1000 > f["ms"] * 0.7:
                f["arrived"] = True
                if f["arrive"]:
                    f["arrive"]()
        self.flights = [f for f in self.flights if (now - f["start"]) * 1000 < f["ms"] * 1.15]
        if not self.flights:
            self._timer.stop()
        # the paths stay inside the box around start, end and the arc's bulge
        box = QRectF()
        for f in self.flights:
            for a, b, bend, _ in f["pts"]:
                box = box.united(QRectF(a, b).normalized().adjusted(-50, -100, 50, 20))
        self.update((box.united(self._dirty)).toAlignedRect())
        self._dirty = box

    def paintEvent(self, e) -> None:
        if not self.flights:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        now = time.monotonic()
        for f in self.flights:
            prog = (now - f["start"]) * 1000 / f["ms"]
            spots = []
            for a, b, bend, lag in f["pts"]:
                t = min(1.0, max(0.0, (prog - lag) / (1 - lag)))
                e = 1 - (1 - t) ** 3
                x = a.x() + (b.x() - a.x()) * e + bend.x() * math.sin(e * math.pi)
                y = a.y() + (b.y() - a.y()) * e + bend.y() * math.sin(e * math.pi)
                spots.append(QPointF(x, y))
            fade = max(0.0, min(1.0, (1.15 - prog) / 0.35))
            c = QColor(f["color"])
            for i, s in enumerate(spots):  # links to the next two
                for j in (i + 1, i + 2):
                    if j < len(spots) and math.dist((s.x(), s.y()), (spots[j].x(), spots[j].y())) < 48:
                        c.setAlphaF(0.35 * fade)
                        p.setPen(QPen(c, 0.8))
                        p.drawLine(s, spots[j])
            p.setPen(Qt.PenStyle.NoPen)
            for s in spots:
                c.setAlphaF(0.18 * fade)
                p.setBrush(c)
                p.drawEllipse(s, 4.5, 4.5)
                c.setAlphaF(0.95 * fade)
                p.setBrush(c)
                p.drawEllipse(s, 1.8, 1.8)
        p.end()


# ----------------------------------------------------------------------------
# Cards
#   Things waiting for a click. A card only emits a signal; the app decides what happens.
# ----------------------------------------------------------------------------

class Card(QFrame):
    """Base for draft/event/link cards: white, with a coloured edge for the channel on the left
    and a mint ripple when it succeeds (sent / added)."""

    def __init__(self, color: str):
        super().__init__()
        self.color = QColor(color)
        self.ripple_at = -10.0
        self._timer = QTimer(self, interval=16, timeout=self._ripple_tick)

    def succeed(self) -> None:
        self.color = QColor(theme.SUCCESS)
        self.ripple_at = time.monotonic()
        self._timer.start()

    def _ripple_tick(self) -> None:
        if time.monotonic() - self.ripple_at > 0.9:
            self._timer.stop()
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        p.fillPath(path, QColor("white"))
        p.setPen(QPen(QColor(theme.LINE), 1))
        p.drawPath(path)
        bar = QPainterPath()
        bar.addRoundedRect(QRectF(r.left(), r.top() + 10, 3, r.height() - 20), 1.5, 1.5)
        p.fillPath(bar, self.color)
        rt = time.monotonic() - self.ripple_at
        if rt < 0.9:
            c = QColor(theme.SUCCESS)
            c.setAlphaF(0.45 * (1 - rt / 0.9))
            p.setPen(QPen(c, 2))
            grow = rt * 10
            p.drawRoundedRect(r.adjusted(-grow, -grow, grow, grow), 12 + grow, 12 + grow)
        p.end()

    def set_busy(self, busy: bool) -> None:
        """Panel.set_busy calls this on every card; only drafts use the model."""


class AutoTextEdit(QTextEdit):
    """Grows with its text (up to a limit). Height is recomputed on every resize because
    the wrapped height is only correct once the real width is known."""

    def __init__(self, min_h: int = 40, max_h: int = 220):
        super().__init__()
        self.min_h, self.max_h = min_h, max_h
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.textChanged.connect(self.fit)

    def fit(self) -> None:
        self.document().setTextWidth(max(50, self.viewport().width()))
        h = self.document().size().height() + self.frameWidth() * 2 + 6
        self.setFixedHeight(int(min(max(h, self.min_h), self.max_h)))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.fit()


def _card_layout(card: QFrame) -> QVBoxLayout:
    lay = QVBoxLayout(card)
    lay.setContentsMargins(14, 11, 12, 11)
    lay.setSpacing(6)
    return lay


def _short(name: str) -> str:
    return name.lower().replace(" ", "-") if len(name) <= 18 else name[:17].lower() + "…"


# ----------------------------------------------------------------------------
# Draft card
#   Editable text; Send opens the confirmation dialog, it never sends directly.
# ----------------------------------------------------------------------------

class DraftCard(Card):
    send = Signal(object, str)  # Draft, edited text
    regenerate = Signal(object)
    discard = Signal(object)

    def __init__(self, draft: Draft):
        super().__init__(theme.CHANNEL.get(draft.channel, theme.ACCENT))
        self.draft = draft
        lay = _card_layout(self)
        head = QHBoxLayout()
        name = {"whatsapp": "whatsapp", "gmail": "email"}.get(draft.channel, draft.channel)
        who = crumb(f"draft › {name} › {_short(draft.recipient_name or draft.recipient)}", self.color.name())
        who.setToolTip(draft.recipient)
        head.addWidget(who, 1)
        self.state_label = QLabel(f"{len(draft.text.splitlines())} line{'s' if len(draft.text.splitlines()) != 1 else ''}")
        self.state_label.setObjectName("meta")
        head.addWidget(self.state_label)
        lay.addLayout(head)

        if draft.channel == "gmail" and draft.subject:
            subject = QLabel(f"<span style='color:{theme.FAINT}'>subject</span>&nbsp;&nbsp;{html_escape(draft.subject)}")
            subject.setTextFormat(Qt.TextFormat.RichText)
            lay.addWidget(subject)

        self.editor = AutoTextEdit()
        self.editor.setObjectName("draftText")
        self.editor.setAcceptRichText(False)
        if is_rtl(draft.text):
            opt = self.editor.document().defaultTextOption()
            opt.setTextDirection(Qt.LayoutDirection.RightToLeft)
            opt.setAlignment(Qt.AlignmentFlag.AlignRight)
            self.editor.document().setDefaultTextOption(opt)
        self.editor.setPlainText(draft.text)
        self.editor.setToolTip("You can edit the text before sending")
        lay.addWidget(self.editor)

        self.buttons = QHBoxLayout()
        self.btn_discard = QPushButton("discard")
        self.btn_discard.setObjectName("danger")
        self.btn_regen = QPushButton("rewrite")
        self.btn_regen.setObjectName("ghost")
        self.btn_send = QPushButton("send  ↵")
        self.btn_send.setObjectName("primary")
        self.btn_send.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_discard.clicked.connect(lambda: self.discard.emit(self.draft))
        self.btn_regen.clicked.connect(lambda: self.regenerate.emit(self.draft))
        self.btn_send.clicked.connect(lambda: self.send.emit(self.draft, self.editor.toPlainText()))
        self.buttons.addWidget(self.btn_discard)
        self.buttons.addStretch(1)
        self.buttons.addWidget(self.btn_regen)
        self.buttons.addWidget(self.btn_send)
        lay.addLayout(self.buttons)

    def set_busy(self, busy: bool) -> None:
        for b in (self.btn_send, self.btn_regen, self.btn_discard):
            b.setEnabled(not busy)

    def mark_sent(self) -> None:
        self.state_label.setText("SENT ✓")
        self.state_label.setStyleSheet(f"color: {theme.SUCCESS};")
        self.editor.setReadOnly(True)
        for b in (self.btn_send, self.btn_regen, self.btn_discard):
            b.hide()
        self.succeed()


# ----------------------------------------------------------------------------
# Event card
#   Nothing reaches the calendar until Add is clicked.
# ----------------------------------------------------------------------------

class DateTile(QFrame):
    """FRI / 31, like a little calendar page."""

    def __init__(self, when):
        super().__init__()
        self.setFixedSize(40, 44)
        self.top, self.day = f"{when:%a}".upper(), f"{when:%d}"

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 8, 8)
        p.fillPath(path, QColor("white"))
        p.save()
        p.setClipPath(path)
        p.fillRect(QRectF(r.left(), r.top(), r.width(), 14), QColor(theme.CHANNEL["calendar"]))
        p.restore()
        p.setPen(QPen(QColor(theme.LINE), 1))
        p.drawPath(path)
        p.setPen(QColor("white"))
        p.setFont(theme.mono(6.5))
        p.drawText(QRectF(r.left(), r.top(), r.width(), 14), Qt.AlignmentFlag.AlignCenter, self.top)
        p.setPen(QColor(theme.INK))
        p.setFont(theme.mono(12))
        p.drawText(QRectF(r.left(), r.top() + 14, r.width(), r.height() - 14), Qt.AlignmentFlag.AlignCenter, self.day)
        p.end()


class EventCard(Card):
    """A calendar event the model proposed. Nothing reaches the calendar until Add is clicked."""
    add = Signal(object)  # EventProposal
    discard = Signal(object)

    def __init__(self, event):
        super().__init__(theme.CHANNEL["calendar"])
        self.proposal = event  # not self.event: that would hide QWidget.event()
        lay = _card_layout(self)
        body = QHBoxLayout()
        body.setSpacing(10)
        body.addWidget(DateTile(event.start), 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        head = QHBoxLayout()
        head.addWidget(crumb("event › calendar", self.color.name()), 1)
        self.state_label = QLabel("not added")
        self.state_label.setObjectName("meta")
        head.addWidget(self.state_label)
        text.addLayout(head)
        title = QLabel(f"<b>{html_escape(event.summary)}</b>")
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setWordWrap(True)
        text.addWidget(title)
        place = f" · {event.location}" if event.location else ""
        when = QLabel(f"{event.start:%H:%M} → {event.end:%H:%M}{place}")
        when.setObjectName("meta")
        when.setStyleSheet(f"color: {theme.MUTED};")
        text.addWidget(when)
        if event.notes:
            notes = QLabel(event.notes)
            notes.setObjectName("muted")
            notes.setWordWrap(True)
            text.addWidget(notes)
        body.addLayout(text, 1)
        lay.addLayout(body)
        row = QHBoxLayout()
        self.btn_discard = QPushButton("dismiss")
        self.btn_discard.setObjectName("ghost")
        self.btn_add = QPushButton("add  ＋")
        self.btn_add.setObjectName("primary")
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_discard.clicked.connect(lambda: self.discard.emit(self.proposal))
        self.btn_add.clicked.connect(self._clicked_add)
        row.addStretch(1)
        row.addWidget(self.btn_discard)
        row.addWidget(self.btn_add)
        lay.addLayout(row)

    def _clicked_add(self) -> None:
        self.btn_add.setEnabled(False)
        self.btn_add.setText("adding…")
        self.add.emit(self.proposal)

    def mark_added(self, ok: bool, text: str) -> None:
        if ok:
            self.state_label.setText("ADDED ✓")
            self.state_label.setStyleSheet(f"color: {theme.SUCCESS};")
            self.btn_add.hide()
            self.btn_discard.hide()
            self.succeed()
        else:
            self.btn_add.setEnabled(True)
            self.btn_add.setText("add  ＋")
            self.state_label.setText("not added")
            self.state_label.setToolTip(text)


# ----------------------------------------------------------------------------
# Link card
#   A link from a message: opened only by a click.
# ----------------------------------------------------------------------------

class LinkCard(Card):
    """A link from a message or email: shown, opened only when the user clicks Open."""
    open = Signal(str)
    discard = Signal(object)

    def __init__(self, link):
        super().__init__(theme.WARN)
        self.proposal = link
        lay = _card_layout(self)
        lay.addWidget(crumb("link › from a message", theme.WARN))
        url = QLabel(link.url)
        url.setObjectName("meta")
        url.setStyleSheet(f"color: {theme.INK}; font-size: 9pt;")
        url.setWordWrap(True)
        url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(url)
        row = QHBoxLayout()
        note = QLabel("not from you · check before opening")
        note.setObjectName("faint")
        row.addWidget(note, 1)
        dismiss = QPushButton("dismiss")
        dismiss.setObjectName("ghost")
        self.btn_open = QPushButton("open  ↗")
        self.btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.clicked.connect(lambda: self.discard.emit(self.proposal))
        self.btn_open.clicked.connect(lambda: (self.open.emit(self.proposal.url), self.discard.emit(self.proposal)))
        row.addWidget(dismiss)
        row.addWidget(self.btn_open)
        lay.addLayout(row)


# ----------------------------------------------------------------------------
# Person / group card ("who is Ali?")
#   Shown next to Ken's summary. Initials, a row of numbers from the chat, what's waiting for
#   your reply, and quick actions:
#     reply   -> asks Ken for a draft (one AI request, only on click)
#     watch   -> "tell me when they write", without the model
#     remind  -> puts "Remind me to reply to X at " in the input box for you to finish
#     ★       -> marks them important: their nudges are spoken aloud
# ----------------------------------------------------------------------------

class Initials(QWidget):
    """A round badge with up to two initials, in the WhatsApp colour (a group gets a ring)."""

    def __init__(self, name: str, group: bool):
        super().__init__()
        self.setFixedSize(40, 40)
        words = [w for w in re.split(r"[\s\-_]+", name.strip()) if w and not w.startswith("+")]
        self.text = "".join(w[0] for w in words[:2]).upper() or "#"
        self.group = group

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        color = QColor(theme.CHANNEL["whatsapp"])
        fill = QColor(color)
        fill.setAlphaF(0.14)
        p.setBrush(fill)
        p.setPen(QPen(color, 2 if self.group else 1))
        p.drawEllipse(r)
        p.setPen(color.darker(130))
        p.setFont(theme.font(10.5, QFont.Weight.DemiBold))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text)
        p.end()


class PersonCard(Card):
    reply = Signal(object)  # PersonProposal
    watch = Signal(object)
    remind = Signal(object)
    star = Signal(object)
    shortcut = Signal(object)  # "＋ shortcut": the app asks for the word(s)
    discard = Signal(object)

    def __init__(self, person, important: bool = False):
        super().__init__(theme.CHANNEL["whatsapp"])
        self.proposal = person
        lay = _card_layout(self)

        # --------------------------------------------------------------------
        # Head: badge, name, breadcrumb, star
        # --------------------------------------------------------------------
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(Initials(person.name, person.is_group), 0, Qt.AlignmentFlag.AlignTop)
        names = QVBoxLayout()
        names.setSpacing(1)
        names.addWidget(crumb(f"{'group' if person.is_group else 'person'} › whatsapp", self.color.name()))
        title = QLabel(f"<b>{html_escape(person.name)}</b>")
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setWordWrap(True)
        names.addWidget(title)
        if person.number:
            num = QLabel(f"+{person.number}")
            num.setObjectName("faint")
            names.addWidget(num)
        head.addLayout(names, 1)
        self.btn_star = QPushButton()
        self.btn_star.setObjectName("ghost")
        self.btn_star.setFixedSize(30, 30)
        self.btn_star.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_star.clicked.connect(lambda: self.star.emit(self.proposal))
        self.set_important(important)
        head.addWidget(self.btn_star, 0, Qt.AlignmentFlag.AlignTop)
        # dismiss sits up here as a small ×: six buttons in the action row didn't fit the 448 px panel
        close = QPushButton("×")
        close.setObjectName("ghost")
        close.setFixedSize(30, 30)
        close.setStyleSheet("padding: 0; font-size: 12pt;")
        close.setToolTip("Dismiss")
        close.clicked.connect(lambda: self.discard.emit(self.proposal))
        head.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)

        # --------------------------------------------------------------------
        # Numbers and what's waiting
        # --------------------------------------------------------------------
        s = person.stats
        if s is not None and (s.sent or s.received):
            facts = f" · {person.facts} saved fact{'s' if person.facts != 1 else ''}" if person.facts else ""
            numbers = QLabel(f"↑ {s.sent} sent · ↓ {s.received} received · {s.days} days{facts}")
            numbers.setObjectName("meta")
            numbers.setStyleSheet(f"color: {theme.MUTED};")
            lay.addWidget(numbers)
            if s.waiting:
                label = "open questions" if person.is_group else "waiting for your reply"
                wait = QLabel(f"<span style='color:{theme.WARN}'>● {len(s.waiting)} {label}</span>")
                wait.setTextFormat(Qt.TextFormat.RichText)
                lay.addWidget(wait)

        # --------------------------------------------------------------------
        # Actions
        # --------------------------------------------------------------------
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_shortcut = QPushButton("＋ shortcut")
        self.btn_shortcut.setObjectName("ghost")
        self.btn_shortcut.setToolTip("Your own word for them, e.g. “mom”")
        self.btn_shortcut.clicked.connect(lambda: self.shortcut.emit(self.proposal))
        row.addWidget(self.btn_shortcut)
        self.btn_watch = QPushButton("watch")
        self.btn_watch.setObjectName("ghost")
        self.btn_watch.setToolTip("Tell me as soon as they write")
        self.btn_watch.clicked.connect(lambda: (self.watch.emit(self.proposal), self.btn_watch.setEnabled(False),
                                                self.btn_watch.setText("watching ✓")))
        self.btn_remind = QPushButton("remind")
        self.btn_remind.setObjectName("ghost")
        self.btn_remind.clicked.connect(lambda: self.remind.emit(self.proposal))
        self.btn_reply = QPushButton("reply  ↵")
        self.btn_reply.setObjectName("primary")
        self.btn_reply.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reply.clicked.connect(lambda: self.reply.emit(self.proposal))
        for b in (self.btn_watch, self.btn_remind, self.btn_reply):
            row.addWidget(b)
        lay.addLayout(row)

    def set_important(self, on: bool) -> None:
        self.btn_star.setText("★" if on else "☆")
        # Segoe UI Symbol has the star; the default icon font draws something else for it.
        self.btn_star.setStyleSheet(f"color: {theme.WARN if on else theme.FAINT}; font-size: 13pt; padding: 0;"
                                    "font-family: 'Segoe UI Symbol', 'Segoe UI';")
        self.btn_star.setToolTip("Important: their nudges are spoken aloud" if on else "Mark as important")

    def set_busy(self, busy: bool) -> None:
        self.btn_reply.setEnabled(not busy)  # reply asks the model; the rest work any time


# ----------------------------------------------------------------------------
# Shortcut card ("mom -> ماما")
#   Shown when Ken proposes a shortcut, or once at startup for contacts saved as "Mom", "ماما"…
#   Nothing is saved until Save is clicked: a wrong shortcut would mean messaging the wrong person.
# ----------------------------------------------------------------------------

def word_chips(words: list[str], target: str = "", max_n: int = 5) -> QWidget:
    """Shortcut words as separate chips, then '→ target'. Separate widgets keep the order right when
    English and Arabic words are mixed (one label with commas lets Arabic words swap places)."""
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    for w in words[:max_n]:
        row.addWidget(kbd(w))
    if len(words) > max_n:
        more = QLabel(f"+{len(words) - max_n}")
        more.setObjectName("faint")
        more.setToolTip(", ".join(words[max_n:]))
        row.addWidget(more)
    if target:
        arrow = QLabel("→")
        arrow.setObjectName("faint")
        row.addWidget(arrow)
        name = QLabel(f"<b>{html_escape(target)}</b>")
        name.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(name)
    row.addStretch(1)
    return box


class ShortcutCard(Card):
    save = Signal(object)  # ShortcutProposal
    discard = Signal(object)

    def __init__(self, proposal):
        s = proposal.shortcut
        super().__init__(theme.CHANNEL.get(s.channel, theme.ACCENT))
        self.proposal = proposal
        lay = _card_layout(self)
        head = QHBoxLayout()
        kind = "group" if s.target.endswith("@g.us") else "email" if s.channel == "gmail" else "whatsapp"
        head.addWidget(crumb(f"shortcut › {kind}" + (" › suggested" if proposal.suggested else ""), self.color.name()), 1)
        self.state_label = QLabel("not saved")
        self.state_label.setObjectName("meta")
        head.addWidget(self.state_label)
        lay.addLayout(head)
        from ..shortcuts import iso

        lay.addWidget(word_chips(s.words, s.label))
        where = iso(f"+{s.number}") if s.number else ("group chat" if kind == "group" else s.target)
        detail = QLabel(f"{where} · “send a message to {iso(s.words[0])}” will mean them")
        detail.setObjectName("faint")
        detail.setWordWrap(True)
        lay.addWidget(detail)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_discard = QPushButton("dismiss")
        self.btn_discard.setObjectName("ghost")
        self.btn_save = QPushButton("save  ＋")
        self.btn_save.setObjectName("primary")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_discard.clicked.connect(lambda: self.discard.emit(self.proposal))
        self.btn_save.clicked.connect(lambda: self.save.emit(self.proposal))
        row.addWidget(self.btn_discard)
        row.addWidget(self.btn_save)
        lay.addLayout(row)

    def mark_saved(self) -> None:
        self.state_label.setText("SAVED ✓")
        self.state_label.setStyleSheet(f"color: {theme.SUCCESS};")
        self.btn_save.hide()
        self.btn_discard.hide()
        self.succeed()


# ----------------------------------------------------------------------------
# Small parts
# ----------------------------------------------------------------------------

class BadgeButton(QPushButton):
    """Icon button with a count badge (the bell)."""

    def __init__(self, icon: str):
        super().__init__(icon)
        self.count = 0

    def set_count(self, n: int) -> None:
        self.count = n
        self.setToolTip(f"{n} new messages" if n else "Check new messages")
        self.update()

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if not self.count:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        label = "9+" if self.count > 9 else str(self.count)
        w = 13 if len(label) == 1 else 17
        r = QRectF(self.width() - w, 0, w, 13)
        p.setPen(QPen(QColor("white"), 1.5))
        p.setBrush(QColor(theme.DANGER))
        p.drawRoundedRect(r, 6.5, 6.5)
        p.setPen(QColor("white"))
        p.setFont(theme.mono(6, QFont.Weight.Bold))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)
        p.end()


class SpeakToggle(QPushButton):
    """Checkable speaker icon; the icon follows the state however it's set."""

    def __init__(self):
        super().__init__(I["mute"])
        self.setCheckable(True)

    def checkStateSet(self) -> None:
        super().checkStateSet()
        self.setText(I["speaker"] if self.isChecked() else I["mute"])

    def nextCheckState(self) -> None:
        super().nextCheckState()
        self.setText(I["speaker"] if self.isChecked() else I["mute"])


class InputBox(QPlainTextEdit):
    submitted = Signal()
    focused = Signal(bool)
    edited_by_user = Signal()

    def __init__(self):
        super().__init__()
        self.setPlaceholderText("Ask Ken anything")
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(32)
        self.document().contentsChanged.connect(self._fit)

    def _fit(self) -> None:
        lines = max(1, min(5, int(self.document().size().height())))
        self.setFixedHeight(12 + lines * 20)
        opt = self.document().defaultTextOption()
        rtl = is_rtl(self.toPlainText())
        opt.setTextDirection(Qt.LayoutDirection.RightToLeft if rtl else Qt.LayoutDirection.LeftToRight)
        self.document().setDefaultTextOption(opt)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            return
        if e.text():
            self.edited_by_user.emit()
        super().keyPressEvent(e)

    def focusInEvent(self, e) -> None:
        self.focused.emit(True)
        super().focusInEvent(e)

    def focusOutEvent(self, e) -> None:
        self.focused.emit(False)
        super().focusOutEvent(e)

    def mousePressEvent(self, e) -> None:
        self.edited_by_user.emit()
        super().mousePressEvent(e)


# ----------------------------------------------------------------------------
# The panel
#   Header, scrolling feed, status line, quick chats and the input. The paper background is drawn
#   once into a cached pixmap: repainting it every frame made the whole app run at ~10 fps.
# ----------------------------------------------------------------------------

class Panel(QWidget):
    ask = Signal(str, bool)  # text, spoken (came from the microphone)
    mic_clicked = Signal()
    settings_clicked = Signal()
    new_chat = Signal()
    check_new = Signal()
    quit_app = Signal()
    stop = Signal()  # the send button turns into Stop while the assistant works
    speak_toggled = Signal(bool)
    send_draft = Signal(object, str)
    regenerate_draft = Signal(object)
    add_event_clicked = Signal(object)  # EventProposal
    person_reply = Signal(object)  # PersonProposal: the card's buttons
    person_watch = Signal(object)
    person_star = Signal(object)
    person_shortcut = Signal(object)
    save_shortcut = Signal(object)  # ShortcutProposal: a shortcut card's Save
    open_link = Signal(str)
    brief = Signal()
    escape = Signal()

    HEADER_H = 52  # drag the panel by this strip
    SUGGESTIONS = ["What's new on WhatsApp?", "Summarize my latest emails", "☀  Brief me"]

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(PANEL_W + 2 * SHADOW, PANEL_H + 2 * SHADOW)
        self.cards: dict[str, DraftCard | EventCard | LinkCard] = {}
        self._busy = False
        self._from_voice = False  # the text in the input box was dictated (a correction keeps it spoken)
        self._drag_from: QPoint | None = None
        self.user_moved = False  # once dragged, the panel stays where you put it
        self._auto_send = QTimer(self, singleShot=True, interval=1800, timeout=self._submit)
        self._bg: QPixmap | None = None  # shadow + paper, drawn once (see paintEvent)
        self._status_text = ""
        self._cursor_on = True
        self._cursor = QTimer(self, interval=530, timeout=self._blink)

        self.card = QFrame(self)
        self.card.setGeometry(SHADOW, SHADOW, PANEL_W, PANEL_H)
        self.card.setObjectName("panelCard")
        self.card.setStyleSheet("QFrame#panelCard { background: transparent; }")

        root = QVBoxLayout(self.card)
        root.setContentsMargins(16, 12, 16, 10)
        root.setSpacing(8)
        root.addLayout(self._header())

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.feed = QWidget()
        self.feed_layout = QVBoxLayout(self.feed)
        self.feed_layout.setContentsMargins(0, 2, 6, 4)
        self.feed_layout.setSpacing(0)
        self.feed_layout.addStretch(1)
        self.scroll.setWidget(self.feed)
        self.scroll.verticalScrollBar().rangeChanged.connect(lambda _min, mx: self.scroll.verticalScrollBar().setValue(mx))
        root.addWidget(self.scroll, 1)
        self.empty = None
        self._empty_state()

        status_row = QHBoxLayout()
        status_row.setContentsMargins(2, 0, 0, 0)
        status_row.setSpacing(6)
        self.thinking = Avatar(22, 24)  # the gyroscope, small, while Ken works
        self.thinking.set_state(THINK)
        self.thinking.hide()
        self.status = QLabel("")
        self.status.setObjectName("statusLine")
        status_row.addWidget(self.thinking)
        status_row.addWidget(self.status, 1)
        status_holder = QWidget()
        status_holder.setFixedHeight(24)
        status_holder.setLayout(status_row)
        root.addWidget(status_holder)
        root.addWidget(self._quick_bar())
        root.addWidget(self._input_bar())

        footer = QHBoxLayout()
        footer.setContentsMargins(2, 4, 2, 0)
        self.connections = QLabel("")
        self.connections.setObjectName("statusLine")
        self.connections.setTextFormat(Qt.TextFormat.RichText)
        footer.addWidget(self.connections)
        footer.addStretch(1)
        self.usage = QLabel("")
        self.usage.setObjectName("statusLine")
        footer.addWidget(self.usage)
        root.addLayout(footer)
        self._conn = {}
        self.set_connections({})

        self.overlay = FlyOverlay(self.card)
        self.overlay.setGeometry(0, 0, PANEL_W, PANEL_H)

    # ------------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------------

    def _header(self) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(5)
        self.avatar = Avatar(36, 40)
        names = QVBoxLayout()
        names.setSpacing(0)
        title = QLabel("Ken")
        title.setObjectName("title")
        self.subtitle = QLabel("● online")
        self.subtitle.setObjectName("subtitle")
        names.addWidget(title)
        names.addWidget(self.subtitle)
        h.addWidget(self.avatar)
        h.addSpacing(4)
        h.addLayout(names)
        h.addStretch(1)

        self.speak_switch = SpeakToggle()  # read every reply aloud
        self.speak_switch.toggled.connect(self.speak_toggled.emit)
        buttons = [(self.speak_switch, "Read every reply aloud")]
        for icon, tip, sig in ((I["sun"], "Brief me", self.brief), (I["bell"], "Check new messages", self.check_new),
                               (I["refresh"], "New conversation", self.new_chat), (I["settings"], "Settings", self.settings_clicked),
                               (I["close"], "Hide  ·  esc", self.escape), (I["power"], "Quit Ken", self.quit_app)):
            b = BadgeButton(icon) if icon == I["bell"] else QPushButton(icon)
            if icon == I["bell"]:
                self.bell = b
            b.clicked.connect(sig.emit)
            buttons.append((b, tip))
        for b, tip in buttons:
            b.setObjectName("icon")
            b.setFixedSize(27, 27)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            h.addWidget(b)
        return h

    def _empty_state(self) -> None:
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(6, 10, 6, 6)
        lay.setSpacing(6)
        lay.addStretch(1)
        self.hero = Avatar(124, 64)
        lay.addWidget(self.hero, 0, Qt.AlignmentFlag.AlignHCenter)
        hour = time.localtime().tm_hour
        greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        hello = QLabel(f"{greeting}, Sir.")
        hello.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hello.setStyleSheet("font-size: 14.5pt; font-weight: 600;")
        sub = QLabel("ken › how may I help?")
        sub.setObjectName("prompt")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hello)
        lay.addWidget(sub)
        lay.addSpacing(8)
        for n, text in enumerate(self.SUGGESTIONS, 1):
            b = QPushButton(text)
            b.setObjectName("suggestion")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            inner = QHBoxLayout(b)
            inner.setContentsMargins(0, 0, 10, 0)
            inner.addStretch(1)
            key = kbd(str(n))
            key.setFixedHeight(18)
            inner.addWidget(key, 0, Qt.AlignmentFlag.AlignVCenter)
            b.clicked.connect(lambda _=False, i=n - 1: self._suggest(i))
            lay.addWidget(b)
        lay.addStretch(1)
        self.empty = holder
        self.feed_layout.addWidget(holder)

    def _suggest(self, index: int) -> None:
        if index == 2:
            self.brief.emit()
            return
        self.input.setPlainText(self.SUGGESTIONS[index])
        self._submit()

    # ------------------------------------------------------------------------
    # Quick chats: tap a person, then just say what to tell them
    # ------------------------------------------------------------------------

    def _quick_bar(self) -> QWidget:
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(5)
        self._quick_layout = QHBoxLayout()
        self._quick_layout.setSpacing(5)
        self.target_tag = QPushButton()
        self.target_tag.setObjectName("targetTag")
        self.target_tag.setCursor(Qt.CursorShape.PointingHandCursor)
        self.target_tag.setToolTip("Click to clear")
        self.target_tag.clicked.connect(lambda: self.set_target(None))
        self.target_tag.hide()
        lay.addLayout(self._quick_layout)
        lay.addWidget(self.target_tag)
        lay.addStretch(1)
        self.target = None
        return holder

    def set_quick_chats(self, refs: list) -> None:
        while self._quick_layout.count():
            item = self._quick_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for ref in refs[:5]:
            chip = QPushButton(("#" if ref.is_group else "@") + _short(ref.label))
            chip.setObjectName("quickChat")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(f"{ref.label} · {'group' if ref.is_group else '+' + ref.number}\nClick, then type what to tell them")
            chip.clicked.connect(lambda _=False, r=ref: self.set_target(r))
            self._quick_layout.addWidget(chip)

    def _show_quick(self, on: bool) -> None:
        for i in range(self._quick_layout.count()):
            self._quick_layout.itemAt(i).widget().setVisible(on)

    def set_target(self, ref) -> None:
        self.target = ref
        if ref is None:
            self.target_tag.hide()
            self._show_quick(True)
            self.input.setPlaceholderText("Ask Ken anything")
        else:
            self.target_tag.setText(f"to @{_short(ref.label)}  ✕")
            self.target_tag.show()
            self._show_quick(False)
            self.input.setPlaceholderText(f"What should I tell {ref.label}?")
        self.input.setFocus()

    def _input_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("inputBar")
        self.input_bar = bar
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 4, 4, 4)
        lay.setSpacing(5)
        prompt = QLabel("›")
        prompt.setObjectName("prompt")
        prompt.setStyleSheet("font-size: 12pt;")
        self.input = InputBox()
        self.input.submitted.connect(self._submit)
        self.input.edited_by_user.connect(self._auto_send.stop)
        self.input.focused.connect(lambda f: (bar.setProperty("focused", f), bar.style().unpolish(bar), bar.style().polish(bar)))
        self.mic = QPushButton(I["mic"])
        self.mic.setObjectName("mic")
        self.mic.setFixedSize(30, 30)
        self.mic.setToolTip('Talk (or hold the hotkey, or say "Ken")')
        self.mic.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic.clicked.connect(self.mic_clicked.emit)
        self.send_btn = QPushButton(I["send"])
        self.send_btn.setObjectName("send")
        self.send_btn.setFixedSize(30, 30)
        self.send_btn.setToolTip("Send  ·  ↵")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.clicked.connect(lambda: self.stop.emit() if self._busy else self._submit())
        lay.addWidget(prompt, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.input, 1, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.mic, 0, Qt.AlignmentFlag.AlignBottom)
        lay.addWidget(self.send_btn, 0, Qt.AlignmentFlag.AlignBottom)
        return bar

    # ------------------------------------------------------------------------
    # Painting and placement
    # ------------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        # Every animated child (avatar, flying particles...) repaints the panel behind it, so the
        # background must be cheap: a cached picture, not a live drawing.
        dpr = self.devicePixelRatioF()
        if self._bg is None or self._bg.devicePixelRatio() != dpr:
            self._bg = self._render_background(dpr)
        p = QPainter(self)
        p.drawPixmap(0, 0, self._bg)
        p.end()

    def _render_background(self, dpr: float) -> QPixmap:
        pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(SHADOW, SHADOW, PANEL_W, PANEL_H)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(SHADOW, 0, -2):  # soft shadow: many faint layers add up near the card
            p.setBrush(QColor(20, 40, 60, 4))
            p.drawRoundedRect(r.adjusted(-i, -i + 6, i, i + 6), 20 + i, 20 + i)
        theme.paint_paper(p, r, 20)
        path = QPainterPath()
        path.addRoundedRect(r, 20, 20)
        p.setPen(QPen(QColor("white"), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(QPen(QColor(theme.LINE), 1))
        p.drawLine(QPointF(r.left() + 16, r.top() + self.HEADER_H + 8), QPointF(r.right() - 16, r.top() + self.HEADER_H + 8))
        p.end()
        return pm

    def place_near(self, pill_geometry) -> None:
        """Above the pill (below it if there's no room), centred on it, kept on screen."""
        if self.user_moved:
            self.show()
            return
        screen = self.screen().availableGeometry()
        x = pill_geometry.center().x() - self.width() // 2
        y = pill_geometry.top() - self.height() + SHADOW + 4
        if y < screen.top():
            y = pill_geometry.bottom() - SHADOW
        x = max(screen.left() - SHADOW, min(x, screen.right() - self.width() + SHADOW))
        y = max(screen.top() - SHADOW, min(y, screen.bottom() - self.height() + SHADOW))
        self.move(x, y)
        self.show()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < SHADOW + self.HEADER_H + 8:
            self._drag_from = e.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._drag_from is not None:
            self.move(e.globalPosition().toPoint() - self._drag_from)
            self.user_moved = True
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._drag_from = None
        self.unsetCursor()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.escape.emit()
            return
        if self.empty is not None and not self.input.toPlainText() and e.text() in ("1", "2", "3"):
            self._suggest(int(e.text()) - 1)  # the numbered suggestions
            return
        super().keyPressEvent(e)

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.input.setFocus()

    # ------------------------------------------------------------------------
    # The feed: adding bubbles and cards
    # ------------------------------------------------------------------------

    def _add(self, widget: QWidget, align: Qt.AlignmentFlag, caption: str = "", appear: bool = True) -> Row:
        if self.empty is not None:
            self.empty.hide()  # deleteLater alone can leave it on screen until the next event loop pass
            self.empty.deleteLater()
            self.empty = None
        row = Row(widget, align, caption)
        self.feed_layout.addWidget(row)
        if appear:
            row.appear()
        return row

    def _rect_in_card(self, w: QWidget) -> QRectF:
        top_left = w.mapTo(self.card, QPoint(0, 0))
        return QRectF(top_left.x(), top_left.y(), w.width(), w.height())

    def add_user(self, text: str) -> None:
        """The message leaves the input box as particles that land and become the bubble."""
        bubble = Bubble(text, "user")
        row = self._add(bubble, Qt.AlignmentFlag.AlignRight, caption=time.strftime("%H:%M"), appear=False)
        src = self._rect_in_card(self.input_bar) if self.isVisible() else None

        def launch():
            if src is None or not self.isVisible():
                row.appear()
                return
            self.overlay.fly(src.adjusted(20, 6, -60, -6), self._rect_in_card(bubble), theme.INK, on_arrive=row.appear)
        QTimer.singleShot(40, launch)  # after the layout has placed (and scrolled to) the bubble

    def add_assistant(self, text: str) -> None:
        """Ken's reply streams out of his avatar and types itself."""
        if not text.strip():
            return
        bubble = Bubble(text, "assistant")
        row = self._add(bubble, Qt.AlignmentFlag.AlignLeft, caption="ken ›", appear=False)

        def launch():
            if not self.isVisible():
                row.appear()
                return
            self.overlay.fly(self._rect_in_card(self.avatar), self._rect_in_card(bubble), theme.ACCENT,
                             on_arrive=lambda: (row.appear(), bubble.type_out()), n=16, ms=460)
        QTimer.singleShot(40, launch)

    def add_notice(self, text: str, kind: str = "info") -> None:
        label = QLabel(text)
        label.setObjectName("errorNotice" if kind == "error" else "notice")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._add(label, Qt.AlignmentFlag.AlignCenter)

    def add_draft(self, draft: Draft) -> DraftCard:
        card = DraftCard(draft)
        card.send.connect(self.send_draft.emit)
        card.regenerate.connect(self.regenerate_draft.emit)
        card.discard.connect(self._discard)
        card.set_busy(self._busy)
        self.cards[draft.id] = card
        self._add(card, Qt.AlignmentFlag.AlignJustify)
        return card

    def add_link(self, link) -> LinkCard:
        card = LinkCard(link)
        card.open.connect(self.open_link.emit)
        card.discard.connect(self._discard)
        self.cards[link.id] = card
        self._add(card, Qt.AlignmentFlag.AlignJustify)
        return card

    def add_event(self, event) -> EventCard:
        card = EventCard(event)
        card.add.connect(self.add_event_clicked.emit)
        card.discard.connect(self._discard)
        self.cards[event.id] = card
        self._add(card, Qt.AlignmentFlag.AlignJustify)
        return card

    def add_person(self, person, important: bool = False) -> PersonCard:
        card = PersonCard(person, important)
        card.reply.connect(self.person_reply.emit)
        card.watch.connect(self.person_watch.emit)
        card.star.connect(self.person_star.emit)
        card.shortcut.connect(self.person_shortcut.emit)
        card.remind.connect(self._remind_about)
        card.discard.connect(self._discard)
        card.set_busy(self._busy)
        old = self.cards.pop(person.id, None)  # asked twice: keep only the newest card
        if old is not None:
            old.parentWidget().hide()
        self.cards[person.id] = card
        self._add(card, Qt.AlignmentFlag.AlignJustify)
        return card

    def add_shortcut(self, proposal) -> ShortcutCard:
        card = ShortcutCard(proposal)
        card.save.connect(self.save_shortcut.emit)
        card.discard.connect(self._discard)
        self.cards[proposal.id] = card
        self._add(card, Qt.AlignmentFlag.AlignJustify)
        return card

    def _remind_about(self, person) -> None:
        """'remind me': the sentence is started for you; you add the time and press Enter."""
        self.input.setPlainText(f"Remind me to reply to {person.name} at ")
        self.input.setFocus()
        self.input.moveCursor(self.input.textCursor().MoveOperation.End)

    def _discard(self, draft) -> None:
        card = self.cards.pop(draft.id, None)
        if card is not None:
            row = card.parentWidget()
            row.hide()
            row.deleteLater()

    def clear(self) -> None:
        while self.feed_layout.count() > 1:
            item = self.feed_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self.cards.clear()
        self.empty = None
        self._empty_state()

    # ------------------------------------------------------------------------
    # State: mode, connections, busy, status line, recording
    # ------------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        """Mirror the pill: idle / listen / think / speak / done / sleep."""
        self.avatar.set_state(mode)
        if self.empty is not None:
            self.hero.set_state(mode)
        colors = {LISTEN: theme.ACCENT_2, THINK: theme.VIOLET}
        self.subtitle.setText({IDLE: "● online", LISTEN: "● listening", THINK: "● thinking", SPEAK: "● speaking",
                               DONE: "● done", SLEEP: "● resting"}.get(mode, "● online"))
        self.subtitle.setStyleSheet(f"color: {colors.get(mode, theme.ACCENT)};")

    def set_connections(self, conn: dict) -> None:
        self._conn = conn
        parts = []
        for name, label in (("whatsapp", "wa"), ("gmail", "gmail"), ("calendar", "cal")):
            ok = conn.get(name)
            color = theme.FAINT if ok is None else theme.ACCENT if ok else theme.DANGER
            parts.append(f"<span style='color:{color}'>●</span> {label}")
        self.connections.setText("&nbsp;&nbsp;".join(parts))

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.send_btn.setText(I["stop"] if busy else I["send"])
        self.send_btn.setToolTip("Stop" if busy else "Send  ·  ↵")
        self.send_btn.setProperty("stopping", busy)
        self.send_btn.style().unpolish(self.send_btn)
        self.send_btn.style().polish(self.send_btn)
        for card in list(self.cards.values()):
            try:
                card.set_busy(busy)
            except Exception as exc:  # one broken card must never leave the others' buttons disabled
                print(f"  ⚠  card {type(card).__name__}: {exc}")

    def set_status(self, text: str, robot: bool = True) -> None:
        """While Ken works: the small gyroscope and `ken › reading whatsapp▍`."""
        working = bool(text) and robot
        self.thinking.setVisible(working)
        if not text:
            self._status_text = ""
        elif not robot:
            self._status_text = text
        else:
            step = "thinking" if text.startswith("Thinking") else text.rstrip("…. ").lower()
            self._status_text = f"ken › {step}"
        if working and not self._cursor.isActive():
            self._cursor.start()
        elif not working:
            self._cursor.stop()
        self._cursor_on = True
        self._render_status()

    def _blink(self) -> None:
        self._cursor_on = not self._cursor_on
        self._render_status()

    def _render_status(self) -> None:
        cursor = "▍" if self._cursor.isActive() and self._cursor_on else (" " if self._cursor.isActive() else "")
        self.status.setText(self._status_text + cursor)

    def set_recording(self, on: bool) -> None:
        self.mic.setProperty("recording", on)
        self.mic.setText(I["stop"] if on else I["mic"])
        self.mic.style().unpolish(self.mic)
        self.mic.style().polish(self.mic)

    def put_transcript(self, text: str) -> None:
        """Show what was heard; send it after a short pause unless the user starts editing."""
        self.input.setPlainText(text)
        self.input.moveCursor(self.input.textCursor().MoveOperation.End)
        self._from_voice = bool(text)
        if text:
            self.set_status("sending in a moment · click the text to edit", robot=False)
            self._auto_send.start()

    def send_spoken(self, text: str) -> None:
        """A spoken request while the panel is closed: send it now (nobody is there to edit it)."""
        self.input.setPlainText(text)
        self._from_voice = True
        self._submit()

    def _submit(self) -> None:
        self._auto_send.stop()
        text = self.input.toPlainText().strip()
        if not text or self._busy:
            return
        spoken, self._from_voice = self._from_voice, False
        self.input.clear()
        self.set_status("")
        if self.target is not None:  # a quick chat is selected: draft a message to it
            ref = self.target
            who = f"{ref.label} ({'group' if ref.is_group else '+' + ref.number})"
            self.add_user(f"To {ref.label}: {text}")
            self.set_target(None)
            self.ask.emit(f"Write a WhatsApp message to {who}, chat JID {ref.jid}. What I want to say: {text}\n"
                          "Don't ask me anything: call my_past_messages for this chat, then show_draft.", spoken)
            return
        self.add_user(text)
        self.ask.emit(text, spoken)
