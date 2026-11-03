"""Find WhatsApp chats by approximate name, and list the chats you message most.

Reads the WhatsApp server's local cache (read-only), no AI. Matching forgives typos,
Arabic spelling variants (أ/إ/ا, ة/ه, ى/ي, diacritics) and Arabic <-> Latin spelling
("ahmed" finds "أحمد"), so you don't have to type names or numbers exactly.
"""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

from .inbox import _contact_names, _name_or_blank, _parse_time

# ----------------------------------------------------------------------------
# Arabic and Latin spelling
#   normalize() folds Arabic letter variants, diacritics and punctuation; latin() transliterates
#   Arabic and Franco-Arabic digits, so 'ahmed', 'أحمد' and '2a7med' can match each other.
# ----------------------------------------------------------------------------

_DIACRITICS = re.compile(r"[ً-ْـٰ]")
_ARABIC_TO_LATIN = {
    "ا": "a", "أ": "a", "إ": "e", "آ": "a", "ب": "b", "ت": "t", "ث": "th", "ج": "g", "ح": "h", "خ": "kh",
    "د": "d", "ذ": "z", "ر": "r", "ز": "z", "س": "s", "ش": "sh", "ص": "s", "ض": "d", "ط": "t", "ظ": "z",
    "ع": "a", "غ": "gh", "ف": "f", "ق": "k", "ك": "k", "ل": "l", "م": "m", "ن": "n", "ه": "h", "ة": "a",
    "و": "o", "ي": "i", "ى": "a", "ئ": "e", "ؤ": "o", "ء": "",
}


_ARABIZI = {"2": "a", "3": "a", "5": "kh", "6": "t", "7": "h", "8": "gh", "9": "k"}


def normalize(text: str) -> str:
    t = _DIACRITICS.sub("", (text or "").lower())
    t = re.sub("[أإآ]", "ا", t).replace("ة", "ه").replace("ى", "ي")
    return re.sub(r"[^\w\s]", " ", t).strip()


def latin(text: str, vowels: bool = True) -> str:
    """Rough Arabic -> Latin: 'ماما' -> 'mama', 'أحمد' -> 'ahmd'. vowels=False drops vowels on both
    sides ('mohamed' ~ 'محمد'), a looser match used only as a weaker signal."""
    t = "".join(_ARABIC_TO_LATIN.get(ch, ch) for ch in normalize(text))
    if re.search(r"[a-z]", t):  # Franco-Arabic: "el7agga" -> "elhagga", "3abdo" -> "aabdo"
        for digit, sound in _ARABIZI.items():
            t = t.replace(digit, sound)
    t = t.replace("ph", "f").replace("ou", "o").replace("ee", "i").replace("oo", "o").replace("y", "i")
    t = re.sub(r"(.)\1+", r"\1", t)  # "hagga" ~ "haga": doubled letters vary between spellings
    return t if vowels else re.sub(r"[aeiou]", "", t)


# ----------------------------------------------------------------------------
# Chat records
# ----------------------------------------------------------------------------

@dataclass
class ChatRef:
    jid: str
    name: str
    number: str  # phone digits, empty for groups
    is_group: bool
    sent_recently: int = 0  # how many messages you sent there lately
    last_time: str = ""
    score: float = 0.0

    @property
    def label(self) -> str:
        return self.name or (f"+{self.number}" if self.number else self.jid)


@dataclass
class ChatStats:
    """What ChatDirectory.stats() found in one chat (see there)."""
    jid: str
    is_group: bool = False
    days: int = 30
    sent: int = 0  # your messages in the period
    received: int = 0  # theirs
    last_time: datetime | None = None
    last_from_me: bool = False
    waiting: list[str] = field(default_factory=list)  # their messages since your last one
    waiting_texts: list[str] = field(default_factory=list)  # the same, text only (for the nudge rules)
    waiting_since: datetime | None = None  # when the first of those arrived
    my_last_text: str = ""  # your last message, if you spoke last
    reply_minutes: int | None = None  # your median reply time
    top_speakers: list[str] = field(default_factory=list)  # groups only

    def lines(self) -> list[str]:
        """Plain-English summary lines for the model and the person card."""
        out = [f"Last {self.days} days: you sent {self.sent}, received {self.received}."]
        if self.last_time:
            ago = datetime.now(timezone.utc) - self.last_time
            when = "today" if ago < timedelta(days=1) else f"{ago.days} day{'s' if ago.days > 1 else ''} ago"
            out.append(f"Last message {when}, from {'you' if self.last_from_me else 'them'}.")
        if self.reply_minutes is not None:
            m = self.reply_minutes
            out.append(f"You usually reply in {m} min." if m < 90 else f"You usually reply in about {round(m / 60)} h.")
        if self.top_speakers:
            out.append("Most active: " + ", ".join(self.top_speakers) + ".")
        return out


# ----------------------------------------------------------------------------
# Fuzzy name matching
#   Exact, substring, per-word and cross-script (with and without vowels) similarity, 0..1.
# ----------------------------------------------------------------------------

def _similarity(query: str, name: str) -> float:
    q, n = normalize(query), normalize(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    if q in n:  # "ahmed" in "ahmed sami"
        return 0.92
    best = SequenceMatcher(None, q, n).ratio()
    for word in n.split():  # match against single words of the name ("ahmd" vs "Ahmed Sami")
        best = max(best, SequenceMatcher(None, q, word).ratio() * 0.97)
    # Cross-script only ("ahmed" vs "أحمد"): vowel-less spelling would wrongly equate
    # same-script names like "mama" and "Meme".
    if _is_arabic(q) != _is_arabic(n):
        for vowels, weight in ((True, 0.9), (False, 0.7)):  # with vowels is the trustworthy one
            lq, ln = latin(q, vowels), latin(n, vowels)
            if len(lq) < 2 or not ln:
                continue
            best = max(best, SequenceMatcher(None, lq, ln).ratio() * weight)
            for word in ln.split():
                best = max(best, SequenceMatcher(None, lq, word).ratio() * weight * 0.98)
    return best


def _is_arabic(text: str) -> bool:
    return bool(re.search(r"[؀-ۿ]", text))


# ----------------------------------------------------------------------------
# The chat directory (read-only WhatsApp cache)
#   Reads the WhatsApp server's SQLite files directly: no AI, no network, millisecond lookups.
# ----------------------------------------------------------------------------

class ChatDirectory:
    def __init__(self, store_dir: Path):
        self.store = store_dir
        self._cache: tuple[float, list[ChatRef]] | None = None
        # voicenotes.TranscriptCache, set by the app: voice notes already transcribed (on request)
        # read as "[voice 0:42] «…»" instead of "[audio]" in conversations.
        self.transcripts = None

    def _load(self) -> list[ChatRef]:
        """Every chat plus every saved contact, with names and how often you message them."""
        now = datetime.now(timezone.utc).timestamp()
        if self._cache and now - self._cache[0] < 60:
            return self._cache[1]
        msgs_db = self.store / "messages.db"
        if not msgs_db.exists():
            return []
        names = _contact_names(self.store / "whatsapp.db")
        db = sqlite3.connect(f"file:{msgs_db.as_posix()}?mode=ro", uri=True)
        since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        sent = dict(db.execute(
            "SELECT chat_jid, COUNT(*) FROM messages WHERE is_from_me = 1 AND timestamp >= ? GROUP BY chat_jid", (since,)))
        chats = db.execute("SELECT jid, name, last_message_time FROM chats").fetchall()
        db.close()
        refs: dict[str, ChatRef] = {}
        for jid, chat_name, last in chats:
            if "status@broadcast" in jid or jid.endswith("@lid") or jid.endswith("@newsletter"):
                continue
            is_group = jid.endswith("@g.us")
            number = "" if is_group else jid.split("@")[0]
            name = (chat_name or "") if is_group else names.get(number) or _name_or_blank(chat_name)
            refs[jid] = ChatRef(jid, name, number, is_group, sent.get(jid, 0), str(last or ""))
        for key, name in names.items():  # contacts you've never chatted with can still be found
            if key.startswith("lid:") or not key.isdigit():
                continue
            jid = f"{key}@s.whatsapp.net"
            refs.setdefault(jid, ChatRef(jid, name, key, False))
        result = list(refs.values())
        self._cache = (now, result)
        return result

    # ------------------------------------------------------------------------
    # Finding chats
    #   Scores every chat and contact; people you message often win ties.
    # ------------------------------------------------------------------------

    def find(self, query: str, k: int = 5) -> list[ChatRef]:
        query = query.strip()
        digits = re.sub(r"\D", "", query)
        out = []
        for ref in self._load():
            if len(digits) >= 4 and digits in ref.number:
                score = 1.0
            else:
                score = _similarity(query, ref.name) if ref.name else 0.0
            if score >= 0.55:
                # People you talk to a lot win ties ("Ahmed" -> the Ahmed you message daily).
                ref.score = round(score + min(0.15, math.log1p(ref.sent_recently) * 0.04), 3)
                out.append(ref)
        return sorted(out, key=lambda r: (r.score, r.last_time), reverse=True)[:k]

    def frequent(self, limit: int = 6) -> list[ChatRef]:
        """The chats you sent the most messages to in the last 30 days."""
        refs = [r for r in self._load() if r.sent_recently > 0]
        return sorted(refs, key=lambda r: (r.sent_recently, r.last_time), reverse=True)[:limit]

    def contacts(self) -> list[ChatRef]:
        """Every chat and saved contact (shortcut suggestions look for "Mom", "ماما"… among these)."""
        return list(self._load())

    def active(self, days: float = 3) -> list[ChatRef]:
        """Chats with any message in the last `days` (the nudge rules look only at these)."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        out = []
        for ref in self._load():
            t = _parse_time(ref.last_time) if ref.last_time else None
            if t is not None and t >= cutoff:
                out.append(ref)
        return out

    def _rows(self, jid: str, limit: int) -> list[tuple]:
        db = sqlite3.connect(f"file:{(self.store / 'messages.db').as_posix()}?mode=ro", uri=True)
        rows = db.execute("SELECT content, timestamp, is_from_me FROM messages WHERE chat_jid = ? "
                          "ORDER BY timestamp DESC LIMIT ?", (jid, limit)).fetchall()
        db.close()
        return rows[::-1]  # oldest first

    # ------------------------------------------------------------------------
    # What you're answering
    # ------------------------------------------------------------------------

    def last_incoming(self, recipient: str, within_hours: float = 12) -> str:
        """What they said since your last message in this chat (the message you're answering).
        Empty if you spoke last, or their last message is older than `within_hours`."""
        jid = to_jid(recipient)
        if not jid or not (self.store / "messages.db").exists():
            return ""
        burst: list[str] = []
        for content, ts, from_me in reversed(self._rows(jid, 20)):
            if from_me:
                break
            t = _parse_time(ts)
            if t is None or datetime.now(timezone.utc) - t > timedelta(hours=within_hours):
                break
            if (content or "").strip():
                burst.append(content.strip())
            if len(burst) == 3:
                break
        return "\n".join(reversed(burst))

    def incoming_since(self, recipient: str, since: datetime) -> str:
        """Their newest message in this chat after `since` ("" if none): for "tell me when X replies"."""
        jid = to_jid(recipient)
        if not jid or not (self.store / "messages.db").exists():
            return ""
        for content, ts, from_me in reversed(self._rows(jid, 20)):
            t = _parse_time(ts)
            if t is None or t <= since:
                break
            if not from_me:
                return (content or "").strip() or "[media]"
        return ""

    # ------------------------------------------------------------------------
    # Your writing habits
    #   Median length, line-split rate and whole-reply length: targets that keep drafts like you.
    # ------------------------------------------------------------------------

    def my_typical_length(self, recipient: str, sample: int = 150) -> int:
        """Median length (characters) of your recent messages in this chat, links left out. 0 if unknown."""
        jid = to_jid(recipient)
        if not jid or not (self.store / "messages.db").exists():
            return 0
        recent = [len(c.strip()) for c, _, from_me in self._rows(jid, 600)
                  if from_me and (c or "").strip() and "http" not in c][-sample:]  # your newest messages
        recent.sort()
        return recent[len(recent) // 2] if len(recent) >= 5 else 0

    def _my_turns(self, jid: str, sample: int = 100, gap_minutes: float = 3) -> list[tuple[int, int]]:
        """Your last `sample` turns in a chat as (lines, characters). A turn is your messages sent
        within a few minutes of each other; links are left out."""
        turns: list[list[int]] = []
        last_t = None
        for content, ts, from_me in self._rows(jid, 1500):
            t = _parse_time(ts)
            text = (content or "").strip()
            if not from_me or t is None or not text or "http" in text:
                last_t = None if not from_me else last_t
                continue
            lines = [l for l in text.splitlines() if l.strip()]
            if last_t is not None and t - last_t <= timedelta(minutes=gap_minutes):
                turns[-1][0] += len(lines)
                turns[-1][1] += len(text)
            else:
                turns.append([len(lines), len(text)])
            last_t = t
        return [tuple(x) for x in turns[-sample:]]

    def my_split_rate(self, recipient: str, sample: int = 100, gap_minutes: float = 3) -> float:
        """How often your turn in this chat is several short messages (sent within a few minutes)
        or one message over several lines. 0..1 over your last `sample` turns."""
        jid = to_jid(recipient)
        if not jid or not (self.store / "messages.db").exists():
            return 0.0
        turns = self._my_turns(jid, sample, gap_minutes)
        return round(sum(n > 1 for n, _ in turns) / len(turns), 2) if len(turns) >= 5 else 0.0

    def my_turn_length(self, recipient: str, sample: int = 100) -> int:
        """Median total characters of your whole reply (all quick messages together) in this chat."""
        jid = to_jid(recipient)
        if not jid or not (self.store / "messages.db").exists():
            return 0
        lengths = sorted(c for _, c in self._my_turns(jid, sample))
        return lengths[len(lengths) // 2] if len(lengths) >= 5 else 0

    # ------------------------------------------------------------------------
    # Conversation text for prompts
    #   Timestamped lines, newest kept under the caps; transcribed voice notes appear as text.
    # ------------------------------------------------------------------------

    def _voice_texts(self, ids: list[str]) -> dict[str, str]:
        """Cached transcripts for these voice-note message ids, as '[voice 0:42] «…»'."""
        if self.transcripts is None or not ids:
            return {}
        from .voicenotes import label

        try:
            return {i: label(sec, text) for i, (sec, text) in self.transcripts.get_many(ids).items()}
        except Exception:
            return {}  # a broken cache must never break reading the chat

    def recent_conversation(self, recipient: str, hours: float = 24, max_msgs: int = 40, max_chars: int = 3000,
                            before: datetime | None = None) -> str:
        """The chat's last `hours` before `before` (default now), oldest first, one line per message:
        '[Tue 13:05] Them: …' ('You' for you, the sender's name in groups). Newest lines win the caps."""
        jid = to_jid(recipient)
        if not jid or not (self.store / "messages.db").exists():
            return ""
        end = before or datetime.now(timezone.utc)
        start = end - timedelta(hours=hours)
        if not hasattr(self, "_names"):
            self._names = _contact_names(self.store / "whatsapp.db")
        db = sqlite3.connect(f"file:{(self.store / 'messages.db').as_posix()}?mode=ro", uri=True)
        rows = db.execute("SELECT content, timestamp, is_from_me, sender, media_type, id FROM messages "
                          "WHERE chat_jid = ? ORDER BY timestamp DESC LIMIT ?",
                          (jid, 300 if before is None else 6000)).fetchall()
        db.close()
        voice = self._voice_texts([r[5] for r in rows if r[4] == "audio"])
        lines: list[str] = []
        used = 0
        for content, ts, from_me, sender, media, msg_id in rows:  # newest first
            t = _parse_time(ts)
            if t is None or t >= end:
                continue
            if t < start or len(lines) >= max_msgs:
                break
            text = (content or "").strip() or voice.get(msg_id) or (f"[{media}]" if media else "")
            if not text:
                continue
            if from_me:
                who = "You"
            elif jid.endswith("@g.us"):
                num = str(sender or "").split("@")[0].split(":")[0]
                who = self._names.get(num) or (f"+{num}" if num.isdigit() else "Someone")
            else:
                who = "Them"
            line = f"[{t.astimezone().strftime('%a %H:%M')}] {who}: {text}"
            if used + len(line) > max_chars:
                break
            lines.append(line)
            used += len(line)
        return "\n".join(reversed(lines))

    # ------------------------------------------------------------------------
    # Chat statistics for the person / group summary (no AI, read-only)
    #   One pass over the last `days` of a chat, oldest first:
    #   - counts of messages each way, and when you last talked / who spoke last;
    #   - waiting: their messages since your last message (what you haven't answered);
    #   - reply_minutes: your median time to answer (gaps over a day don't count);
    #   - groups only: the most active members, and questions asked since you last wrote.
    # ------------------------------------------------------------------------

    def stats(self, recipient: str, days: int = 30) -> ChatStats:
        jid = to_jid(recipient)
        out = ChatStats(jid=jid, is_group=jid.endswith("@g.us"), days=days)
        if not jid or not (self.store / "messages.db").exists():
            return out
        if not hasattr(self, "_names"):
            self._names = _contact_names(self.store / "whatsapp.db")
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        db = sqlite3.connect(f"file:{(self.store / 'messages.db').as_posix()}?mode=ro", uri=True)
        rows = db.execute("SELECT content, timestamp, is_from_me, sender, media_type FROM messages "
                          "WHERE chat_jid = ? AND timestamp >= ? ORDER BY timestamp", (jid, since)).fetchall()
        db.close()
        speakers: dict[str, int] = {}
        gaps: list[float] = []
        their_since: datetime | None = None  # first of their messages you haven't answered yet
        for content, ts, from_me, sender, media in rows:
            t = _parse_time(ts)
            if t is None:
                continue
            text = (content or "").strip() or (f"[{media}]" if media else "")
            out.last_time, out.last_from_me = t, bool(from_me)
            if from_me:
                out.sent += 1
                if their_since is not None:
                    gap = (t - their_since).total_seconds() / 60
                    if gap <= 24 * 60:
                        gaps.append(gap)
                their_since = None
                out.waiting, out.waiting_texts = [], []
                out.my_last_text = text
            else:
                out.received += 1
                if their_since is None:
                    their_since = t
                who = "Them"
                if out.is_group:
                    num = str(sender or "").split("@")[0].split(":")[0]
                    who = self._names.get(num) or (f"+{num}" if num.isdigit() else "Someone")
                    speakers[who] = speakers.get(who, 0) + 1
                out.my_last_text = ""
                if text:
                    out.waiting.append(f"[{t.astimezone().strftime('%a %H:%M')}] {who}: {text[:200]}")
                    out.waiting_texts.append(text[:300])
        out.waiting = out.waiting[-8:]
        out.waiting_since = their_since
        if gaps:
            gaps.sort()
            out.reply_minutes = round(gaps[len(gaps) // 2])
        out.top_speakers = [name for name, _ in sorted(speakers.items(), key=lambda kv: kv[1], reverse=True)[:3]]
        if out.is_group:  # in a group only questions count as "waiting for you"
            out.waiting = [w for w in out.waiting if "?" in w or "؟" in w][-5:]
        return out

    # ------------------------------------------------------------------------
    # Reply pairs for the style memory
    #   Their burst of messages followed by your burst = one (them -> you) pair.
    # ------------------------------------------------------------------------

    def history_pairs(self, jid: str, max_pairs: int = 150, gap_hours: float = 12,
                      with_time: bool = False) -> list[tuple]:
        """(their messages, your reply) pairs from past conversations in one chat, newest last.
        A pair is their burst of messages followed by your burst, less than `gap_hours` apart.
        with_time=True adds the time of your first reply (for evaluations that rebuild context)."""
        pairs: list[tuple] = []
        theirs: list[tuple[str, datetime]] = []
        mine: list[str] = []
        mine_at: list[datetime] = []

        def flush() -> None:
            if theirs and mine:
                pair = ("\n".join(c for c, _ in theirs[-3:]), "\n".join(mine))
                pairs.append(pair + (mine_at[0],) if with_time else pair)

        for content, ts, from_me in self._rows(jid, 5000):
            text, t = (content or "").strip(), _parse_time(ts)
            if not text or t is None:
                continue
            if from_me:
                if theirs and t - theirs[-1][1] <= timedelta(hours=gap_hours):
                    mine.append(text)
                    mine_at.append(t)
                else:  # you started a new conversation: keep any finished pair, then start over
                    flush()
                    theirs, mine, mine_at = [], [], []
            else:
                if mine:
                    flush()
                    theirs, mine, mine_at = [], [], []
                theirs.append((text, t))
        flush()
        return pairs[-max_pairs:]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def to_jid(recipient: str) -> str:
    """'201001234567', '+20 100 123 4567' or a JID -> the chat JID."""
    r = str(recipient).strip()
    if "@" in r:
        return r
    digits = re.sub(r"\D", "", r)
    return f"{digits}@s.whatsapp.net" if digits else ""


# ----------------------------------------------------------------------------
# Fake directory for --mock and the tests
# ----------------------------------------------------------------------------

class MockDirectory:
    """Fake directory for --mock, matching mock_tools.py."""

    def __init__(self) -> None:
        self.refs = [ChatRef("201001234567@s.whatsapp.net", "Brother", "201001234567", False, 12),
                     ChatRef("201119876543@s.whatsapp.net", "ماما", "201119876543", False, 8),
                     ChatRef("1203630-1600@g.us", "Uni friends", "", True, 5)]

    def find(self, query: str, k: int = 5) -> list[ChatRef]:
        scored = [(ChatRef(**{**r.__dict__, "score": _similarity(query, r.name)})) for r in self.refs]
        return sorted([r for r in scored if r.score >= 0.55], key=lambda r: r.score, reverse=True)[:k]

    def frequent(self, limit: int = 6) -> list[ChatRef]:
        return self.refs[:limit]

    def active(self, days: float = 3) -> list[ChatRef]:
        return self.refs

    def contacts(self) -> list[ChatRef]:
        return list(self.refs)

    def last_incoming(self, recipient: str, within_hours: float = 12) -> str:
        return "Hey, are you free Friday? Thinking of dinner at 8." if "201001234567" in recipient else ""

    def incoming_since(self, recipient: str, since) -> str:
        return ""

    def history_pairs(self, jid: str, max_pairs: int = 150, gap_hours: float = 12, with_time: bool = False) -> list:
        return []

    def my_typical_length(self, recipient: str, sample: int = 150) -> int:
        return 24 if "201001234567" in recipient else 0

    def my_split_rate(self, recipient: str, sample: int = 100, gap_minutes: float = 3) -> float:
        return 0.4 if "201001234567" in recipient else 0.0

    def my_turn_length(self, recipient: str, sample: int = 100) -> int:
        return 30 if "201001234567" in recipient else 0

    def stats(self, recipient: str, days: int = 30) -> ChatStats:
        if "201001234567" not in recipient:
            return ChatStats(jid=recipient, is_group=recipient.endswith("@g.us"), days=days)
        return ChatStats(jid=recipient, days=days, sent=14, received=19,
                         last_time=datetime.now(timezone.utc) - timedelta(hours=3), last_from_me=False,
                         waiting=["[Tue 09:12] Them: Hey, are you free Friday? Thinking of dinner at 8."],
                         waiting_texts=["Hey, are you free Friday? Thinking of dinner at 8."],
                         waiting_since=datetime.now(timezone.utc) - timedelta(hours=3), reply_minutes=25)

    def recent_conversation(self, recipient: str, **kwargs) -> str:
        if "201001234567" not in recipient:
            return ""
        return "[Mon 21:40] You: Good night!\n[Tue 09:12] Them: Hey, are you free Friday? Thinking of dinner at 8."
