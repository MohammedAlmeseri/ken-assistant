"""What Ken says when you call him ("At your service, Sir."), in the current voice.

Made once per voice/persona and cached in data/voice/, so it plays the instant the wake word is
heard and costs nothing after the first time. Falls back to a short blip if it can't be made.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import numpy as np

# ----------------------------------------------------------------------------
# The greeting
#   'At your service, Sir.' is synthesized once per voice and personality and cached in data/voice/.
# ----------------------------------------------------------------------------

GREETING = {"butler": "At your service, Sir.", "plain": "Yes?"}
OPENAI_RATE = 24000


# ----------------------------------------------------------------------------
# Greeter
#   Plays it (blocking) so recording starts only after it, and its echo is dropped.
# ----------------------------------------------------------------------------

class Greeter:
    def __init__(self, prefs: dict, cache_dir: Path, text: str = ""):
        self.prefs = prefs  # the live TTS prefs: engine, openai_voice, openai_model, persona, en
        self.cache_dir = cache_dir
        self.fixed_text = text  # a voice preview says this instead of the greeting
        self._cache: dict[str, tuple[np.ndarray, int]] = {}
        self._lock = threading.Lock()

    def text(self) -> str:
        if self.fixed_text:
            return self.fixed_text
        return GREETING.get(self.prefs.get("persona", "plain"), GREETING["plain"])

    def _key(self) -> str:
        p = self.prefs
        parts = [self.text(), p.get("engine", ""), p.get("persona", "")]
        parts += {"openai": [p.get("openai_voice", ""), p.get("openai_model", "")],
                  "edge": [p.get("edge_en", ""), p.get("rate", 1.0)]}.get(p.get("engine"), [p.get("en", "")])
        return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12]

    def audio(self) -> tuple[np.ndarray, int] | None:
        """(int16 samples, rate), made and cached on first use. None if it couldn't be made."""
        key = self._key()
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            path = self.cache_dir / f"greeting-{key}.npz"
            try:
                if path.exists():
                    d = np.load(path)
                    clip = (d["audio"], int(d["rate"]))
                else:
                    clip = self._make()
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    np.savez(path, audio=clip[0], rate=clip[1])
            except Exception as exc:
                print(f"  ⚠  greeting voice failed ({type(exc).__name__}: {str(exc)[:120]}); using a beep.")
                return None
            self._cache[key] = clip
            return clip

    def _make(self) -> tuple[np.ndarray, int]:
        from .tts import EdgeTTSEngine, OneCoreEngine, OpenAITTSEngine, edge_synthesize, tts_style

        if self.prefs.get("engine") == "edge":
            text = self.text()
            lang = "ar" if any("\u0600" <= ch <= "\u06ff" for ch in text) else "en"
            samples, rate = edge_synthesize(text, EdgeTTSEngine(self.prefs).voice_for(lang),
                                            float(self.prefs.get("rate", 1.0)))
            return (np.clip(samples, -1, 1) * 32767).astype(np.int16), int(rate)
        if self.prefs.get("engine") == "openai":
            client = OpenAITTSEngine(self.prefs).client
            resp = client.audio.speech.create(
                model=self.prefs.get("openai_model") or "gpt-4o-mini-tts",
                voice=self.prefs.get("openai_voice") or "marin", input=self.text(), response_format="pcm",
                instructions=tts_style("en", self.prefs.get("tone", ""), self.prefs.get("persona", "plain")))
            return np.frombuffer(resp.content, dtype=np.int16).copy(), OPENAI_RATE
        samples, rate, _ = OneCoreEngine(self.prefs).synthesize(self.text(), "en")
        x = np.asarray(samples, dtype=np.float32)
        x = x.mean(axis=1) if x.ndim > 1 else x
        if np.abs(x).max() <= 1.0:
            x = x * 32767
        return x.astype(np.int16), int(rate)

    def warm(self) -> None:
        """Make it in the background so the first "Ken" doesn't wait for it."""
        threading.Thread(target=self.audio, daemon=True).start()

    def play(self) -> None:
        """Blocking: the wake listener starts recording the request only after this returns."""
        clip = self.audio()
        if clip is None:
            from ..ui.timer_widget import chime

            chime("wake")
            return
        import sounddevice as sd

        samples, rate = clip
        with sd.OutputStream(samplerate=rate, channels=1, dtype="int16") as out:
            out.write(samples.reshape(-1, 1))
