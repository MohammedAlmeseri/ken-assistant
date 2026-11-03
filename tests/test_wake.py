"""Wake word pieces that don't need a microphone or the feature models."""

from __future__ import annotations

import numpy as np

from assistant.voice.wake import RATE, WakeModel, held3, label_windows, word_span, word_stream


# ----------------------------------------------------------------------------
# Wake-word training pieces
# ----------------------------------------------------------------------------

def tone(seconds: float, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def test_word_span_finds_the_word_and_rejects_silence():
    quiet = np.random.default_rng(0).normal(0, 0.002, int(0.5 * RATE)).astype(np.float32)
    take = np.concatenate([quiet, tone(0.4), quiet])
    start, end = word_span(take)
    assert abs(start / RATE - 0.5) < 0.06 and abs(end / RATE - 0.9) < 0.08
    assert word_span(np.concatenate([quiet, quiet])) is None  # nothing said
    assert word_span(np.concatenate([quiet, tone(2.0), quiet])) is None  # too long for one word


def test_word_stream_and_labels():
    quiet = np.random.default_rng(0).normal(0, 0.002, int(0.5 * RATE)).astype(np.float32)
    takes = [np.concatenate([quiet, tone(0.35), quiet])] * 2
    audio, ends = word_stream(takes, quiet, per_take=3)
    assert audio.dtype == np.int16 and len(ends) == 6
    assert all(b > a for a, b in zip(ends, ends[1:]))
    times = np.arange(2.0, len(audio) / RATE, 0.08)
    pos, neg = label_windows(times, ends)
    assert pos.any() and neg.any() and not (pos & neg).any()
    for e in ends:  # right after a word is positive, during the word is not
        assert pos[(times >= e + 0.1) & (times <= e + 0.4)].all()
        assert not pos[(times >= e - 0.3) & (times < e)].any()


def test_held3_needs_three_frames_in_a_row():
    assert held3(np.array([0.9, 0.1, 0.9, 0.9, 0.9, 0.2])).tolist() == [0.1, 0.1, 0.9, 0.2]


def test_model_scores_and_round_trips(tmp_path):
    rng = np.random.default_rng(0)
    model = WakeModel(np.zeros(1536), np.ones(1536), rng.normal(0, 0.01, 1536), -1.0, 0.42)
    x = rng.normal(0, 1, (16, 96)).astype(np.float32)
    single = model.score(x)
    batch = model.score(np.stack([x, x]))
    assert 0 < single < 1 and np.allclose(batch, single)
    model.save(tmp_path / "ken.npz")
    again = WakeModel.load(tmp_path / "ken.npz")
    assert again.threshold == 0.42 and abs(again.score(x) - single) < 1e-9


# ----------------------------------------------------------------------------
# Greeting cache
# ----------------------------------------------------------------------------

def test_greeting_is_cached_per_voice(tmp_path, monkeypatch):
    from assistant.voice.greeting import Greeter

    made = []
    prefs = {"engine": "openai", "openai_voice": "ballad", "openai_model": "gpt-4o-mini-tts", "persona": "butler"}
    greeter = Greeter(prefs, tmp_path)
    monkeypatch.setattr(greeter, "_make", lambda: made.append(prefs["openai_voice"]) or (np.zeros(10, np.int16), 24000))
    assert greeter.text() == "At your service, Sir."
    greeter.audio()
    greeter.audio()
    assert made == ["ballad"]  # made once, then from memory
    assert Greeter(prefs, tmp_path).audio()[1] == 24000  # and from disk in a new session
    prefs["openai_voice"] = "cedar"  # a new voice gets a new greeting
    greeter.audio()
    assert made == ["ballad", "cedar"]
    prefs["persona"] = "plain"
    assert greeter.text() == "Yes?"
