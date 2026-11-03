"""The send confirmation dialog (safety rule 1), the "new messages" popup and the settings window,
in the Frost console style."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QRectF, Qt, QTime, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSlider, QStackedWidget, QTimeEdit, QVBoxLayout, QWidget,
)

from ..agent import SendRequest
from ..config import Settings
from . import theme
from .panel import AutoTextEdit, crumb, is_rtl, kbd
from .winutil import dark_title_bar

# ----------------------------------------------------------------------------
# Voice lists and preview lines
# ----------------------------------------------------------------------------

# (value, label). The voice's accent follows the style instructions (the butler asks for British
# English), so these describe the voice itself; the first four suit a butler best.
OPENAI_VOICES = [
    ("ballad", "Ballad ★ warm, expressive man"),
    ("cedar", "Cedar ★ deep, natural man"),
    ("fable", "Fable · storyteller man"),
    ("onyx", "Onyx · very deep man"),
    ("ash", "Ash · clear man"),
    ("echo", "Echo · smooth man"),
    ("verse", "Verse · lively man"),
    ("marin", "Marin · natural woman"),
    ("coral", "Coral · warm woman"),
    ("sage", "Sage · calm woman"),
    ("shimmer", "Shimmer · bright woman"),
    ("nova", "Nova · young woman"),
    ("alloy", "Alloy · neutral"),
]
EDGE_VOICES_EN = [
    ("en-GB-RyanNeural", "Ryan ★ British man"),
    ("en-GB-ThomasNeural", "Thomas · British man"),
    ("en-GB-SoniaNeural", "Sonia · British woman"),
    ("en-GB-LibbyNeural", "Libby · British woman"),
    ("en-US-AndrewNeural", "Andrew · American man"),
    ("en-US-GuyNeural", "Guy · American man"),
    ("en-US-AriaNeural", "Aria · American woman"),
]
EDGE_VOICES_AR = [
    ("ar-EG-ShakirNeural", "Shakir ★ Egyptian man"),
    ("ar-EG-SalmaNeural", "Salma · Egyptian woman"),
    ("ar-SA-HamedNeural", "Hamed · Saudi man"),
    ("ar-SA-ZariyahNeural", "Zariyah · Saudi woman"),
]
PREVIEW_TEXT_AR = {"butler": "Sir، أخوك بيسأل عن يوم الجمعة. أكتبله رد؟",
                   "plain": "أخوك بيسأل عن يوم الجمعة. أكتبله رد؟"}
PREVIEW_TEXT = {"butler": "Sir, your brother asked about Friday. Shall I draft a reply?",
                "plain": "Your brother asked about Friday. Want me to draft a reply?"}


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def friendly_recipient(recipient: str, channel: str) -> str:
    """'201001234567@s.whatsapp.net' -> '+201001234567'. Groups and emails stay as they are."""
    if channel == "whatsapp":
        user, _, domain = recipient.partition("@")
        if user.isdigit() and domain in ("", "s.whatsapp.net", "c.us"):
            return f"+{user}"
    return recipient


# ----------------------------------------------------------------------------
# Frameless paper dialogs
# ----------------------------------------------------------------------------

class GlassDialog(QDialog):
    """Frameless rounded dialog on console paper, matching the panel."""

    W, PAD = 420, 18

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(self.PAD + 20, self.PAD + 16, self.PAD + 20, self.PAD + 18)
        self.body.setSpacing(10)
        self.setFixedWidth(self.W + 2 * self.PAD)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(self.PAD, 0, -2):
            p.setBrush(QColor(20, 40, 60, 5))
            p.drawRoundedRect(r.adjusted(-i, -i + 5, i, i + 5), 18 + i, 18 + i)
        theme.paint_paper(p, r, 18)
        path = QPainterPath()
        path.addRoundedRect(r, 18, 18)
        p.setPen(QPen(QColor("white"), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.end()


# ----------------------------------------------------------------------------
# The send confirmation (safety rule 1)
#   Shows exactly what will be sent. Only a mouse click on Send accepts; Enter does nothing.
# ----------------------------------------------------------------------------

class ConfirmSendDialog(GlassDialog):
    """Shows the exact channel, recipients, subject and text. Nothing is sent unless Send is clicked."""

    def __init__(self, request: SendRequest, parent: QWidget | None = None):
        super().__init__(parent)
        channel = {"whatsapp": "whatsapp", "gmail": "email"}.get(request.channel, "message")
        color = theme.CHANNEL.get(request.channel, theme.ACCENT)
        to = ", ".join(friendly_recipient(r, request.channel) for r in request.recipients)

        title = QLabel("Send this?")
        title.setObjectName("title")
        self.body.addWidget(title)
        path = crumb(f"send › {channel} › {to}", color)
        path.setWordWrap(True)
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.addWidget(path)
        raw = ", ".join(request.recipients)
        if raw != to:
            exact = QLabel(raw)  # what is actually sent, always shown
            exact.setObjectName("meta")
            exact.setWordWrap(True)
            self.body.addWidget(exact)
        if request.subject:
            subj = QLabel(f"<span style='color:{theme.FAINT}'>subject</span>&nbsp;&nbsp;{escape(request.subject)}")
            subj.setTextFormat(Qt.TextFormat.RichText)
            subj.setWordWrap(True)
            self.body.addWidget(subj)
        cc = request.arguments.get("cc")
        if cc:
            ccl = QLabel(f"<span style='color:{theme.FAINT}'>cc</span>&nbsp;&nbsp;"
                         f"{escape(', '.join(cc) if isinstance(cc, list) else str(cc))}")
            ccl.setTextFormat(Qt.TextFormat.RichText)
            self.body.addWidget(ccl)

        text = AutoTextEdit(min_h=48, max_h=260)
        text.setReadOnly(True)
        text.setObjectName("draftText")
        text.setStyleSheet("QTextEdit { background: white; }")
        if is_rtl(request.text):
            opt = text.document().defaultTextOption()
            opt.setTextDirection(Qt.LayoutDirection.RightToLeft)
            opt.setAlignment(Qt.AlignmentFlag.AlignRight)
            text.document().setDefaultTextOption(opt)
        text.setPlainText(request.text)
        self.body.addWidget(text)

        row = QHBoxLayout()
        note = QLabel("Ken sends only after you confirm. This can't be undone.")
        note.setObjectName("faint")
        note.setWordWrap(True)
        row.addWidget(note, 1)
        cancel = QPushButton("cancel  esc")
        cancel.setObjectName("ghost")
        send = QPushButton("send")
        send.setObjectName("primary")
        send.setMinimumWidth(84)
        cancel.clicked.connect(self.reject)
        send.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(send)
        self.body.addLayout(row)
        cancel.setDefault(True)  # Enter does NOT send by accident
        cancel.setFocus()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return  # only a click on Send sends
        super().keyPressEvent(e)


# ----------------------------------------------------------------------------
# What's new
# ----------------------------------------------------------------------------

class InboxDialog(GlassDialog):
    """'What's new': one row per chat or email with a Reply button. No AI involved."""

    reply = Signal(object)  # InboxItem

    W = 440

    def __init__(self, items: list, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedWidth(self.W + 2 * self.PAD)
        head = QHBoxLayout()
        title = QLabel(f"New · {len(items)}" if items else "You're all caught up")
        title.setObjectName("title")
        head.addWidget(title, 1)
        close = QPushButton(theme.ICONS["close"])
        close.setObjectName("icon")
        close.setFixedSize(27, 27)
        close.clicked.connect(self.accept)
        head.addWidget(close)
        self.body.addLayout(head)
        if not items:
            note = QLabel("Nothing new on WhatsApp or Gmail since you last checked.")
            note.setObjectName("muted")
            note.setWordWrap(True)
            self.body.addWidget(note)
            return

        box = QFrame()
        box.setObjectName("tile")
        rows = QVBoxLayout(box)
        rows.setContentsMargins(12, 4, 12, 4)
        rows.setSpacing(0)
        for n, item in enumerate(items):
            rows.addWidget(self._row(item, last=n == len(items) - 1))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(box)
        scroll.setMaximumHeight(460)
        scroll.setMinimumHeight(min(460, 74 * len(items) + 10))
        self.body.addWidget(scroll)

    def _row(self, item, last: bool) -> QWidget:
        row = QWidget()
        if not last:
            row.setStyleSheet(f"QWidget#inboxRow {{ border-bottom: 1px solid {theme.LINE}; }}")
            row.setObjectName("inboxRow")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 9, 0, 9)
        lay.setSpacing(10)
        color = theme.CHANNEL.get(item.channel, theme.MUTED)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {color}; font-size: 9pt;")
        lay.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(1)
        top = QHBoxLayout()
        name = item.sender_name or (f"+{item.sender}" if item.channel == "whatsapp" else item.sender)
        who = QLabel(f"<b>{escape(('#' + item.group) if item.group else name)}</b>")
        who.setTextFormat(Qt.TextFormat.RichText)
        top.addWidget(who, 1)
        when = QLabel(item.time + (f" · +{item.more}" if item.more else ""))
        when.setObjectName("meta")
        top.addWidget(when)
        text.addLayout(top)
        if item.group:  # who wrote it, on its own line (an Arabic group name scrambles a mixed line)
            by = QLabel(name)
            by.setObjectName("faint")
            text.addWidget(by)
        preview = QLabel(item.text if len(item.text) < 160 else item.text[:157] + "…")
        preview.setObjectName("muted")
        preview.setWordWrap(True)
        if is_rtl(item.text):
            preview.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            preview.setAlignment(Qt.AlignmentFlag.AlignRight)
        text.addWidget(preview)
        lay.addLayout(text, 1)
        reply = QPushButton("reply")
        reply.setCursor(Qt.CursorShape.PointingHandCursor)
        reply.clicked.connect(lambda: (self.reply.emit(item), self.accept()))
        lay.addWidget(reply, 0, Qt.AlignmentFlag.AlignVCenter)
        return row


# ----------------------------------------------------------------------------
# Settings building blocks
#   Chips, tiles and rows used by the Settings window.
# ----------------------------------------------------------------------------

class Chips(QWidget):
    """A few choices as clickable chips (instead of a dropdown). Same API the code used on combos."""

    changed = Signal()

    def __init__(self, items: list[tuple[str, str]], current: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.values = []
        for label, value in items:
            b = QPushButton(label)
            b.setObjectName("chipButton")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(b, len(self.values))
            self.values.append(value)
            lay.addWidget(b)
            b.setChecked(value == current)
        if self.group.checkedId() < 0 and self.group.buttons():
            self.group.buttons()[0].setChecked(True)
        lay.addStretch(1)
        self.group.idClicked.connect(lambda _: self.changed.emit())

    def currentData(self) -> str:
        return self.values[max(0, self.group.checkedId())]


def tile(label: str, *widgets: QWidget, hint: str = "") -> QFrame:
    box = QFrame()
    box.setObjectName("tile")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(11, 9, 11, 10)
    lay.setSpacing(5)
    if label:
        head = QLabel(label)
        head.setObjectName("tileLabel")
        lay.addWidget(head)
    for w in widgets:
        lay.addWidget(w)
    if hint:
        note = QLabel(hint)
        note.setObjectName("faint")
        note.setWordWrap(True)
        lay.addWidget(note)
    return box


def toggle_tile(box: QCheckBox) -> QFrame:
    """A switch on a tile of its own, side by side with others."""
    t = QFrame()
    t.setObjectName("tile")
    lay = QHBoxLayout(t)
    lay.setContentsMargins(11, 9, 11, 9)
    text = QLabel(box.text())
    text.setWordWrap(True)
    box.setText("")
    lay.addWidget(text, 1)
    lay.addWidget(box)
    return t


def row_of(*widgets: QWidget) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    for x in widgets:
        lay.addWidget(x, 1 if isinstance(x, (QComboBox, QSlider)) else 0)
    return w


# ----------------------------------------------------------------------------
# The Settings window
#   A sidebar of sections, one page of tiles each, and a search box. changes() returns only what
#   changed; facts and shortcuts are edited at once by their own buttons.
# ----------------------------------------------------------------------------

class SettingsDialog(QDialog):
    """A sidebar of sections; each section is a page of small tiles. Search jumps to a section."""

    SECTIONS = [  # (name, icon, words the search box matches)
        ("General", "general", "model openai local speech text stt tone egyptian saudi english personality butler sir"),
        ("Voice", "voice", "voice engine edge openai windows ryan shakir speed read aloud out loud got it ack preview"),
        ("Wake word", "ear", "wake ken listen train hotword"),
        ("Accounts", "accounts", "calendar google whatsapp server connect"),
        ("Memory", "memory", "memory history learn style embed remember facts birthday forget"),
        ("Shortcuts", "person", "shortcuts aliases mom dad nicknames contacts words"),
        ("Nudges", "bell", "nudges proactive suggestions quiet hours reminders unanswered"),
        ("App", "settings", "pill morning briefing hotkey privacy"),
    ]

    def __init__(self, settings: Settings, voices: list[tuple[str, str]], hotkey_label: str,
                 parent: QWidget | None = None, on_learn=None, on_calendar=None, on_wake_train=None,
                 on_preview=None, facts=None, on_delete_fact=None, shortcuts=None, on_remove_shortcut=None,
                 on_add_shortcut=None, shortcut_suggestions=None, on_save_shortcut=None):
        super().__init__(parent)
        # Shortcuts: like facts, these change at once (not on save), because each row has its own button.
        self.shortcuts = list(shortcuts or [])
        self.shortcut_suggestions = list(shortcut_suggestions or [])
        self.on_remove_shortcut = on_remove_shortcut  # (id) -> None
        self.on_add_shortcut = on_add_shortcut  # (words text, who) -> (Shortcut | None, why not)
        self.on_save_shortcut = on_save_shortcut  # (Shortcut) -> Shortcut
        self.facts = list(facts or [])  # facts.Fact list for Settings → Memory
        self.on_delete_fact = on_delete_fact  # deletes at once (not on save), like the list shows
        self.on_learn = on_learn
        self.on_calendar = on_calendar
        self.on_wake_train = on_wake_train
        self.on_preview = on_preview  # (prefs dict, text) -> plays it
        self.settings = settings
        self.setWindowTitle("Ken settings")
        self.setWindowIcon(theme.app_icon())
        self.resize(720, 540)

        # --------------------------------------------------------------------
        # Controls (the same ones changes() reads)
        # --------------------------------------------------------------------
        self.backend = Chips([("OpenAI", "openai"), ("Local qwen3", "local")], settings.backend)
        self.stt = Chips([("Same as model", "auto"), ("OpenAI", "openai"), ("Local Whisper", "local")], settings.stt_backend)
        self.tone = Chips([("Egyptian", "egyptian_friendly"), ("Saudi", "saudi_professional"),
                           ("US English", "us_professional")], settings.tone)
        self.persona = Chips([("Butler · Sir", "butler"), ("Plain", "plain")], settings.persona)
        self.speak_voice = QCheckBox("Answer out loud when I talk")
        self.speak_voice.setChecked(settings.speak_after_voice)
        self.speak = QCheckBox("Read typed replies too")
        self.speak.setChecked(settings.speak_replies)
        self.ack = QCheckBox("Quick “got it” while working")
        self.ack.setChecked(settings.instant_ack)
        self.engine = Chips([("Edge · free", "edge"), ("OpenAI", "openai"), ("Windows", "windows")], settings.tts_engine)
        self.edge_en = self._combo([(label, v) for v, label in EDGE_VOICES_EN], settings.edge_voice_en)
        self.edge_ar = self._combo([(label, v) for v, label in EDGE_VOICES_AR], settings.edge_voice_ar)
        self.preview_en = self._play(lambda: self._preview())
        self.preview_ar = self._play(lambda: self._preview(arabic=True))
        self.openai_voice = self._combo([(label, v) for v, label in OPENAI_VOICES], settings.tts_openai_voice)
        self.preview = self._play(lambda: self._preview())
        self.preview.setToolTip("Hear it (OpenAI voices cost about a tenth of a cent)")
        en = [(n, n.split()[-1].lower()) for n, lang in voices if lang.lower().startswith("en")]
        ar = [(n, n.split()[-1].lower()) for n, lang in voices if lang.lower().startswith("ar")]
        self.voice_en = self._combo([("Windows default", "")] + en, settings.tts_voice_en)
        self.voice_ar = self._combo([("First installed", "")] + ar, settings.tts_voice_ar)
        self.rate = QSlider(Qt.Orientation.Horizontal)
        self.rate.setRange(6, 20)
        self.rate.setValue(int(round(settings.tts_rate * 10)))
        self.rate_label = QLabel()
        self.rate_label.setObjectName("prompt")
        self.rate.valueChanged.connect(lambda v: self.rate_label.setText(f"{v / 10:.1f}×"))
        self.rate_label.setText(f"{settings.tts_rate:.1f}×")
        self.pill = QCheckBox("Show the floating pill")
        self.pill.setChecked(settings.show_pill)
        self.autostart = QCheckBox("Start the WhatsApp server with Ken")
        self.autostart.setChecked(settings.autostart_whatsapp)
        self.morning = QCheckBox("Morning briefing")
        self.morning.setChecked(settings.morning_brief)
        self.morning_time = QTimeEdit(QTime.fromString(settings.morning_brief_time, "HH:mm"))
        self.morning_time.setDisplayFormat("HH:mm")
        self.morning_time.setEnabled(settings.morning_brief)
        self.morning.toggled.connect(self.morning_time.setEnabled)
        from .wake_dialog import MODEL_PATH as WAKE_MODEL

        self.wake = QCheckBox("Listen for “Ken”")
        self.wake.setChecked(settings.wake_word and WAKE_MODEL.exists())
        self.wake.setEnabled(WAKE_MODEL.exists())

        # ---------------------------------------------------------------------
        # Follow-up window, nudges and memory controls
        # ---------------------------------------------------------------------
        self.follow = Chips([("Off", 0), ("5 s", 5), ("8 s", 8), ("12 s", 12)], settings.follow_up_seconds)
        self.nudges = QCheckBox("Let Ken speak up on his own")
        self.nudges.setChecked(settings.nudges)
        self.nudge_max = Chips([("2 a day", 2), ("4 a day", 4), ("6 a day", 6)], settings.nudge_max_per_day)
        quiet_from, _, quiet_to = settings.nudge_quiet.partition("-")
        self.quiet_from = QTimeEdit(QTime.fromString(quiet_from.strip() or "23:00", "HH:mm"))
        self.quiet_to = QTimeEdit(QTime.fromString(quiet_to.strip() or "09:00", "HH:mm"))
        self.birthday_time = QTimeEdit(QTime.fromString(settings.birthday_time, "HH:mm"))
        for w in (self.quiet_from, self.quiet_to, self.birthday_time):
            w.setDisplayFormat("HH:mm")
            w.setFixedWidth(110)
        self.privacy = QLabel()
        self.privacy.setObjectName("faint")
        self.privacy.setWordWrap(True)
        self.learn_privacy = QLabel()
        self.learn_privacy.setObjectName("faint")
        self.learn_privacy.setWordWrap(True)

        # --------------------------------------------------------------------
        # Layout: top bar, sidebar and pages, buttons
        # --------------------------------------------------------------------
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(10)
        top = QHBoxLayout()
        title = QLabel("Settings")
        title.setObjectName("title")
        top.addWidget(title)
        top.addStretch(1)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("search settings…")
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(self._find)
        top.addWidget(self.search)
        outer.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(14)
        nav = QVBoxLayout()
        nav.setSpacing(2)
        self.nav = QButtonGroup(self)
        self.pages = QStackedWidget()
        builders = [self._page_general, self._page_voice, lambda: self._page_wake(WAKE_MODEL),
                    self._page_accounts, self._page_memory, self._page_shortcuts, self._page_nudges,
                    lambda: self._page_app(hotkey_label)]
        for i, ((name, icon, _), build) in enumerate(zip(self.SECTIONS, builders)):
            b = QPushButton(f"{theme.ICONS.get(icon, '')}   {name}")
            b.setObjectName("navButton")
            b.setStyleSheet(f"font-family: '{theme.ICON_FAMILY}', '{theme.FONT_FAMILY}';")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.nav.addButton(b, i)
            nav.addWidget(b)
            self.pages.addWidget(self._scrolling(name, build()))
        nav.addStretch(1)
        self.nav.idClicked.connect(self.pages.setCurrentIndex)
        self.nav.button(0).setChecked(True)
        side = QWidget()
        side.setFixedWidth(140)
        side.setLayout(nav)
        body.addWidget(side)
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)

        row = QHBoxLayout()
        row.addWidget(self.privacy, 1)
        cancel = QPushButton("cancel  esc")
        cancel.setObjectName("ghost")
        save = QPushButton("save")
        save.setObjectName("primary")
        save.setMinimumWidth(84)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(save)
        outer.addLayout(row)

        for w in (self.backend, self.stt, self.engine):
            w.changed.connect(self._update)
        self.speak.toggled.connect(self._update)
        self.speak_voice.toggled.connect(self._update)
        self._update()

    # ------------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------------

    def _grid(self, tiles: list, cols: int = 2) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(7)
        r = c = 0
        for t in tiles:
            span = 2 if isinstance(t, tuple) else 1
            t = t[0] if isinstance(t, tuple) else t
            if c + span > cols:
                r, c = r + 1, 0
            g.addWidget(t, r, c, 1, span)
            c += span
            if c >= cols:
                r, c = r + 1, 0
        return w

    def _scrolling(self, name: str, content: QWidget) -> QScrollArea:
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(8)
        where = QLabel(f"settings › {name.lower()}")
        where.setObjectName("sectionLabel")
        lay.addWidget(where)
        lay.addWidget(content)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        return scroll

    def _page_general(self) -> QWidget:
        return self._grid([(tile("Assistant model", self.backend, hint="Local is private but slow (~30 s) and weaker."),),
                           (tile("Speech to text", self.stt),),
                           (tile("Writing tone", self.tone, hint="How drafts and Arabic replies sound."),),
                           (tile("Personality", self.persona, hint="Only how Ken talks to you, never your drafts."),)])

    def _page_voice(self) -> QWidget:
        return self._grid([
            (tile("Voice engine", self.engine, hint="Edge: natural and free (text goes to Microsoft). "
                                                    "OpenAI ends 6 Jan 2027. Windows: offline."),),
            tile("English · Edge", row_of(self.edge_en, self.preview_en)),
            tile("Arabic · Edge", row_of(self.edge_ar, self.preview_ar)),
            (tile("OpenAI voice", row_of(self.openai_voice, self.preview)),),
            toggle_tile(self.speak_voice), toggle_tile(self.speak),
            toggle_tile(self.ack), tile("Speed", row_of(self.rate, self.rate_label)),
            tile("Windows English", self.voice_en), tile("Windows Arabic", self.voice_ar)])

    def _page_wake(self, model_path) -> QWidget:
        teach = QPushButton("Teach Ken my voice again…" if model_path.exists() else "Teach Ken my voice…")
        teach.setCursor(Qt.CursorShape.PointingHandCursor)
        teach.clicked.connect(lambda: (self.reject(), self.on_wake_train and self.on_wake_train()))
        return self._grid([toggle_tile(self.wake),
                           tile("", teach),
                           (tile("Keep listening after answers", self.follow,
                                 hint="After Ken answers something you said, he listens this long for a follow-up "
                                      "without “Ken”. “Thanks” ends it. Needs “Listen for Ken” on."),),
                           (tile("How it works", hint="About 3 minutes of recording, learned on this computer. The "
                                                      "microphone is checked locally; audio leaves only after “Ken”, "
                                                      "like holding the hotkey."),)])

    def _page_accounts(self) -> QWidget:
        from ..calendar_mcp import TOKEN

        connected = TOKEN.exists()
        cal_btn = QPushButton("Reconnect" if connected else "Connect Google Calendar")
        cal_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        state = QLabel("● connected" if connected else "● not connected")
        state.setObjectName("prompt")
        state.setStyleSheet(f"color: {theme.ACCENT if connected else theme.FAINT};")
        cal_btn.clicked.connect(lambda: (self.on_calendar and self.on_calendar(), cal_btn.setEnabled(False),
                                         cal_btn.setText("waiting for your browser…")))
        return self._grid([(tile("Google Calendar", row_of(state, cal_btn),
                                 hint="Reads events and free time. Adds an event only when you click Add on a card; "
                                      "never edits or deletes."),),
                           (toggle_tile(self.autostart),)])

    def _page_memory(self) -> QWidget:
        learn = QPushButton("Learn from my WhatsApp history")
        learn.setCursor(Qt.CursorShape.PointingHandCursor)

        def start_learning() -> None:
            learn.setEnabled(False)
            learn.setText("learning… (see the panel)")
            if self.on_learn:
                self.on_learn()

        learn.clicked.connect(start_learning)
        return self._grid([(tile("Style memory", learn, self.learn_privacy,
                                 hint="Reads how you answered people in your 15 most-messaged chats (up to 150 "
                                      "replies each) so drafts sound like you. Safe to run again."),),
                           (tile("Things Ken remembers", self._facts_list(),
                                 hint="Say “Ken, remember…” to add, or “forget…” to remove. × deletes at once."),),
                           (tile("Birthday reminders at", self.birthday_time,
                                 hint="Yearly dates you ask Ken to remember remind you at this time (and appear in "
                                      "the briefing). Applies to new ones."),)])

    # -------------------------------------------------------------------------
    # The saved-facts list: one row per fact with a × that deletes it right away
    # -------------------------------------------------------------------------

    def _facts_list(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        if not self.facts:
            empty = QLabel("Nothing yet. Try “Ken, remember Youssef's birthday is 3 May”.")
            empty.setObjectName("faint")
            empty.setWordWrap(True)
            lay.addWidget(empty)
        for fact in self.facts:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            when = f"  ·  every {fact.date}" if fact.date else ""
            text = QLabel(f"{fact.text}{when}")
            text.setWordWrap(True)
            remove = QPushButton("×")
            remove.setObjectName("ghost")
            remove.setFixedSize(32, 28)
            remove.setStyleSheet("padding: 0; font-size: 12pt;")
            remove.setToolTip("Forget this")
            remove.setCursor(Qt.CursorShape.PointingHandCursor)
            remove.clicked.connect(lambda _=False, f=fact, r=row: (self.on_delete_fact and self.on_delete_fact(f),
                                                                    r.hide(), r.deleteLater()))
            rl.addWidget(text, 1)
            rl.addWidget(remove)
            lay.addWidget(row)
        return box

    # -------------------------------------------------------------------------
    # Shortcuts page
    #   Your shortcuts (× removes), an Add row (word(s) + a chat name or an email address), and
    #   family-looking contacts that don't have one yet. Every button acts at once.
    # -------------------------------------------------------------------------

    def _page_shortcuts(self) -> QWidget:
        self._shortcut_rows = QVBoxLayout()
        self._shortcut_rows.setContentsMargins(0, 0, 0, 0)
        self._shortcut_rows.setSpacing(3)
        holder = QWidget()
        holder.setLayout(self._shortcut_rows)
        self._shortcut_empty = QLabel("None yet. Try “Ken, mom is ماما”, or add one below.")
        self._shortcut_empty.setObjectName("faint")
        self._shortcut_rows.addWidget(self._shortcut_empty)
        for s in self.shortcuts:
            self._add_shortcut_row(s)

        words = QLineEdit()
        words.setPlaceholderText("word(s): mom, ماما")
        who = QLineEdit()
        who.setPlaceholderText("chat name or email")
        for box in (words, who):
            box.setObjectName("search")  # the bordered input style
        add = QPushButton("add")
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        result = QLabel()
        result.setObjectName("faint")
        result.setWordWrap(True)

        def do_add() -> None:
            if not self.on_add_shortcut:
                return
            saved, why = self.on_add_shortcut(words.text(), who.text())
            if saved is None:
                result.setText(why)
                return
            result.setText(f"Saved: {', '.join(saved.words)} → {saved.label}")
            words.clear()
            who.clear()
            self._add_shortcut_row(saved)

        add.clicked.connect(do_add)
        tiles = [(tile("Your shortcuts", holder,
                       hint="“Send a message to mom” goes to exactly this chat. One shortcut is one person, group "
                            "or email address. Sending still asks you to confirm."),),
                 (tile("Add a shortcut", row_of(words, who, add), result),)]
        if self.shortcut_suggestions:
            tiles.append((tile("Suggested from your contacts", self._suggestion_list(),
                               hint="Contacts saved under a family word."),))
        return self._grid(tiles)

    def _add_shortcut_row(self, s) -> None:
        """One row per shortcut; adding words to an existing target replaces its row."""
        old = getattr(self, "_shortcut_widgets", {}).pop(s.target, None)
        if old is not None:
            old.hide()
            old.deleteLater()
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        from ..shortcuts import iso  # keeps "+20…" in order next to English
        from .panel import word_chips  # one chip per word: Arabic and English words stay in order

        where = iso(f"+{s.number}") if s.number else ("group" if s.target.endswith("@g.us") else
                                                       "" if s.target == s.label else s.target)
        text = word_chips(s.words, s.label, max_n=4)
        place = QLabel(where)
        place.setObjectName("faint")
        text.layout().insertWidget(text.layout().count() - 1, place)
        remove = QPushButton("×")
        remove.setObjectName("ghost")
        remove.setFixedSize(32, 28)
        remove.setStyleSheet("padding: 0; font-size: 12pt;")
        remove.setToolTip("Remove this shortcut")
        remove.clicked.connect(lambda _=False, sid=s.id, t=s.target, r=row: (
            self.on_remove_shortcut and self.on_remove_shortcut(sid), self._shortcut_widgets.pop(t, None),
            r.hide(), r.deleteLater()))
        rl.addWidget(text, 1)
        rl.addWidget(remove)
        self._shortcut_widgets = getattr(self, "_shortcut_widgets", {})
        self._shortcut_widgets[s.target] = row
        self._shortcut_rows.addWidget(row)
        self._shortcut_empty.hide()

    def _suggestion_list(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        for p in self.shortcut_suggestions:
            s = p.shortcut
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            from ..shortcuts import iso

            text = QLabel(f"{escape(iso(s.words[0]))}  →  {escape(iso(s.label))}")
            text.setTextFormat(Qt.TextFormat.RichText)
            add = QPushButton("add")
            add.setObjectName("ghost")
            add.setCursor(Qt.CursorShape.PointingHandCursor)
            add.clicked.connect(lambda _=False, sc=s, r=row: (
                self.on_save_shortcut and self._add_shortcut_row(self.on_save_shortcut(sc)), r.hide()))
            rl.addWidget(text, 1)
            rl.addWidget(add)
            lay.addWidget(row)
        return box

    def _page_nudges(self) -> QWidget:
        return self._grid([(toggle_tile(self.nudges),),
                           (tile("At most", self.nudge_max, hint="Message nudges a day. Calendar “starts in 20 "
                                                                 "minutes” alerts don't count."),),
                           (tile("Quiet hours", row_of(self.quiet_from, QLabel("to"), self.quiet_to, QWidget()),
                                 hint="No nudges in this window. They also wait during a Pomodoro focus and meetings."),),
                           (tile("How it works", hint="Every 10 minutes Ken checks (on this computer) for messages "
                                                      "you haven't answered: 3 h for people you message often, 24 h "
                                                      "for others. A tiny AI check makes sure it really needs a reply "
                                                      "(a few requests a day). ★ people on a person card are spoken "
                                                      "aloud; the rest are a card. Nothing is ever sent."),)])

    def _page_app(self, hotkey_label: str) -> QWidget:
        keys = QWidget()
        kl = QHBoxLayout(keys)
        kl.setContentsMargins(0, 0, 0, 0)
        for part in hotkey_label.split("+"):
            kl.addWidget(kbd(part))
        kl.addStretch(1)
        return self._grid([toggle_tile(self.pill),
                           tile("Morning briefing", row_of(self.morning, self.morning_time),
                                hint="The ☀ briefing once a morning, first line read aloud."),
                           (tile("Hotkey", keys, hint="Tap to open, hold to talk. Esc closes the panel."),)])

    # ------------------------------------------------------------------------
    # Behaviour: search, privacy line, previews, collecting changes
    # ------------------------------------------------------------------------

    def _find(self, text: str) -> None:
        words = text.strip().lower()
        if not words:
            return
        for i, (name, _, keys) in enumerate(self.SECTIONS):
            if words in name.lower() or any(k.startswith(words) for k in keys.split()):
                self.nav.button(i).setChecked(True)
                self.pages.setCurrentIndex(i)
                return

    def _update(self) -> None:
        sent = []
        if self.backend.currentData() == "openai":
            sent.append("message text")
        stt = self.stt.currentData()
        if stt == "openai" or (stt == "auto" and self.backend.currentData() == "openai"):
            sent.append("your voice")
        speaking = self.speak.isChecked() or self.speak_voice.isChecked()
        if self.engine.currentData() == "openai" and speaking:
            sent.append("replies to be spoken")
        text = ("sent to OpenAI: " + ", ".join(sent)) if sent else ""
        if self.engine.currentData() == "edge" and speaking:
            text += (" · " if text else "") + "spoken replies: Microsoft"
        self.privacy.setText(text or "everything stays on this computer")
        self.learn_privacy.setText(
            "Those messages are sent to OpenAI to be embedded (well under a cent)."
            if self.settings.backend == "openai" else "Embedded locally; nothing leaves this computer.")
        engine = self.engine.currentData()
        for w in (self.openai_voice, self.preview):
            w.setEnabled(engine == "openai")
        for w in (self.edge_en, self.edge_ar, self.preview_en, self.preview_ar):
            w.setEnabled(engine == "edge")

    @staticmethod
    def _play(slot) -> QPushButton:
        b = QPushButton(theme.ICONS["play"])
        b.setObjectName("icon")
        b.setFixedSize(30, 30)
        b.setToolTip("Hear it")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    def _preview(self, arabic: bool = False) -> None:
        if not self.on_preview:
            return
        persona = self.persona.currentData()
        prefs = {"engine": self.engine.currentData(), "openai_voice": self.openai_voice.currentData(),
                 "openai_model": self.settings.tts_openai_model, "persona": persona, "tone": self.tone.currentData(),
                 "en": self.voice_en.currentData(), "rate": self.rate.value() / 10,
                 "edge_en": self.edge_en.currentData(), "edge_ar": self.edge_ar.currentData()}
        self.on_preview(prefs, (PREVIEW_TEXT_AR if arabic else PREVIEW_TEXT)[persona])

    def showEvent(self, e) -> None:
        dark_title_bar(self, dark=False)
        screen = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        self.resize(min(self.width(), screen.width() - 40), min(self.height(), screen.height() - 60))
        super().showEvent(e)

    @staticmethod
    def _combo(items: list[tuple[str, str]], current: str) -> QComboBox:
        box = QComboBox()
        # long voice names must not make the tile (and the page) wider than the window
        box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        box.setMinimumContentsLength(10)
        for label, value in items:
            box.addItem(label, value)
        idx = box.findData(current)
        box.setCurrentIndex(idx if idx >= 0 else 0)
        return box

    def changes(self) -> dict:
        new = {
            "backend": self.backend.currentData(),
            "stt_backend": self.stt.currentData(),
            "tone": self.tone.currentData(),
            "persona": self.persona.currentData(),
            "speak_replies": self.speak.isChecked(),
            "speak_after_voice": self.speak_voice.isChecked(),
            "instant_ack": self.ack.isChecked(),
            "tts_engine": self.engine.currentData(),
            "edge_voice_en": self.edge_en.currentData(),
            "edge_voice_ar": self.edge_ar.currentData(),
            "tts_openai_voice": self.openai_voice.currentData(),
            "tts_voice_en": self.voice_en.currentData(),
            "tts_voice_ar": self.voice_ar.currentData(),
            "tts_rate": self.rate.value() / 10,
            "show_pill": self.pill.isChecked(),
            "autostart_whatsapp": self.autostart.isChecked(),
            "wake_word": self.wake.isChecked(),
            "morning_brief": self.morning.isChecked(),
            "morning_brief_time": self.morning_time.time().toString("HH:mm"),
            "follow_up_seconds": self.follow.currentData(),
            "nudges": self.nudges.isChecked(),
            "nudge_max_per_day": self.nudge_max.currentData(),
            "nudge_quiet": f"{self.quiet_from.time().toString('HH:mm')}-{self.quiet_to.time().toString('HH:mm')}",
            "birthday_time": self.birthday_time.time().toString("HH:mm"),
        }
        return {k: v for k, v in new.items() if getattr(self.settings, k) != v}
