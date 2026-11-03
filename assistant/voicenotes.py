"""WhatsApp voice notes, transcribed only when the owner asks ("what did Ahmed say in his voice note?").

How a request flows:
  1. The owner's latest message must mention a voice note (has_voice_intent). Nothing is ever
     transcribed in the background, and old notes are never backfilled.
  2. The newest voice notes of that chat are found in the local WhatsApp cache (messages.db).
  3. The app (never the model) asks the WhatsApp server to download each one. The server decrypts
     it locally and does NOT tell the sender: no blue "played" microphone.
  4. The audio is decoded here and sent to the same speech-to-text Ken uses for the owner's voice
     (gpt-transcribe), so it shows up in the cost counter.
  5. The text is cached by message ID in data/voice_notes.db, so a note is never paid for twice,
     and chat summaries show it instead of "[audio]" from then on.

Transcripts are other people's words: they are wrapped as untrusted data like any message.
"""

from __future__ import annotations

import io
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

import numpy as np

from .inbox import _contact_names, _parse_time
from .llm import ToolSpec

RATE = 16_000  # what the speech-to-text expects
MAX_NOTES = 5  # per request
MAX_SECONDS = 300.0  # audio per request (5 minutes ≈ $0.02)


# ----------------------------------------------------------------------------
# The tool the model calls
# ----------------------------------------------------------------------------

VOICE_NOTES_TOOL = ToolSpec(
    name="voice_notes",
    description="Transcribe the newest WhatsApp voice notes in a chat. Only when the user asks about a voice "
                "note / audio / recording.",
    parameters={
        "type": "object",
        "properties": {
            "chat": {"type": "string", "description": "chat JID from find_chat (or a name)"},
            "count": {"type": "integer", "description": "how many of the newest notes, default 1, max 5"},
            "sender": {"type": "string", "enum": ["them", "me", "any"], "description": "whose notes, default them"},
        },
        "required": ["chat"],
    },
)


# ----------------------------------------------------------------------------
# Intent guard
#   The download tool is hidden from the model; this tool only runs when the owner's own words
#   ask about a voice note. That's what "only when I ask" means in code.
# ----------------------------------------------------------------------------

_VOICE = re.compile(r"\b(voice|audio|recording|record|vn|voicenote)s?\b|"
                    r"(فويس|ڤويس|ريكورد|تسجيل|صوتي|صوتية|صوتيه|بصمة|بصمه|اوديو|أوديو)", re.IGNORECASE)


def has_voice_intent(text: str) -> bool:
    return bool(_VOICE.search(text or ""))


# ----------------------------------------------------------------------------
# One voice note (a row of messages.db with media_type = 'audio')
# ----------------------------------------------------------------------------

@dataclass
class VoiceNote:
    id: str
    chat_jid: str
    sender: str  # "You", "Them", or the member's name in a group
    time: datetime
    seconds: float = 0.0
    text: str = ""


def fmt_seconds(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


# ----------------------------------------------------------------------------
# Transcript cache: data/voice_notes.db (message id -> text, length)
#   Read by contacts.recent_conversation() so cached notes appear as text everywhere.
# ----------------------------------------------------------------------------

class TranscriptCache:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, chat_jid TEXT, seconds REAL, "
                       "text TEXT, created TEXT)")

    def get_many(self, ids: list[str]) -> dict[str, tuple[float, str]]:
        if not ids:
            return {}
        with sqlite3.connect(self.path) as db:
            marks = ",".join("?" * len(ids))
            rows = db.execute(f"SELECT id, seconds, text FROM notes WHERE id IN ({marks})", ids).fetchall()
        return {i: (s, t) for i, s, t in rows}

    def put(self, note: VoiceNote) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?)",
                       (note.id, note.chat_jid, note.seconds, note.text,
                        datetime.now(timezone.utc).isoformat(timespec="seconds")))


def label(seconds: float, text: str) -> str:
    """How a transcribed note reads inside a conversation: [voice 0:42] «…»."""
    return f"[voice {fmt_seconds(seconds)}] «{text}»"


# ----------------------------------------------------------------------------
# Audio decoding
#   Voice notes are Ogg/Opus. PyAV (already used for the Edge voices) decodes and resamples them
#   to 16 kHz mono float32, the format the speech-to-text takes.
# ----------------------------------------------------------------------------

def decode(data: bytes) -> np.ndarray:
    import av

    resampler = av.AudioResampler(format="flt", layout="mono", rate=RATE)
    chunks = []
    with av.open(io.BytesIO(data)) as container:
        for frame in container.decode(container.streams.audio[0]):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):  # flush what the resampler still holds
            chunks.append(out.to_ndarray().reshape(-1))
    return np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, dtype=np.float32)


# ----------------------------------------------------------------------------
# The service
#   store_dir:  the WhatsApp server's store (messages.db, and where downloads land).
#   tools:      the MCP manager; only this class calls whatsapp__download_media.
#   transcribe: async audio -> text, supplied by the app (it also counts the cost).
# ----------------------------------------------------------------------------

class VoiceNotes:
    def __init__(self, store_dir: Path, tools, transcribe: Callable[[np.ndarray], Awaitable[str]], cache: TranscriptCache):
        self.store = store_dir
        self.tools = tools
        self.transcribe = transcribe
        self.cache = cache

    # ------------------------------------------------------------------------
    # Finding notes
    # ------------------------------------------------------------------------

    def find(self, jid: str, count: int = 1, sender: str = "them") -> list[VoiceNote]:
        """The newest `count` voice notes in a chat, oldest first, with cached text filled in."""
        db_path = self.store / "messages.db"
        if not db_path.exists():
            return []
        who = {"them": " AND is_from_me = 0", "me": " AND is_from_me = 1"}.get(sender, "")
        db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        rows = db.execute(f"SELECT id, sender, timestamp, is_from_me FROM messages WHERE chat_jid = ? "
                          f"AND media_type = 'audio'{who} ORDER BY timestamp DESC LIMIT ?",
                          (jid, max(1, min(count, MAX_NOTES)))).fetchall()
        db.close()
        names = _contact_names(self.store / "whatsapp.db") if jid.endswith("@g.us") else {}
        notes = []
        for msg_id, from_jid, ts, from_me in reversed(rows):
            t = _parse_time(ts) or datetime.now(timezone.utc)
            if from_me:
                name = "You"
            elif names:
                num = str(from_jid or "").split("@")[0].split(":")[0]
                name = names.get(num) or f"+{num}"
            else:
                name = "Them"
            notes.append(VoiceNote(str(msg_id), jid, name, t))
        for note in notes:
            if hit := self.cache.get_many([note.id]).get(note.id):
                note.seconds, note.text = hit
        return notes

    # ------------------------------------------------------------------------
    # One transcript
    # ------------------------------------------------------------------------

    async def _download(self, note: VoiceNote) -> bytes:
        """Ask the WhatsApp server to fetch and decrypt the note; read the file it wrote.
        The path must be inside the store: a strange answer must not make Ken read other files."""
        raw = await self.tools.call_tool("whatsapp__download_media", {"message_id": note.id, "chat_jid": note.chat_jid})
        if raw.startswith("Tool error"):
            raise RuntimeError(raw.removeprefix("Tool error: ")[:200])
        info = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        if not info.get("Success"):
            raise RuntimeError(str(info.get("Message") or "download failed")[:200])
        path = Path(str(info.get("Path", ""))).resolve()
        if not path.is_file() or not path.is_relative_to(self.store.resolve()):
            raise RuntimeError("the downloaded file isn't in the WhatsApp store")
        return path.read_bytes()

    async def transcript(self, note: VoiceNote, budget: float = MAX_SECONDS) -> VoiceNote:
        """Fill note.text: from the cache, or download + transcribe (at most `budget` seconds of audio)."""
        if note.text:
            return note
        import asyncio

        audio = await asyncio.to_thread(decode, await self._download(note))
        note.seconds = len(audio) / RATE
        if note.seconds > budget:  # a very long note: only its start fits this request
            audio = audio[: int(budget * RATE)]
        note.text = (await self.transcribe(audio)).strip() or "(no speech)"
        if note.seconds <= budget:  # only complete transcripts are cached
            self.cache.put(note)
        return note

    # ------------------------------------------------------------------------
    # A whole request (the tool)
    # ------------------------------------------------------------------------

    async def transcribe_chat(self, jid: str, count: int = 1, sender: str = "them") -> str:
        """The tool's result: one line per note, '[Tue 13:05] Them (0:42): «…»', or why not."""
        notes = self.find(jid, count, sender)
        if not notes:
            return "No voice notes found in that chat" + (" from them." if sender == "them" else ".")
        budget, lines = MAX_SECONDS, []
        for note in notes:
            if budget <= 1:
                lines.append(f"[{note.time.astimezone():%a %H:%M}] {note.sender}: (skipped: 5-minute limit per request)")
                continue
            cached = bool(note.text)
            try:
                await self.transcript(note, budget)
            except Exception as exc:
                lines.append(f"[{note.time.astimezone():%a %H:%M}] {note.sender}: (couldn't get this note: {exc})")
                continue
            cut = " (only the first part)" if note.seconds > budget else ""
            if not cached:  # cached notes cost nothing, so they don't use up the budget
                budget -= min(note.seconds, budget)
            lines.append(f"[{note.time.astimezone():%a %H:%M}] {note.sender} ({fmt_seconds(note.seconds)}){cut}: «{note.text}»")
        return "\n".join(lines)
