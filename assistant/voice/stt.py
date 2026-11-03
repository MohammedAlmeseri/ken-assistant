"""Speech-to-text: faster-whisper on CPU (int8), push-to-talk recording."""

from __future__ import annotations

import asyncio
import threading

from ..config import Settings

SAMPLE_RATE = 16000


# ----------------------------------------------------------------------------
# Recording
#   Push-to-talk: the microphone is recorded between start() and stop().
# ----------------------------------------------------------------------------

class Recorder:
    """Records the microphone between start() and stop()."""

    def __init__(self) -> None:
        import sounddevice  # noqa: F401  (fail early if voice extras aren't installed)

        self._chunks: list = []
        self._stream = None
        self._lock = threading.Lock()
        self.level = 0.0  # loudness of the latest chunk, 0..1, for the UI's waveform

    def start(self) -> None:
        import sounddevice as sd

        self._chunks = []
        self.level = 0.0

        def callback(indata, frames, time, status):
            with self._lock:
                self._chunks.append(indata.copy())
            self.level = min(1.0, float((indata ** 2).mean() ** 0.5) * 8)

        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback)
        self._stream.start()

    def stop(self):
        import numpy as np

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype="float32")
            return np.concatenate(self._chunks)[:, 0]


# ----------------------------------------------------------------------------
# Local speech-to-text (faster-whisper)
#   Private and free, but slow on a laptop CPU and weak at Arabic: used as the fallback.
# ----------------------------------------------------------------------------

class Transcriber:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._loaded = ""

    def _load(self):
        if self._model is None or self._loaded != self.settings.whisper_model:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.settings.whisper_model, device="cpu", compute_type="int8")
            self._loaded = self.settings.whisper_model
        return self._model

    def warm_up(self) -> None:
        self._load()

    def transcribe_sync(self, audio) -> tuple[str, str]:
        """Returns (text, detected_language)."""
        if len(audio) < SAMPLE_RATE * 0.3:
            return "", ""
        lang = None if self.settings.voice_language == "auto" else self.settings.voice_language
        segments, info = self._load().transcribe(
            audio,
            language=lang,
            beam_size=1,
            vad_filter=True,
            without_timestamps=True,
            condition_on_previous_text=False,
            initial_prompt=self.settings.whisper_initial_prompt or None,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return text, info.language

    async def transcribe(self, audio) -> tuple[str, str]:
        return await asyncio.to_thread(self.transcribe_sync, audio)


# ----------------------------------------------------------------------------
# OpenAI speech-to-text
#   gpt-transcribe ($0.0045 / min). Any failure falls back to local Whisper.
# ----------------------------------------------------------------------------

def to_wav_bytes(audio) -> bytes:
    import io
    import wave

    import numpy as np

    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


class OpenAITranscriber:
    """OpenAI speech-to-text. Much better Arabic than local Whisper on this CPU, but the
    audio leaves the machine. Falls back to local Whisper if the call fails (e.g. offline)."""

    def __init__(self, settings: Settings, fallback: Transcriber | None = None):
        from ..openai_client import async_client

        self.settings = settings
        self.client = async_client("speech-to-text")
        self.fallback = fallback or Transcriber(settings)
        self.audio_seconds = 0.0  # sent to OpenAI and transcribed there, for the cost estimate

    def warm_up(self) -> None:
        pass  # nothing to load; local Whisper loads only if a fallback is needed

    async def transcribe(self, audio) -> tuple[str, str]:
        from .tts import detect_language

        if len(audio) < SAMPLE_RATE * 0.3:
            return "", ""
        kwargs = {}
        if self.settings.voice_language != "auto":
            kwargs["language"] = self.settings.voice_language
        if self.settings.whisper_initial_prompt:
            kwargs["prompt"] = self.settings.whisper_initial_prompt
        try:
            resp = await self.client.audio.transcriptions.create(
                model=self.settings.openai_stt_model,
                file=("speech.wav", to_wav_bytes(audio), "audio/wav"),
                **kwargs,
            )
        except Exception as exc:
            print(f"  ⚠  OpenAI transcription failed ({type(exc).__name__}); using local Whisper.")
            return await self.fallback.transcribe(audio)
        self.audio_seconds += len(audio) / SAMPLE_RATE
        text = (resp.text or "").strip()
        return text, detect_language(text) if text else ""


# ----------------------------------------------------------------------------
# Choosing
#   'auto' follows the brain: OpenAI audio only when text already goes to OpenAI.
# ----------------------------------------------------------------------------

def make_transcriber(settings: Settings):
    """'auto' follows the LLM backend: OpenAI audio only when text already goes to OpenAI."""
    choice = settings.stt_backend
    if choice == "auto":
        choice = "openai" if settings.backend == "openai" else "local"
    return OpenAITranscriber(settings) if choice == "openai" else Transcriber(settings)
