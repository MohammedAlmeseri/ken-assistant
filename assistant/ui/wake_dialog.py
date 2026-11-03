"""Teach Ken your voice: say "Ken" 25 times, read a short text, stay quiet a moment, train.

Recordings and the model stay in data/wake/ (git-ignored). Training runs on a worker thread
(about a minute and a half on this laptop) so the window stays responsive.
"""

from __future__ import annotations

import math
import threading
import time

import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..config import DATA_DIR
from ..voice import wake
from . import theme
from .winutil import dark_title_bar

# ----------------------------------------------------------------------------
# Recording plan
#   25 takes of 'Ken', a short read-aloud text (speech without the word) and room noise.
# ----------------------------------------------------------------------------

WAKE_DIR = DATA_DIR / "wake"
MODEL_PATH = WAKE_DIR / "ken.npz"
SAMPLES_PATH = WAKE_DIR / "samples.npz"
TAKES = 25
TAKE_S = 1.8
TALK_MIN_S, TALK_MAX_S = 30, 75
QUIET_S = 8
HINTS = ["normally", "a bit louder", "quietly", "quickly", "like you're calling from across the room"]
TALK_EN = ("Hi, can you check my messages and then tell me what's new? I think ten people came again. "
           "Kevin can call me tomorrow, then we can plan the class. Okay, thank you, can I send it now?")
TALK_AR = ("لكن أنا كنت عايز أقولك حاجة. كان عندي محاضرة النهارده، وكمان كنت مشغول شوية. "
           "كده تمام، ابعتلي الرسالة بكرة لو تقدر.")


# ----------------------------------------------------------------------------
# The orb
# ----------------------------------------------------------------------------

class Orb(QWidget):
    """Big circle: grey idle, red pulse while recording, green flash for a good take."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(150, 150)
        self.state = "idle"  # idle | rec | ok | miss | busy
        self.label = "Ken"  # what to say while recording
        self.level = 0.0
        self.progress = 0.0
        self._since = time.monotonic()
        QTimer(self, interval=33, timeout=self.update).start()

    def set_state(self, state: str) -> None:
        self.state, self._since = state, time.monotonic()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self.rect().center()
        t = time.monotonic() - self._since
        color = {"rec": theme.DANGER, "ok": theme.SUCCESS, "miss": theme.WARN, "busy": theme.ACCENT}.get(self.state, theme.FAINT)
        r = 46 + (10 * self.level if self.state == "rec" else 0) + (3 * math.sin(time.monotonic() * 5) if self.state == "busy" else 0)
        glow = QColor(color)
        glow.setAlphaF(0.18 if self.state != "idle" else 0.08)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, r + 14, r + 14)
        fill = QColor(color)
        fill.setAlphaF(0.9 if self.state in ("rec", "ok") and t < 2 else 0.55)
        p.setBrush(fill)
        p.drawEllipse(c, r, r)
        if self.progress > 0:
            box = QRectF(c.x() - 66, c.y() - 66, 132, 132)
            p.setPen(QPen(QColor(theme.ACCENT_2), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(box, 90 * 16, int(-self.progress * 360 * 16))
        p.setPen(QColor("white"))
        p.setFont(theme.font(13, QFont.Weight.DemiBold))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                   {"rec": self.label, "ok": "✓", "miss": "?", "busy": "…"}.get(self.state, "🎙"))
        p.end()


# ----------------------------------------------------------------------------
# The training window
#   Records, then trains in a worker thread (voice/wake.train) and saves data/wake/ken.npz.
# ----------------------------------------------------------------------------

class WakeTrainDialog(QDialog):
    trained = Signal(str)  # report text, on the UI thread
    failed = Signal(str)
    _progress = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Teach Ken your voice")
        self.setWindowIcon(theme.app_icon())
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 20)
        lay.setSpacing(12)
        self.title = QLabel()
        self.title.setObjectName("title")
        self.title.setWordWrap(True)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setObjectName("muted")
        self.orb = Orb()
        self.count = QLabel("")
        self.count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count.setObjectName("muted")
        self.text = QLabel(f"<p>{TALK_EN}</p><p dir='rtl' align='right'>{TALK_AR}</p>")
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.text.setWordWrap(True)
        self.text.setStyleSheet(f"background: {theme.SURFACE}; border-radius: 10px; padding: 12px; font-size: 11pt;")
        self.text.hide()
        lay.addWidget(self.title)
        lay.addWidget(self.body)
        lay.addWidget(self.orb, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.count)
        lay.addWidget(self.text)
        row = QHBoxLayout()
        self.cancel = QPushButton("Cancel")
        self.cancel.setObjectName("ghost")
        self.cancel.clicked.connect(self.reject)
        self.go = QPushButton("Start")
        self.go.setObjectName("primary")
        self.go.setMinimumWidth(110)
        self.go.clicked.connect(self._next)
        row.addWidget(self.cancel)
        row.addStretch(1)
        row.addWidget(self.go)
        lay.addLayout(row)

        self.step = "intro"
        self.takes: list[np.ndarray] = []
        self.talk = np.zeros(0, np.float32)
        self.quiet = np.zeros(0, np.float32)
        self._chunks: list[np.ndarray] = []
        self._stream = None
        self._rec_until = 0.0
        self._rec_started = 0.0
        self._tick = QTimer(self, interval=50, timeout=self._on_tick)
        self.trained.connect(self._on_trained)
        self.failed.connect(self._on_failed)
        self._progress.connect(lambda s: self.count.setText(s))
        self._show_intro()

    def showEvent(self, e) -> None:
        dark_title_bar(self, dark=False)
        super().showEvent(e)

    # ------------------------------------------------------------------------
    # Microphone
    # ------------------------------------------------------------------------

    def _rec_start(self, seconds: float) -> None:
        import sounddevice as sd

        self._chunks = []

        def cb(indata, frames, t, status):
            self._chunks.append(indata[:, 0].copy())
            self.orb.level = min(1.0, float(np.sqrt(np.mean(indata ** 2))) * 10)

        if self._stream is None:
            self._stream = sd.InputStream(samplerate=wake.RATE, channels=1, dtype="float32", callback=cb)
            self._stream.start()
        self._rec_started = time.monotonic()
        self._rec_until = self._rec_started + seconds
        self._tick.start()

    def _rec_stop(self) -> np.ndarray:
        self._tick.stop()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self.orb.level = 0.0
        return np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)

    def reject(self) -> None:
        self._rec_stop()
        super().reject()

    # ------------------------------------------------------------------------
    # Steps: intro, takes, talk, quiet, training
    # ------------------------------------------------------------------------

    def _show_intro(self) -> None:
        self.title.setText("Teach Ken your voice")
        self.body.setText(
            f"Three short steps, about 3 minutes: say “Ken” {TAKES} times, read a short text aloud, then "
            "stay quiet for a few seconds. Everything is recorded and learned on this computer only "
            "(saved in data/wake/). Use the microphone you normally talk to Ken with.")

    def _next(self) -> None:
        if self.step == "intro":
            self.step = "takes"
            self.go.setEnabled(False)
            self.title.setText("Step 1 of 3 · Say “Ken”")
            self._take()
        elif self.step == "talk":
            self.talk = self._rec_stop()
            self._start_quiet()
        elif self.step == "done":
            self.accept()

    def _take(self) -> None:
        n = len(self.takes)
        self.body.setText(f"When the circle turns red, say “Ken” {HINTS[(n // 5) % len(HINTS)]}. One word, then wait.")
        self.count.setText(f"{n} / {TAKES}")
        self.orb.progress = n / TAKES
        self.orb.set_state("idle")
        QTimer.singleShot(700, lambda: (self.orb.set_state("rec"), self._rec_start(TAKE_S)))

    def _on_tick(self) -> None:
        now = time.monotonic()
        if self.step == "takes" and now >= self._rec_until:
            audio = self._rec_stop()
            if wake.word_span(wake.to_int16(audio)) is None:
                self.orb.set_state("miss")
                self.count.setText(f"{len(self.takes)} / {TAKES} · didn't catch it, once more")
            else:
                self.takes.append(audio)
                self.orb.set_state("ok")
            if len(self.takes) >= TAKES:
                QTimer.singleShot(500, self._start_talk)
            else:
                QTimer.singleShot(450, self._take)
        elif self.step == "talk":
            spent = now - self._rec_started
            self.count.setText(f"{int(spent)} s · read the whole text, then click Done")
            self.go.setEnabled(spent >= TALK_MIN_S)
            if spent >= TALK_MAX_S:
                self._next()
        elif self.step == "quiet":
            left = self._rec_until - now
            self.count.setText(f"{max(0, math.ceil(left))} s")
            if left <= 0:
                self.quiet = self._rec_stop()
                self._start_training()

    def _start_talk(self) -> None:
        self.step = "talk"
        self.title.setText("Step 2 of 3 · Read this aloud")
        self.body.setText("At your normal pace, like you'd talk to a friend. Don't say “Ken”. "
                          "This teaches it what NOT to wake up for (“can”, “then”, “لكن”, “كنت”…).")
        self.text.show()
        self.orb.progress = 0
        self.orb.label = "Talk"
        self.orb.set_state("rec")
        self.go.setText("Done")
        self.go.setEnabled(False)
        self._rec_start(TALK_MAX_S + 1)

    def _start_quiet(self) -> None:
        self.step = "quiet"
        self.text.hide()
        self.title.setText("Step 3 of 3 · Stay quiet")
        self.body.setText("Just the normal sound of your room for a few seconds.")
        self.go.setEnabled(False)
        self.orb.label = "Shh"
        self.orb.set_state("rec")
        self._rec_start(QUIET_S)

    def _start_training(self) -> None:
        self.step = "training"
        self.title.setText("Learning your voice…")
        self.body.setText("About a minute and a half. You can leave this window open.")
        self.orb.set_state("busy")
        self.cancel.setEnabled(False)
        takes, talk, quiet = list(self.takes), self.talk, self.quiet

        def work():
            try:
                WAKE_DIR.mkdir(parents=True, exist_ok=True)
                np.savez(SAMPLES_PATH, talk=talk, quiet=quiet, **{f"take{i}": t for i, t in enumerate(takes)})
                self._progress.emit("Adding other voices saying look-alike words…")
                others = wake.other_voices_speech()
                model, report = wake.train(takes, talk, quiet, others, progress=self._progress.emit)
                model.save(MODEL_PATH)
                self.trained.emit(report.text())
            except Exception as exc:
                self.failed.emit(f"{type(exc).__name__}: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _on_trained(self, report: str) -> None:
        self.step = "done"
        self.orb.set_state("ok")
        self.title.setText("Ken knows your voice ✓")
        self.body.setText(report + "\n\nSay “Ken” and then your request, e.g. “Ken, what's new on WhatsApp?”. "
                          "Turn listening on or off from the pill or tray menu.")
        self.count.setText("")
        self.go.setText("Finish")
        self.go.setEnabled(True)
        self.cancel.hide()

    def _on_failed(self, error: str) -> None:
        self.step = "done"
        self.orb.set_state("miss")
        self.title.setText("Training didn't work")
        self.body.setText(error)
        self.count.setText("")
        self.go.setText("Close")
        self.go.setEnabled(True)
        self.cancel.setEnabled(True)
