"""Wake word ("Ken"), trained on the owner's own voice, fully offline.

openWakeWord's frozen speech-embedding models turn every 2 s of audio into 16x96 features;
a small logistic-regression head trained here on the owner's recordings says "that was Ken".
Training uses ~25 takes of the word, a minute of normal talking (with look-alike words such as
"can", "then", "لكن", "كنت") and a few seconds of room noise; everything stays in data/wake/.

WakeListener keeps one microphone stream open, scores every 80 ms chunk, and after the word
records the request until a short silence (Silero VAD), then hands the audio over.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

# ----------------------------------------------------------------------------
# Audio constants
#   16 kHz audio in 80 ms chunks: one openWakeWord feature frame each.
# ----------------------------------------------------------------------------

RATE = 16000
CHUNK = 1280  # 80 ms, one new feature frame
SPEECH_GATE = 0.6


# ----------------------------------------------------------------------------
# Features
#   openWakeWord's melspectrogram + embedding models: audio -> 96-number frames.
# ----------------------------------------------------------------------------

def _features():
    from openwakeword.utils import AudioFeatures, download_models

    try:
        return AudioFeatures(inference_framework="onnx")
    except Exception:  # first run: fetch the two small feature models (and the VAD) once
        download_models(model_names=["__features_only__"])
        return AudioFeatures(inference_framework="onnx")


def to_int16(audio: np.ndarray) -> np.ndarray:
    if audio.dtype == np.int16:
        return audio
    return (np.clip(audio, -1, 1) * 32767).astype(np.int16)


# ----------------------------------------------------------------------------
# Building training streams
#   Takes are replayed into room noise at random gains and STREAMED through the same features the
#   live listener uses. (Training on per-clip features didn't match the live stream: it never fired.)
# ----------------------------------------------------------------------------

def word_span(clip: np.ndarray) -> tuple[int, int] | None:
    """Start/end sample of the spoken word in a short take, by loudness. None if nothing was said."""
    x = clip.astype(np.float32) / (32768.0 if clip.dtype == np.int16 else 1.0)
    frame = 320  # 20 ms
    n = len(x) // frame
    if n < 5:
        return None
    rms = np.sqrt((x[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
    floor, peak = np.percentile(rms, 20), rms.max()
    if peak < max(0.01, floor * 4):
        return None  # silence
    loud = np.where(rms > floor + 0.25 * (peak - floor))[0]
    start, end = max(0, loud[0] - 2) * frame, min(n, loud[-1] + 3) * frame
    if not 0.12 * RATE <= end - start <= 1.3 * RATE:
        return None  # too short (a click) or too long (not one word)
    return start, end


def _slice(pool: np.ndarray, rng: np.random.Generator, length: int) -> np.ndarray:
    if len(pool) <= length:
        return np.pad(pool, (0, length - len(pool)))
    i = rng.integers(0, len(pool) - length)
    return pool[i: i + length].copy()


def stream_features(audio: np.ndarray, feats=None) -> tuple[np.ndarray, np.ndarray]:
    """Feed audio through the features exactly as the live listener does (80 ms chunks).
    Returns (end time of each window in seconds, windows (n, 16, 96)); the first 2 s are
    skipped because the feature buffer starts out with noise."""
    feats = feats or _features()
    feats.reset()
    audio = to_int16(audio)
    times, windows = [], []
    for i in range(0, len(audio) - CHUNK + 1, CHUNK):
        feats(audio[i: i + CHUNK])
        t = (i + CHUNK) / RATE
        if t >= 2.0:
            times.append(t)
            windows.append(feats.get_features(16)[0])
    if not windows:
        return np.zeros(0), np.zeros((0, 16, 96), np.float32)
    return np.array(times), np.array(windows, dtype=np.float32)


def word_stream(takes: list[np.ndarray], background: np.ndarray, per_take: int, seed: int = 0,
                ) -> tuple[np.ndarray, list[float]]:
    """One long recording: room noise, the word (random gain), room noise, ... for every take,
    `per_take` times. Returns the audio and the time each word ends."""
    words = [take[span[0]: span[1]] for take in takes if (span := word_span(take)) is not None]
    return segment_stream(words, background, per_take, seed)


def segment_stream(segments: list[np.ndarray], background: np.ndarray, repeats: int, seed: int = 0,
                   ) -> tuple[np.ndarray, list[float]]:
    """Each short sound alone in room noise, `repeats` times at random gains: the same shape as a
    wake word, so the model has to learn the sound itself, not "short sound after silence"."""
    rng = np.random.default_rng(seed)
    bg = background.astype(np.float32) if len(background) else np.zeros(RATE, np.float32)
    parts, ends, t = [_slice(bg, rng, 2 * RATE)], [], 2.0
    for segment in segments:
        word = segment.astype(np.float32)
        for _ in range(repeats):
            lead = _slice(bg, rng, int(rng.uniform(0.6, 1.4) * RATE)) * rng.uniform(0.3, 1.5)
            tail = _slice(bg, rng, int(1.2 * RATE)) * rng.uniform(0.3, 1.5)
            spoken = word * rng.uniform(0.5, 1.6)
            parts += [lead, spoken, tail]
            t += len(lead) / RATE + len(spoken) / RATE
            ends.append(t)
            t += len(tail) / RATE
    return np.clip(np.concatenate(parts), -32768, 32767).astype(np.int16), ends


# ----------------------------------------------------------------------------
# Labels and scoring
# ----------------------------------------------------------------------------

def label_windows(times: np.ndarray, ends: list[float]) -> tuple[np.ndarray, np.ndarray]:
    """Masks over windows: positive = the word ended 0.08-0.5 s ago; negative = no word in the
    last 1.6 s (room noise between words). Windows in between are ambiguous and left out."""
    ends_arr = np.array(ends)
    pos = np.zeros(len(times), bool)
    neg = np.ones(len(times), bool)
    for e in ends_arr:
        since = times - e
        pos |= (since >= 0.08) & (since <= 0.5)
        neg &= ~((since >= -1.0) & (since <= 1.6))
    return pos, neg


def held3(scores: np.ndarray) -> np.ndarray:
    """Score held for 3 frames in a row (the listener fires on 3 consecutive hits)."""
    if len(scores) < 3:
        return np.zeros(0)
    return np.lib.stride_tricks.sliding_window_view(scores, 3).min(axis=1)


def word_peaks(times: np.ndarray, scores: np.ndarray, ends: list[float]) -> np.ndarray:
    """Best 3-frame score in the 0.8 s after each word ends."""
    return np.array([held3(scores[(times >= e) & (times <= e + 0.8)]).max(initial=0.0) for e in ends])


# ----------------------------------------------------------------------------
# Hard negatives
#   Look-alike words ('can', 'then', 'لكن'), lone syllables, sniffs, breaths and clicks. Adding
#   these fixed the false wakes on sniffs and on 'hey'.
# ----------------------------------------------------------------------------

LOOKALIKES = {
    "en": "Can you send it then? Ten kind people came again. Kevin can call when the cane is ready. "
          "I can, and then we can go. Okay, thank you, can I come in? Again and again, then ten.",
    "ar": "لكن أنا كنت هناك كمان. كان عندي كلام كتير، لكن كنت مشغول. كده كويس، كمان شوية. "
          "كنت عايز أقولك لكن نسيت. هو كان كده؟ لا، كمان مرة.",
}
# Said one at a time by Windows voices: lone short words that must NOT wake Ken.
LONE_WORDS = {
    "en": ["Hey", "Hi", "Yes", "Okay", "Can", "Ten", "Then", "Kid", "Key", "Cake", "Hmm", "Kevin", "Again", "When",
           "Yeah", "Come", "Kim", "Pen", "Men"],
    "ar": ["لكن", "كنت", "ايه", "أيوه", "كمان", "هاي", "كده", "تمام", "كان", "يلا"],
}


def _bandpass(x: np.ndarray, low: float, high: float) -> np.ndarray:
    from scipy.signal import butter, lfilter

    b, a = butter(4, [low / (RATE / 2), min(high, RATE / 2 - 100) / (RATE / 2)], "band")
    return lfilter(b, a, x)


def short_sounds(talk: np.ndarray, level: float, seed: int = 0, n_synthetic: int = 60) -> list[np.ndarray]:
    """Lone short sounds that aren't the wake word, at the user's speaking level (int16 scale):
    syllables and snippets cut from their own talk, plus sniffs, breaths, clicks, knocks and hums."""
    rng = np.random.default_rng(seed)
    x = talk.astype(np.float32)
    out = []
    frame = 320
    n = len(x) // frame
    if n > 10:
        # syllable-sized bursts between pauses in the user's own talk
        rms = np.sqrt((x[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
        floor, peak = np.percentile(rms, 20), np.percentile(rms, 99)
        loud = rms > floor + 0.25 * (peak - floor)
        start = None
        for i, on in enumerate(np.append(loud, False)):
            if on and start is None:
                start = i
            elif not on and start is not None:
                if 4 <= i - start <= 40:  # 80-800 ms
                    out.append(x[start * frame: i * frame])
                start = None
        # and random snippets (cut mid-word too)
        for _ in range(60):
            length = int(rng.uniform(0.15, 0.6) * RATE)
            if len(x) > length:
                i = rng.integers(0, len(x) - length)
                out.append(x[i: i + length] * np.hanning(length))
    for k in range(n_synthetic):  # non-speech
        kind = k % 5
        low, high = [(0.15, 0.4), (0.3, 0.7), (0.01, 0.04), (0.05, 0.15), (0.2, 0.5)][kind]
        length = int(rng.uniform(low, high) * RATE)
        t = np.arange(length) / RATE
        env = np.sin(np.linspace(0, np.pi, length)) ** 2
        noise = rng.normal(0, 1, length)
        if kind == 0:  # sniff: hissy, rises and falls
            sound = _bandpass(noise, 1800, 7000) * env
        elif kind == 1:  # breath: softer, lower
            sound = _bandpass(noise, 300, 2500) * env
        elif kind == 2:  # click
            sound = noise * np.exp(-t * 300)
        elif kind == 3:  # knock / tap
            sound = np.sin(2 * np.pi * rng.uniform(80, 250) * t) * np.exp(-t * 40)
        else:  # "hmm"
            sound = np.sin(2 * np.pi * rng.uniform(100, 220) * t) * env
        out.append(sound)
    leveled = []
    for sound in out:
        r = float(np.sqrt(np.mean(sound ** 2))) or 1.0
        leveled.append((sound / r * level * rng.uniform(0.4, 1.6)).astype(np.float32))
    return leveled


def _windows_voices(texts: dict[str, list[str]]) -> list[tuple[str, np.ndarray]]:
    """(language, audio at 16 kHz, -1..1) for each installed English/Arabic Windows voice and text."""
    from math import gcd

    from scipy.signal import resample_poly

    try:
        from .tts import OneCoreEngine

        engine = OneCoreEngine()
        voices = engine.voice_list()
    except Exception:
        return []
    out = []
    for name, lang in voices:
        key = "ar" if lang.lower().startswith("ar") else "en" if lang.lower().startswith("en") else ""
        if not key:
            continue
        engine.prefs = {key: name.split()[-1].lower(), "rate": 1.0}
        for text in texts.get(key, []):
            try:
                samples, rate, _ = engine.synthesize(text, key)
            except Exception:
                continue
            x = np.asarray(samples, dtype=np.float32)
            x = x.mean(axis=1) if x.ndim > 1 else x
            g = gcd(int(rate), RATE)
            out.append((key, resample_poly(x, RATE // g, int(rate) // g).astype(np.float32)))
    return out


def lone_words(level: float, seed: int = 0) -> list[np.ndarray]:
    """Windows voices saying single look-alike words, trimmed and set to the user's level (int16 scale)."""
    rng = np.random.default_rng(seed)
    out = []
    for _, audio in _windows_voices(LONE_WORDS):
        peak = float(np.abs(audio).max()) or 1.0
        span = word_span(to_int16(audio / peak * 0.5))
        word = audio[span[0]: span[1]] if span else audio
        r = float(np.sqrt(np.mean(word ** 2))) or 1.0
        out.append((word / r * level * rng.uniform(0.6, 1.4)).astype(np.float32))
    return out


def other_voices_speech() -> list[np.ndarray]:
    """Windows' own voices saying words that sound like 'Ken' ("can", "then", "لكن", "كنت"), as
    extra 'not the wake word' examples from other speakers. Empty if no voices are installed."""
    from math import gcd

    from scipy.signal import resample_poly

    try:
        from .tts import OneCoreEngine

        engine = OneCoreEngine()
        voices = engine.voice_list()
    except Exception:
        return []
    out = []
    for name, lang in voices:
        key = "ar" if lang.lower().startswith("ar") else "en" if lang.lower().startswith("en") else ""
        if not key:
            continue
        engine.prefs = {key: name.split()[-1].lower(), "rate": 1.0}
        try:
            samples, rate, _ = engine.synthesize(LOOKALIKES[key], key)
        except Exception:
            continue
        x = np.asarray(samples, dtype=np.float32)
        x = x.mean(axis=1) if x.ndim > 1 else x
        g = gcd(int(rate), RATE)
        out.append(resample_poly(x, RATE // g, int(rate) // g).astype(np.float32))
    return out


# ----------------------------------------------------------------------------
# The model
#   Logistic regression on standardized features: tiny, fast, trained in seconds.
# ----------------------------------------------------------------------------

@dataclass
class WakeModel:
    mean: np.ndarray
    scale: np.ndarray
    coef: np.ndarray
    intercept: float
    threshold: float

    def score(self, feats: np.ndarray) -> float:
        """feats: (16, 96) or (n, 16, 96). Probability that the window ends with the wake word."""
        x = feats.reshape(-1, 16 * 96)
        z = ((x - self.mean) / self.scale) @ self.coef + self.intercept
        p = 1 / (1 + np.exp(-z))
        return float(p[0]) if p.shape[0] == 1 else p

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, mean=self.mean, scale=self.scale, coef=self.coef,
                 intercept=self.intercept, threshold=self.threshold)

    @classmethod
    def load(cls, path: Path) -> "WakeModel":
        d = np.load(path)
        return cls(d["mean"], d["scale"], d["coef"], float(d["intercept"]), float(d["threshold"]))


def fit(pos: np.ndarray, neg: np.ndarray, c: float = 0.05) -> WakeModel:
    """Logistic regression on standardized features (pos/neg: (n, 16, 96))."""
    from sklearn.linear_model import LogisticRegression

    x = np.concatenate([pos, neg]).reshape(len(pos) + len(neg), -1)
    y = np.concatenate([np.ones(len(pos)), np.zeros(len(neg))])
    mean, scale = x.mean(axis=0), x.std(axis=0) + 1e-6
    clf = LogisticRegression(C=c, class_weight="balanced", max_iter=3000)
    clf.fit((x - mean) / scale, y)
    return WakeModel(mean, scale, clf.coef_[0], float(clf.intercept_[0]), 0.5)


# ----------------------------------------------------------------------------
# Training
#   The threshold sits between the held-out talk scores and the held-out take scores.
# ----------------------------------------------------------------------------

@dataclass
class TrainReport:
    takes_used: int
    held_out_found: int
    held_out_total: int
    false_alarms: int
    negatives: int
    threshold: float

    def text(self) -> str:
        return (f"Learned from {self.takes_used} takes. On takes it hadn't seen it caught "
                f"{self.held_out_found}/{self.held_out_total}, with {self.false_alarms} false alarms "
                f"in {self.negatives} moments of other talk, lone words, sniffs and clicks.")


def train(takes: list[np.ndarray], talk: np.ndarray, background: np.ndarray,
          other_voices: list[np.ndarray] = (), progress: Callable[[str], None] = lambda s: None,
          ) -> tuple[WakeModel, TrainReport]:
    """Train on streamed features (identical to the live listener), pick the threshold on held-out
    takes and held-out talk, then retrain on everything.
    other_voices: extra speech without the word (e.g. Windows voices saying look-alike words)."""
    takes = [to_int16(t) for t in takes if word_span(to_int16(t)) is not None]
    if len(takes) < 8:
        raise ValueError(f"Only {len(takes)} takes had a clear word in them; record at least 8.")
    talk, background = to_int16(talk), to_int16(background)
    bg_pad = np.tile(background, 1 + 2 * RATE // max(1, len(background)))[: 2 * RATE]
    feats = _features()

    def windows(audio):  # 2 s of room noise first, so nothing real falls in the warm-up
        return stream_features(np.concatenate([bg_pad, to_int16(audio)]), feats)[1]

    rng = np.random.default_rng(1)
    order = rng.permutation(len(takes))
    held = set(order[: max(2, len(takes) // 5)].tolist())
    train_takes = [t for i, t in enumerate(takes) if i not in held]
    test_takes = [t for i, t in enumerate(takes) if i in held]

    progress("Listening to your recordings…")
    audio, ends = word_stream(train_takes, background, per_take=6)
    times, w = stream_features(audio, feats)
    pos_mask, neg_mask = label_windows(times, ends)
    cut = int(len(talk) * 0.8)  # the last 20% of your talk is kept back to test false alarms
    pos_tr = w[pos_mask]
    neg_tr = np.concatenate([w[neg_mask], windows(talk[:cut]), windows(background)]
                            + [windows(v) for v in other_voices])
    test_audio, test_ends = word_stream(test_takes, background, per_take=3, seed=7)
    test_times, test_w = stream_features(test_audio, feats)
    neg_te = windows(talk[cut:])

    progress("Adding sniffs, breaths and lone words it must ignore…")
    words_only = [t[sp[0]: sp[1]] for t in takes if (sp := word_span(t)) is not None]
    level = float(np.median([np.sqrt(np.mean(w.astype(np.float64) ** 2)) for w in words_only]))
    lone = short_sounds(talk, level) + lone_words(level)
    order_lone = rng.permutation(len(lone))
    split = int(len(lone) * 0.75)
    lone_tr_audio, _ = segment_stream([lone[i] for i in order_lone[:split]], background, 2, seed=11)
    lone_te_audio, _ = segment_stream([lone[i] for i in order_lone[split:]], background, 1, seed=12)
    lone_tr = stream_features(lone_tr_audio, feats)[1]
    lone_te = stream_features(lone_te_audio, feats)[1]
    neg_tr = np.concatenate([neg_tr, lone_tr])

    progress("Learning your voice…")
    model = fit(pos_tr, neg_tr)
    peaks = word_peaks(test_times, np.atleast_1d(model.score(test_w)), test_ends)
    s_talk = held3(np.atleast_1d(model.score(neg_te))) if len(neg_te) >= 3 else np.zeros(1)
    s_lone = held3(np.atleast_1d(model.score(lone_te))) if len(lone_te) >= 3 else np.zeros(1)
    s_neg = np.concatenate([s_talk, s_lone])
    talk_max, word_mid = float(s_neg.max(initial=0.0)), float(np.median(peaks))
    if talk_max < word_mid:  # clean gap: a third of the way up from your talk to the word
        threshold = talk_max + max(0.02, (word_mid - talk_max) / 3)
    else:  # overlap: accept a rare false alarm rather than never waking
        threshold = float(np.percentile(s_neg, 99.5)) + 0.02
    threshold = float(np.clip(threshold, 0.05, 0.95))
    found = int((peaks >= threshold).sum())
    report = TrainReport(len(takes), found, len(peaks), int((s_neg >= threshold).sum()), len(s_neg),
                         round(threshold, 3))
    progress(f"talk max {s_talk.max():.2f}, lone sounds max {s_lone.max():.2f}, words median {word_mid:.2f}")

    progress("Final model…")  # everything, reusing the features computed above
    test_pos, test_neg = label_windows(test_times, test_ends)
    final = fit(np.concatenate([pos_tr, test_w[test_pos]]),
                np.concatenate([neg_tr, test_w[test_neg], neg_te, lone_te]))
    final.threshold = threshold
    return final, report


# ----------------------------------------------------------------------------
# Live listening
#   Fires on 3 frames in a row above the threshold AND the Silero speech detector agreeing.
# ----------------------------------------------------------------------------

class WakeListener:
    """Background microphone: wake word, then the request until a pause.

    on_wake(): the word was heard (show the listening state).
    greet(): blocking, says "At your service, Sir." before recording starts (its echo is discarded).
    on_audio(audio | None, follow_up=False): the request as float32 16 kHz, or None if nothing
        followed. follow_up=True when it was heard in a follow-up window (see follow_up()).
    paused(): True while something else uses the mic or the assistant is talking.
    on_follow(seconds): a follow-up window opened (seconds > 0) or closed (0), for the UI.
    """

    def __init__(self, model: WakeModel, on_wake: Callable[[], None], on_audio: Callable,
                 paused: Callable[[], bool] = lambda: False, greet: Callable[[], None] = lambda: None,
                 silence_s: float = 0.9, wait_s: float = 4.0, max_s: float = 15.0,
                 on_follow: Callable[[float], None] = lambda s: None):
        self.model = model
        self.on_follow = on_follow
        # Follow-up request: (window length, must start before this monotonic time). See follow_up().
        self._follow: tuple[float, float] | None = None
        self.on_wake, self.on_audio, self.paused, self.greet = on_wake, on_audio, paused, greet
        self.silence_s, self.wait_s, self.max_s = silence_s, wait_s, max_s
        self.level = 0.0
        self.capturing = False
        self._q: queue.Queue = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self._stream = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import sounddevice as sd

        def callback(indata, frames, t, status):
            try:
                self._q.put_nowait(indata[:, 0].copy())
            except queue.Full:
                pass  # the worker fell behind; dropping audio beats blocking the driver

        self._stop.clear()
        self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK,
                                      callback=callback)
        self._stream.start()
        self._thread = threading.Thread(target=self._run, name="wake-word", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    # ------------------------------------------------------------------------
    # Follow-up window ("and reply yes to him" without saying "Ken" again)
    #   The app calls follow_up() once Ken has finished speaking an answer to a spoken request.
    #   The listening loop then records like after "Ken" (no greeting), but waits only `seconds`
    #   for speech, and needs at least min_speech of real speech so a cough or a click is ignored.
    #   The request expires if the loop can't start it soon (e.g. Ken got busy with something else).
    # ------------------------------------------------------------------------

    def follow_up(self, seconds: float, start_within: float = 5.0) -> None:
        if seconds > 0:
            self._follow = (seconds, time.monotonic() + start_within)

    def cancel_follow_up(self) -> None:
        self._follow = None

    def _take_follow_up(self) -> float:
        """The window length if a follow-up should start now, else 0 (and an expired one is dropped)."""
        follow = self._follow
        if follow is None or self.paused():
            return 0.0
        self._follow = None
        return follow[0] if time.monotonic() <= follow[1] else 0.0

    # ------------------------------------------------------------------------
    # The listening loop
    # ------------------------------------------------------------------------

    def _run(self) -> None:
        from openwakeword.vad import VAD

        feats, vad = _features(), VAD()
        hits, cooldown_until = 0, time.monotonic() + 2.0  # the feature buffer starts with noise
        speech = deque([0.0], maxlen=10)  # speech probability over the last 0.8 s
        while not self._stop.is_set():
            try:
                chunk = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            self.level = min(1.0, float(np.sqrt(np.mean((chunk / 32768.0) ** 2))) * 8)
            if seconds := self._take_follow_up():
                while not self._q.empty():  # drop the end of Ken's own voice
                    self._q.get_nowait()
                vad.reset_states()
                self.on_follow(seconds)
                audio = self._capture(vad, wait_s=seconds, min_speech=0.4)
                self.on_follow(0.0)
                self.on_audio(audio, follow_up=True)
                feats.reset()
                cooldown_until = time.monotonic() + 2.0
                continue
            feats(chunk)
            speech.append(float(vad.predict(chunk, frame_size=640)))
            if self.paused() or time.monotonic() < cooldown_until:
                hits = 0
                continue
            score = self.model.score(feats.get_features(16)[0])
            hits = hits + 1 if score >= self.model.threshold else 0
            # Three frames in a row (240 ms), AND it was speech: sniffs, breaths and clicks score
            # under 0.45 on the speech detector, the owner's "Ken" 0.75-0.97.
            if hits >= 3 and max(speech) >= SPEECH_GATE:
                hits = 0
                self.on_wake()
                try:
                    self.greet()
                except Exception as exc:
                    print(f"  ⚠  greeting failed: {exc}")
                while not self._q.empty():  # drop what the mic heard while Ken was talking
                    self._q.get_nowait()
                vad.reset_states()
                self.on_audio(self._capture(vad))
                feats.reset()
                cooldown_until = time.monotonic() + 2.0

    # ------------------------------------------------------------------------
    # Recording the request
    #   Until 0.9 s of silence after speech; nothing if no speech came.
    # ------------------------------------------------------------------------

    def _capture(self, vad, wait_s: float | None = None, min_speech: float = 0.0) -> np.ndarray | None:
        """Record until `silence_s` of quiet after speech; None if no speech within `wait_s`
        (default self.wait_s), or if there was less than `min_speech` seconds of speech in all."""
        wait_s = self.wait_s if wait_s is None else wait_s
        self.capturing = True
        chunks, spoke, quiet, start = [], False, 0.0, time.monotonic()
        voiced = 0.0  # seconds the speech detector called speech
        try:
            while not self._stop.is_set():
                try:
                    chunk = self._q.get(timeout=0.5)
                except queue.Empty:
                    continue
                chunks.append(chunk)
                self.level = min(1.0, float(np.sqrt(np.mean((chunk / 32768.0) ** 2))) * 8)
                speech = vad.predict(chunk, frame_size=640) > 0.5
                elapsed = time.monotonic() - start
                if speech:
                    spoke, quiet = True, 0.0
                    voiced += CHUNK / RATE
                else:
                    quiet += CHUNK / RATE
                if spoke and quiet >= self.silence_s:
                    break
                if (not spoke and elapsed >= wait_s) or elapsed >= self.max_s:
                    break
        finally:
            self.capturing = False
        if not spoke or voiced < min_speech:
            return None
        return np.concatenate(chunks).astype(np.float32) / 32768.0
