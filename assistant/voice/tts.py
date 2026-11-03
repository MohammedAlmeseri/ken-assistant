"""Text-to-speech with one ordered speech queue and barge-in (stop).

Engines: Windows OneCore voices (default, free, local), OpenAI gpt-4o-mini-tts
(natural, paid, text goes to OpenAI), older SAPI5 voices, and printing as a last
resort, so the rest of the app always works. VoiceEngine switches between Windows
and OpenAI live and falls back to Windows if an OpenAI call fails.
"""

from __future__ import annotations

import asyncio
import queue
import re
import threading
from typing import Callable, Protocol

# ----------------------------------------------------------------------------
# Language detection and cleanup
# ----------------------------------------------------------------------------

_ARABIC = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[A-Za-z]")


def detect_language(text: str) -> str:
    return "ar" if len(_ARABIC.findall(text)) >= len(_LATIN.findall(text)) and _ARABIC.search(text) else "en"


def speakable(text: str) -> str:
    """Strip markdown so the voice doesn't read symbols."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`#>|]+", " ", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


# ----------------------------------------------------------------------------
# Engines: the interface and Windows voices
#   SAPI5 and OneCore (WinRT) voices: offline and robotic, the last fallback.
# ----------------------------------------------------------------------------

class Engine(Protocol):
    def speak(self, text: str, language: str, should_stop: Callable[[], bool]) -> None: ...


class PrintEngine:
    def speak(self, text: str, language: str, should_stop: Callable[[], bool]) -> None:
        print(f"🔊 [{language}] {text}")


class SapiEngine:
    """Windows SAPI5 voices. Must be created on the thread that uses it (COM)."""

    SVSF_ASYNC, SVSF_PURGE = 1, 2

    def __init__(self) -> None:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        self.voice = win32com.client.Dispatch("SAPI.SpVoice")
        self.voices: dict[str, object] = {}
        tokens = self.voice.GetVoices()
        for i in range(tokens.Count):
            token = tokens.Item(i)
            desc = token.GetDescription()
            if "Arabic" in desc and "ar" not in self.voices:
                self.voices["ar"] = token
            if "English" in desc and "en" not in self.voices:
                self.voices["en"] = token

    def speak(self, text: str, language: str, should_stop: Callable[[], bool]) -> None:
        token = self.voices.get(language)
        if token is not None:
            self.voice.Voice = token
        self.voice.Speak(text, self.SVSF_ASYNC)
        while not self.voice.WaitUntilDone(50):
            if should_stop():
                self.voice.Speak("", self.SVSF_ASYNC | self.SVSF_PURGE)
                return


class OneCoreEngine:
    """Windows OneCore voices via WinRT. These include the Arabic voices from the
    Windows Arabic speech pack, which the older SAPI5 API can't see.

    `prefs` is shared with the caller and read on every speak, so voice and speed can
    change while running: {"en": "Mark", "ar": "Hoda", "rate": 1.0}. A voice pref is a
    case-insensitive part of the voice name; empty means the Windows default voice when
    it speaks that language, else the first installed one."""

    def __init__(self, prefs: dict | None = None) -> None:
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer

        self.synth = SpeechSynthesizer()
        self.prefs = prefs if prefs is not None else {}
        self.all = list(SpeechSynthesizer.all_voices)
        self.default = SpeechSynthesizer.default_voice
        self.voices: dict[str, object] = {}  # languages available (for the startup warning)
        for v in self.all:
            self.voices.setdefault(self._lang(v), v)

    @staticmethod
    def _lang(voice) -> str:
        return voice.language.split("-")[0].lower()

    def voice_list(self) -> list[tuple[str, str]]:
        return [(v.display_name, v.language) for v in self.all]

    def pick(self, language: str):
        same = [v for v in self.all if self._lang(v) == language]
        wanted = (self.prefs.get(language) or "").lower()
        if wanted:
            for v in same:
                if wanted in v.display_name.lower():
                    return v
        if self.default is not None and self._lang(self.default) == language:
            return self.default
        return same[0] if same else None

    async def _synthesize(self, text: str) -> bytes:
        from winrt.windows.storage.streams import DataReader

        stream = await self.synth.synthesize_text_to_stream_async(text)
        reader = DataReader(stream.get_input_stream_at(0))
        await reader.load_async(stream.size)
        return bytes(reader.read_buffer(stream.size))

    def synthesize(self, text: str, language: str):
        """Returns (samples as int16 numpy array, sample_rate, channels)."""
        import io
        import wave

        import numpy as np

        voice = self.pick(language)
        if voice is not None:
            self.synth.voice = voice
        self.synth.options.speaking_rate = float(self.prefs.get("rate", 1.0))  # WinRT range 0.5-6.0
        data = asyncio.run(self._synthesize(text))
        with wave.open(io.BytesIO(data)) as w:
            frames = w.readframes(w.getnframes())
            channels, rate = w.getnchannels(), w.getframerate()
        samples = np.frombuffer(frames, dtype=np.int16)
        return (samples.reshape(-1, channels) if channels > 1 else samples), rate, channels

    def speak(self, text: str, language: str, should_stop: Callable[[], bool]) -> None:
        import time

        import sounddevice as sd

        samples, rate, _ = self.synthesize(text, language)
        sd.play(samples, rate)
        end = time.monotonic() + len(samples) / rate + 0.2
        while time.monotonic() < end:
            if should_stop():
                sd.stop()
                return
            time.sleep(0.05)
        sd.wait()


# ----------------------------------------------------------------------------
# OpenAI voices
#   Streamed PCM, with style instructions per language and persona.
# ----------------------------------------------------------------------------

OPENAI_TTS_RATE = 24000  # response_format="pcm" is 24 kHz, 16-bit, mono
# Short instructions ("warm and clear") gave a flat, read-out-loud delivery; spelling out the
# delivery, pace and pauses makes it sound like someone talking, not narrating.
_TALKING = ("Delivery: relaxed and conversational, like a friend answering on the phone, not a narrator or "
            "a news reader. Natural rhythm with small pauses between phrases, light emphasis on the key "
            "word, never sing-song or over-excited. Pace: normal talking speed, not rushed.")
OPENAI_TTS_STYLE = {
    "en": "Voice: a friendly young American personal assistant, warm and casual. " + _TALKING,
    "ar": ("Voice: a young Egyptian from Cairo talking in everyday Egyptian Arabic (عامية مصرية), with a "
           "native Cairene accent and pronunciation (ق as a glottal stop, ج as hard g), never Modern Standard "
           "Arabic or a foreign accent. Warm and easy-going. " + _TALKING),
    "ar_saudi": ("Voice: a polite young Saudi professional speaking in a native Saudi (Najdi) accent, "
                 "courteous and calm. " + _TALKING),
    "en_pro": "Voice: a calm, courteous American professional assistant. " + _TALKING,
}


BUTLER_STYLE = {
    "en": ("Voice: a distinguished older English butler with a refined Received Pronunciation accent. Calm, "
           "warm, measured and impeccably polite, with a hint of dry wit; never rushed. 'Sir' is said with "
           "quiet respect. " + _TALKING),
    "ar": ("Voice: a calm, distinguished older gentleman-butler speaking natural Egyptian Arabic with native "
           "Cairene pronunciation, formal-polite and warm, measured pace. Say the word 'Sir' in English with a "
           "refined British accent. " + _TALKING),
}


def tts_style(language: str, tone: str, persona: str = "plain") -> str:
    if persona == "butler":
        return BUTLER_STYLE["ar" if language == "ar" else "en"]
    if language == "ar":
        return OPENAI_TTS_STYLE["ar_saudi" if tone == "saudi_professional" else "ar"]
    return OPENAI_TTS_STYLE["en_pro" if tone in ("us_professional", "saudi_professional") else "en"]


class OpenAITTSEngine:
    """OpenAI text-to-speech, streamed so playback starts before the whole reply is generated.
    Reads prefs on every call: {"openai_voice": "marin", "openai_model": "gpt-4o-mini-tts", "rate": 1.0}."""

    def __init__(self, prefs: dict, client=None) -> None:
        if client is None:
            from ..openai_client import sync_client

            client = sync_client("voice")
        self.client = client
        self.prefs = prefs
        self.chars = 0  # what was sent to OpenAI this session, for the usage line
        self.audio_seconds = 0.0

    def speak(self, text: str, language: str, should_stop: Callable[[], bool]) -> None:
        import numpy as np
        import sounddevice as sd

        kwargs = {
            "model": self.prefs.get("openai_model") or "gpt-4o-mini-tts",
            "voice": self.prefs.get("openai_voice") or "marin",
            "input": text,
            "response_format": "pcm",
            "instructions": tts_style(language, self.prefs.get("tone", "egyptian_friendly"),
                                      self.prefs.get("persona", "plain")),
        }
        rate = float(self.prefs.get("rate", 1.0))
        if rate != 1.0:
            kwargs["speed"] = max(0.25, min(4.0, rate))
        self.chars += len(text)
        leftover = b""
        with self.client.audio.speech.with_streaming_response.create(**kwargs) as response:
            with sd.RawOutputStream(samplerate=OPENAI_TTS_RATE, channels=1, dtype="int16") as out:
                for chunk in response.iter_bytes(4800):  # ~0.1 s of audio per chunk
                    if should_stop():
                        out.abort()
                        return
                    data = leftover + chunk
                    usable = len(data) - len(data) % 2  # never split a 16-bit sample
                    leftover = data[usable:]
                    if usable:
                        out.write(np.frombuffer(data[:usable], dtype=np.int16).tobytes())
                        self.audio_seconds += usable / 2 / OPENAI_TTS_RATE


# ----------------------------------------------------------------------------
# Microsoft Edge voices (the default)
#   Free neural voices (an unofficial service): MP3 from edge-tts, decoded with PyAV.
# ----------------------------------------------------------------------------

EDGE_DEFAULTS = {"en": "en-GB-RyanNeural", "ar": "ar-EG-ShakirNeural"}


def edge_rate(rate: float) -> str:
    """1.3 -> '+30%' (Edge's speed format)."""
    return f"{round((max(0.5, min(2.0, rate)) - 1) * 100):+d}%"


def edge_synthesize(text: str, voice: str, rate: float = 1.0):
    """Microsoft Edge's free "Read aloud" voices (unofficial service, no account). The text goes to
    Microsoft. Returns (float32 mono samples, sample rate)."""
    import asyncio
    import io

    import av
    import edge_tts
    import numpy as np

    async def fetch() -> bytes:
        mp3 = b""
        async for chunk in edge_tts.Communicate(text, voice, rate=edge_rate(rate)).stream():
            if chunk["type"] == "audio":
                mp3 += chunk["data"]
        return mp3

    mp3 = asyncio.run(fetch())
    if not mp3:
        raise RuntimeError("Edge returned no audio")
    with av.open(io.BytesIO(mp3)) as container:
        stream = container.streams.audio[0]
        samples = np.concatenate([frame.to_ndarray().reshape(-1) for frame in container.decode(stream)])
        return samples.astype(np.float32), stream.rate


class EdgeTTSEngine:
    """Edge neural voices: one for English (default British "Ryan", the butler) and one for Arabic
    (default Egyptian "Shakir"). Reads prefs on every call: edge_en, edge_ar, rate."""

    def __init__(self, prefs: dict):
        self.prefs = prefs

    def voice_for(self, language: str) -> str:
        key = "ar" if language == "ar" else "en"
        return self.prefs.get(f"edge_{key}") or EDGE_DEFAULTS[key]

    def speak(self, text: str, language: str, should_stop: Callable[[], bool]) -> None:
        import time

        import sounddevice as sd

        samples, rate = edge_synthesize(text, self.voice_for(language), float(self.prefs.get("rate", 1.0)))
        if should_stop():
            return
        sd.play(samples, rate)
        end = time.monotonic() + len(samples) / rate + 0.2
        while time.monotonic() < end:
            if should_stop():
                sd.stop()
                return
            time.sleep(0.05)


# ----------------------------------------------------------------------------
# The fallback chain
#   Edge or OpenAI first; on ANY error the same sentence is spoken by a Windows voice instead.
# ----------------------------------------------------------------------------

class VoiceEngine:
    """Speaks with Edge voices when prefs["engine"] == "edge", OpenAI when "openai", otherwise
    Windows voices. Any Edge/OpenAI failure (offline, quota, bad voice name) falls back to
    Windows for that sentence, so a reply is never lost."""

    def __init__(self, prefs: dict, local: Engine, openai_factory: Callable[[], Engine] | None = None,
                 edge_factory: Callable[[], Engine] | None = None):
        self.prefs = prefs
        self.local = local
        self._openai_factory = openai_factory or (lambda: OpenAITTSEngine(prefs))
        self._openai: Engine | None = None
        self.edge = (edge_factory or (lambda: EdgeTTSEngine(prefs)))()
        self.voices = getattr(local, "voices", {})
        if hasattr(local, "pick"):
            self.pick = local.pick
        if hasattr(local, "voice_list"):
            self.voice_list = local.voice_list

    @property
    def openai(self) -> Engine:
        if self._openai is None:
            self._openai = self._openai_factory()
        return self._openai

    def speak(self, text: str, language: str, should_stop: Callable[[], bool]) -> None:
        engine = self.prefs.get("engine")
        if engine in ("openai", "edge"):
            try:
                return (self.openai if engine == "openai" else self.edge).speak(text, language, should_stop)
            except Exception as exc:
                name = {"openai": "OpenAI", "edge": "Edge"}[engine]
                print(f"  ⚠  {name} voice failed ({type(exc).__name__}: {str(exc)[:120]}); using the Windows voice.")
        self.local.speak(text, language, should_stop)


# ----------------------------------------------------------------------------
# Factories
# ----------------------------------------------------------------------------

def local_engine(prefs: dict | None = None) -> Engine:
    try:
        return OneCoreEngine(prefs)
    except Exception:
        pass
    try:
        return SapiEngine()
    except Exception:
        return PrintEngine()


def default_engine(prefs: dict | None = None) -> Engine:
    prefs = prefs if prefs is not None else {}
    return VoiceEngine(prefs, local_engine(prefs))


def available_languages(engine: Engine) -> set[str]:
    return set(getattr(engine, "voices", {}) or ())


# ----------------------------------------------------------------------------
# The speech queue
#   One thread plays lines in order. stop() (a new request, the hotkey, 'Ken') cuts it off at once.
# ----------------------------------------------------------------------------

class SpeechQueue:
    """Plays texts one at a time, in order. stop() cuts speech off and drops the queue.

    A generation counter makes stop() race-free: items queued before a stop are skipped.
    """

    def __init__(self, engine_factory: Callable[[], Engine] = default_engine):
        self._queue: queue.Queue[tuple[int, str]] = queue.Queue()
        self._generation = 0
        self._pending = 0  # queued + currently speaking
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self.engine_name = ""
        self.languages: set[str] = set()
        self.voice_list: list[tuple[str, str]] = []
        self._engine = None
        self._thread = threading.Thread(target=self._run, args=(engine_factory,), daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)

    def say(self, text: str) -> None:
        text = speakable(text)
        if text:
            with self._lock:
                self._pending += 1
                self._queue.put((self._generation, text))

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            try:
                while True:
                    self._queue.get_nowait()
                    self._pending -= 1
            except queue.Empty:
                pass

    def current_voices(self) -> dict[str, str]:
        """Which voice each language will use now (after any /tts change)."""
        if not hasattr(self._engine, "pick"):
            return {}
        return {lang: v.display_name for lang in self.languages if (v := self._engine.pick(lang)) is not None}

    @property
    def speaking(self) -> bool:
        return self._pending > 0

    async def wait_done(self) -> None:
        while self._pending > 0:
            await asyncio.sleep(0.05)

    def _run(self, engine_factory: Callable[[], Engine]) -> None:
        engine = engine_factory()
        self.engine_name = type(engine).__name__
        self.languages = available_languages(engine)
        self.voice_list = engine.voice_list() if hasattr(engine, "voice_list") else []
        self._engine = engine
        self._ready.set()
        while True:
            generation, text = self._queue.get()
            if generation == self._generation:
                try:
                    engine.speak(text, detect_language(text), lambda: generation != self._generation)
                except Exception as exc:  # never let speech crash the app
                    print(f"[tts error] {exc}")
            with self._lock:
                self._pending -= 1
