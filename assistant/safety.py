"""Safety rules enforced in code, not only in the prompt (docs/PROJECT_BRIEF.md, section 5)."""

from __future__ import annotations

import html
import json
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# Untrusted content (rule 3)
#   Text from messages, emails, events and web results is wrapped in markers the model is told
#   never to obey. Markers inside the text are neutralised, so it can't close the wrapper early.
# ----------------------------------------------------------------------------

UNTRUSTED_OPEN = "<<<UNTRUSTED_DATA source={source}>>>"
UNTRUSTED_CLOSE = "<<<END_UNTRUSTED_DATA>>>"


def wrap_untrusted(text: str, source: str) -> str:
    # Stop the content from faking our closing marker.
    text = text.replace("<<<", "‹‹‹").replace(">>>", "›››")
    return f"{UNTRUSTED_OPEN.format(source=source)}\n{text}\n{UNTRUSTED_CLOSE}"


# ----------------------------------------------------------------------------
# Trimming tool output
#   HTML, quoted reply chains and long lists are cut, so one big email can't flood the context.
# ----------------------------------------------------------------------------

# Real tags only: "<sara@example.com>" in a From header is NOT a tag and must survive.
_TAG = re.compile(r"<(script|style)\b[^>]*>.*?</\1>|<!--.*?-->|</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]*)?/?>", re.S | re.I)
_LOOKS_HTML = re.compile(r"</?(p|div|br|span|table|tr|td|html|body|a|b|i|strong|em|img|ul|li|h\d)\b[^@<>]*>", re.I)
# Start of quoted reply history. A bare "From:" line is a normal header, so it's only
# treated as a quote after an Outlook-style separator line.
_QUOTE_START = re.compile(
    r"^(On .{5,200}wrote:|-----\s*Original Message\s*-----|_{10,}\s*\n\s*From: .+|في .{5,200}كتب:)\s*$", re.M
)


def clean_text(text: str, max_chars: int) -> str:
    if _LOOKS_HTML.search(text):
        text = html.unescape(_TAG.sub(" ", text))
    match = _QUOTE_START.search(text)
    if match and match.start() > 0:
        text = text[: match.start()]
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + " …[truncated]"
    return text


def _trim_json(value: Any, max_item_chars: int, max_items: int) -> Any:
    if isinstance(value, list):
        items = [_trim_json(v, max_item_chars, max_items) for v in value[:max_items]]
        if len(value) > max_items:
            items.append(f"…{len(value) - max_items} more not shown")
        return items
    if isinstance(value, dict):
        return {k: _trim_json(v, max_item_chars, max_items) for k, v in value.items()}
    if isinstance(value, str):
        return clean_text(value, max_item_chars)
    return value


def trim_tool_output(text: str, max_item_chars: int, max_items: int, max_total: int) -> str:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        out = clean_text(text, max_item_chars * max_items)
    else:
        out = json.dumps(_trim_json(data, max_item_chars, max_items), ensure_ascii=False)
    return out if len(out) <= max_total else out[:max_total] + " …[truncated]"


# ----------------------------------------------------------------------------
# Explicit intent (rule 2)
#   A send is allowed only when the owner's latest message asks for it, in English or Arabic.
# ----------------------------------------------------------------------------

# Deliberately narrow: "draft a reply" must NOT count as a request to send.
_SEND_INTENT = re.compile(r"\bsend\b|\bgo ahead\b|ارسل|أرسل|إرسل|ابعت|ابعث|إبعث|بعت", re.I)


def has_send_intent(user_text: str) -> bool:
    return bool(_SEND_INTENT.search(user_text or ""))


# ----------------------------------------------------------------------------
# Recipients (rule 5)
#   A send may only go to an address the owner typed or one that appeared in what Ken read.
# ----------------------------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_JID = re.compile(r"\b\d{6,}(?:-\d+)?@(?:s\.whatsapp\.net|g\.us|c\.us|lid)\b")
_PHONE = re.compile(r"\+?\d[\d\s\-()]{6,}\d")


def normalize_recipient(value: str) -> str:
    value = value.strip().lower()
    if "<" in value and ">" in value:  # "Name <a@b.com>"
        value = value[value.index("<") + 1 : value.index(">")]
    if value.endswith("@s.whatsapp.net") or value.endswith("@c.us"):
        value = value.split("@")[0]
    if re.fullmatch(r"\+?[\d\s\-()]+", value):
        value = re.sub(r"\D", "", value)
    return value


class RecipientGuard:
    """Tracks addresses that appeared in what the assistant read or the owner typed.

    A send is allowed only to recipients in that set. This approximates
    "part of the conversation being answered, or typed by the owner".
    """

    def __init__(self) -> None:
        self.known: set[str] = set()

    def observe(self, text: str) -> None:
        for pattern in (_EMAIL, _JID, _PHONE):
            for match in pattern.findall(text or ""):
                self.known.add(normalize_recipient(match))

    def unknown(self, recipients: list[str]) -> list[str]:
        return [r for r in recipients if normalize_recipient(r) not in self.known]


# ----------------------------------------------------------------------------
# Rate limit (rule 8)
#   At most 3 WhatsApp and 5 email sends a minute.
# ----------------------------------------------------------------------------


class RateLimiter:
    def __init__(self, limits_per_minute: dict[str, int]):
        self.limits = limits_per_minute
        self.events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, channel: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        limit = self.limits.get(channel)
        if limit is None:
            return True
        window = self.events[channel]
        while window and now - window[0] > 60:
            window.popleft()
        return len(window) < limit

    def record(self, channel: str, now: float | None = None) -> None:
        self.events[channel].append(time.monotonic() if now is None else now)


# ----------------------------------------------------------------------------
# Audit log (rule 7)
#   Every send and calendar add, one JSON line each.
# ----------------------------------------------------------------------------


@dataclass
class AuditLog:
    path: Path

    def write(self, *, channel: str, tool: str, recipients: list[str], text: str, arguments: dict, result: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "channel": channel,
            "tool": tool,
            "recipients": recipients,
            "text": text,
            "arguments": arguments,
            "result": result[:500],
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
