"""'What's new?' without AI: recent incoming WhatsApp messages from the local cache, unread
Gmail from one search call. Remembers what was already shown in data/inbox_state.json."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ----------------------------------------------------------------------------
# Inbox items
#   One row of the 'what's new' popup. No AI anywhere in this file.
# ----------------------------------------------------------------------------

@dataclass
class InboxItem:
    channel: str  # "whatsapp" | "gmail"
    sender_name: str
    sender: str  # phone number or email address
    text: str  # message text, or the email subject
    time: str
    chat_jid: str = ""  # WhatsApp chat to reply in (the group for group messages)
    group: str = ""  # group name, empty for direct chats
    more: int = 0  # further new messages in the same chat
    email_id: str = ""
    extra: dict = field(default_factory=dict)

    def reply_prompt(self) -> str:
        """What the Reply button sends to the AI: the context and a request to draft a reply right
        away (no questions). The draft only appears on screen; sending still needs a confirmation."""
        if self.channel == "whatsapp":
            where = f" in the WhatsApp group \"{self.group}\"" if self.group else " on WhatsApp"
            who = f"{self.sender_name} (+{self.sender})" if self.sender_name else f"+{self.sender}"
            return (f"Draft a reply{where} to {who}. Chat JID: {self.chat_jid}. "
                    f"Their latest message: \"{self.text}\". Don't ask me anything: if needed read the recent chat "
                    "with list_messages, look at my past messages with my_past_messages, then call show_draft "
                    f"with recipient {self.chat_jid} and a natural reply in my style.")
        return (f"Draft a reply to the email from {self.sender_name} <{self.sender}> with subject \"{self.text}\" "
                f"(message ID {self.email_id}). Don't ask me anything: read it with read_email, then call "
                f"show_draft (channel gmail, recipient {self.sender}, subject \"Re: {self.text}\") with a suitable reply.")


# ----------------------------------------------------------------------------
# WhatsApp: new messages from the local store
# ----------------------------------------------------------------------------

def _parse_time(value: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(value).replace(" ", "T", 1))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def whatsapp_new(store_dir: Path, since: datetime, limit: int = 15) -> list[InboxItem]:
    """Incoming messages after `since`, one row per chat (latest message + how many more)."""
    msgs_db, wa_db = store_dir / "messages.db", store_dir / "whatsapp.db"
    if not msgs_db.exists():
        return []
    db = sqlite3.connect(f"file:{msgs_db.as_posix()}?mode=ro", uri=True)
    rows = db.execute(
        "SELECT m.chat_jid, m.sender, m.content, m.media_type, m.timestamp, c.name "
        "FROM messages m LEFT JOIN chats c ON c.jid = m.chat_jid "
        "WHERE m.is_from_me = 0 ORDER BY m.timestamp DESC LIMIT 400"
    ).fetchall()
    db.close()
    names = _contact_names(wa_db)

    by_chat: dict[str, InboxItem] = {}
    for chat_jid, sender, content, media, ts, chat_name in rows:
        t = _parse_time(ts)
        if t is None or t <= since or "status@broadcast" in chat_jid:
            continue
        if chat_jid in by_chat:
            by_chat[chat_jid].more += 1
            continue
        is_group = chat_jid.endswith("@g.us")
        number = (sender or chat_jid).split("@")[0].split(":")[0]
        number = names.get(f"lid:{number}", number)  # LID sender -> real phone number when known
        text = (content or "").strip() or (f"[{media}]" if media else "[message]")
        by_chat[chat_jid] = InboxItem(
            channel="whatsapp", sender_name=names.get(number, "" if is_group else _name_or_blank(chat_name)),
            sender=number, text=text, time=t.astimezone().strftime("%H:%M"), chat_jid=chat_jid,
            group=(chat_name or "") if is_group else "",
        )
        if len(by_chat) >= limit:
            break
    return list(by_chat.values())


# ----------------------------------------------------------------------------
# Contact names from the synced address book
# ----------------------------------------------------------------------------

def _name_or_blank(name: str | None) -> str:
    return "" if not name or re.fullmatch(r"[\d+\s‎‏‪-‮]+", name) else name


def _contact_names(wa_db: Path) -> dict[str, str]:
    """phone -> best name from the synced address book; 'lid:<id>' -> phone."""
    out: dict[str, str] = {}
    if not wa_db.exists():
        return out
    db = sqlite3.connect(f"file:{wa_db.as_posix()}?mode=ro", uri=True)
    try:
        for jid, first, full, push in db.execute(
                "SELECT their_jid, first_name, full_name, push_name FROM whatsmeow_contacts"):
            name = full or first or push
            if name:
                out[str(jid).split("@")[0]] = name
        for lid, pn in db.execute("SELECT lid, pn FROM whatsmeow_lid_map"):
            out[f"lid:{lid}"] = str(pn)
    except sqlite3.Error:
        pass
    finally:
        db.close()
    return out


# ----------------------------------------------------------------------------
# Gmail: parsing search results
# ----------------------------------------------------------------------------

_EMAIL_BLOCK = re.compile(r"ID:\s*(?P<id>\S+)\s*\nSubject:\s*(?P<subject>.*)\nFrom:\s*(?P<from>.*)\nDate:\s*(?P<date>.*)")


def parse_gmail_search(raw: str) -> list[InboxItem]:
    items = []
    for m in _EMAIL_BLOCK.finditer(raw or ""):
        sender = m["from"].strip()
        addr = re.search(r"<([^>]+)>", sender)
        name = sender.split("<")[0].strip().strip('"') if addr else ""
        t = None
        try:
            from email.utils import parsedate_to_datetime

            t = parsedate_to_datetime(m["date"].strip())
        except (TypeError, ValueError):
            pass
        items.append(InboxItem(channel="gmail", sender_name=name, sender=addr[1] if addr else sender,
                               text=m["subject"].strip(), time=t.astimezone().strftime("%H:%M") if t else "",
                               email_id=m["id"]))
    return items


# ----------------------------------------------------------------------------
# What was already shown
# ----------------------------------------------------------------------------

class InboxState:
    """When we last showed WhatsApp messages, and which emails were already shown."""

    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        last = _parse_time(data.get("whatsapp_since", ""))
        self.whatsapp_since = last or datetime.now(timezone.utc) - timedelta(hours=24)
        self.seen_emails: set[str] = set(data.get("seen_emails", []))

    def mark_seen(self, items: list[InboxItem]) -> None:
        self.whatsapp_since = datetime.now(timezone.utc)
        self.seen_emails |= {i.email_id for i in items if i.email_id}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"whatsapp_since": self.whatsapp_since.isoformat(),
                                         "seen_emails": sorted(self.seen_emails)[-500:]}), encoding="utf-8")
