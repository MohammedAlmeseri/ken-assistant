"""Tool allowlist (safety rule 4): sort MCP tools into read / send / hidden.

Tool names differ between servers and versions, so by default we sort them by
keywords. Exact lists in settings (read_tools / send_tools) override that.
"""

from __future__ import annotations

import re
from typing import Any

from .config import Settings
from .llm import ToolSpec

READ, SEND, HIDDEN = "read", "send", "hidden"

# ----------------------------------------------------------------------------
# The allowlist
#   Tools are sorted by the words in their names. Hidden wins over send and read: anything that
#   deletes, labels, drafts, creates, or touches files and media is never shown to the model.
# ----------------------------------------------------------------------------

# Checked first: anything matching is hidden even if it also says "send".
_DENY = ("delete", "trash", "remove", "archive", "label", "draft", "modify", "batch",
         "filter", "group", "create", "update", "mark", "forward", "download", "block",
         "file", "media", "audio", "image", "video", "attachment",
         # WhatsApp extras (sealjay/mcp-whatsapp exposes 42 tools)
         "set", "edit", "reaction", "poll", "vote", "card", "typing", "presence", "privacy",
         "blocklist", "unblock", "join", "leave", "sync", "pairing", "status", "revoke", "invite")
_SEND = ("send", "reply")
_READ = ("list", "get", "read", "search", "fetch", "query", "find")

RECIPIENT_KEYS = ("to", "cc", "bcc", "recipient", "recipients", "jid", "chat_jid", "phone", "phone_number", "number")
TEXT_KEYS = ("body", "message", "text", "content", "caption")


# ----------------------------------------------------------------------------
# Classifying a tool
# ----------------------------------------------------------------------------

def _words(name: str) -> list[str]:
    words = re.split(r"[_\-\s.]+|(?<=[a-z])(?=[A-Z])", name.lower())
    # Plurals too: "list_email_labels" must hit "label", "list_filters" must hit "filter".
    return words + [w[:-1] for w in words if w.endswith("s") and len(w) > 3]


def classify(name: str, settings: Settings) -> str:
    """name is the prefixed tool name, e.g. 'gmail__send_email'."""
    if name in settings.hidden_tools:
        return HIDDEN
    if settings.read_tools or settings.send_tools:
        if name in settings.send_tools:
            return SEND
        if name in settings.read_tools:
            return READ
        return HIDDEN
    words = _words(name.split("__", 1)[-1])
    if any(w in words for w in _DENY):
        return HIDDEN
    if any(w in words for w in _SEND):
        return SEND
    if any(w in words for w in _READ):
        return READ
    return HIDDEN


# ----------------------------------------------------------------------------
# Compacting tool schemas
#   Every model call carries every tool definition, so descriptions are shortened and noisy
#   parameters (pagination, file paths, HTML) are dropped.
# ----------------------------------------------------------------------------

# Parameters never shown to the model: blocked by the send gate anyway, or just noise.
_DROP_PARAM_WORDS = ("attach", "file", "path", "media", "html", "mime")
_DROP_PARAMS = {"mark_chat_read", "page", "context_before", "context_after",
                "include_last_message", "sort_by",  # server defaults are what we want
                "bcc"}  # a hidden recipient is never needed for a reply
_SCHEMA_NOISE = ("$schema", "additionalProperties")


def _short(text: str, limit: int, cut_parenthetical: bool = False) -> str:
    text = re.sub(r"^\(reads local cache; works while disconnected\)\s*", "", (text or "").strip())
    text = re.split(r"(?<=[A-Za-z0-9`)])\.\s|;\s", text, maxsplit=1)[0].rstrip(".")
    if text.startswith("WhatsApp JID"):
        return "WhatsApp JID"
    if cut_parenthetical:
        text = text.split(" (", 1)[0]
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def compact_spec(spec: ToolSpec) -> ToolSpec:
    """Shrink a tool definition before it goes to the model. Every LLM call carries all tool
    definitions, so the servers' paragraph-long descriptions cost tokens on every request."""
    params = {k: v for k, v in (spec.parameters or {}).items() if k not in _SCHEMA_NOISE}
    props = {}
    for key, prop in (params.get("properties") or {}).items():
        if key in _DROP_PARAMS or any(w in key.lower() for w in _DROP_PARAM_WORDS):
            continue
        prop = dict(prop)
        prop.pop("title", None)  # Python servers repeat the name as a title
        if "description" in prop:
            prop["description"] = _short(prop["description"], 70, cut_parenthetical=True)
        props[key] = prop
    if "properties" in params:
        params["properties"] = props
    if "required" in params:
        params["required"] = [r for r in params["required"] if r in props]
    return ToolSpec(spec.name, _short(spec.description, 130), params)


# ----------------------------------------------------------------------------
# Reading send arguments
#   Channel, recipients and text of a send call, for the gate and the dialog.
# ----------------------------------------------------------------------------

def channel_of(name: str) -> str:
    return name.split("__", 1)[0]


def recipients_of(args: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key in RECIPIENT_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            found.extend(v.strip() for v in value.split(",") if v.strip())
        elif isinstance(value, list):
            found.extend(str(v).strip() for v in value if str(v).strip())
    return found


def text_of(args: dict[str, Any]) -> str:
    for key in TEXT_KEYS:
        if isinstance(args.get(key), str):
            return args[key]
    return ""


# ----------------------------------------------------------------------------
# Status lines
# ----------------------------------------------------------------------------

def status_line(name: str) -> str:
    """Short human status for the UI, never raw JSON."""
    channel = channel_of(name)
    label = {"gmail": "emails", "whatsapp": "WhatsApp"}.get(channel, channel)
    words = _words(name.split("__", 1)[-1])
    if "search" in words or "find" in words:
        return f"Searching {label}…"
    if "contact" in words or "contacts" in words:
        return "Looking up contacts…"
    return f"Reading {label}…"
