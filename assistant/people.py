"""Person and group summaries: "Who is Ali?", "What's going on with Sara?", "catch me up on uni-friends".

Everything is gathered in code, with no AI: the chat (found by any spelling), statistics from the
local WhatsApp cache, the last week of conversation, the facts the owner saved about them, and
upcoming calendar events with their name. The model then writes the 4–6 line summary from that
in the same turn (no extra request), and the app shows a person card with quick actions.
Email is deliberately left out (the owner's choice).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .contacts import ChatStats, normalize
from .llm import ToolSpec
from .safety import wrap_untrusted


# ----------------------------------------------------------------------------
# The tool the model calls
# ----------------------------------------------------------------------------

PERSON_TOOL = ToolSpec(
    name="person_summary",
    description="Everything about one WhatsApp person or group: how much you talk, who spoke last, what's "
                "waiting for your reply, the last week of chat, saved facts and upcoming events with them. "
                "Use for 'who is X', 'what's going on with X', 'catch me up on <group>'.",
    parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
)

INSTRUCTIONS = ("Summarize this for the user in 4-6 short lines, in the language of their question: what you two "
                "usually talk about (or what the group is about), anything waiting for their reply, saved facts "
                "that matter, and anything coming up. Use only the data above. A card with the numbers is shown "
                "next to your answer, so don't repeat every number.")


# ----------------------------------------------------------------------------
# The card shown in the panel (TurnResult.people)
#   Holds what the card needs: the chat, its statistics and how many facts are saved.
# ----------------------------------------------------------------------------

@dataclass
class PersonProposal:
    name: str
    jid: str
    is_group: bool
    number: str = ""
    stats: ChatStats | None = None
    facts: int = 0
    id: str = field(default="")

    def __post_init__(self) -> None:
        self.id = self.id or f"person-{self.jid}"


# ----------------------------------------------------------------------------
# Upcoming events with this person
#   One calendar call (no AI) for the next two weeks; keep the lines that mention their name.
# ----------------------------------------------------------------------------

async def _events_with(tools, name: str, available: bool) -> list[str]:
    if not available:
        return []
    words = [w for w in normalize(name).split() if len(w) >= 3]
    if not words:
        return []
    try:
        raw = await tools.call_tool("calendar__list_events", {"start": "today", "days": 14})
    except Exception:
        return []
    return [line.strip() for line in raw.splitlines()
            if line.strip() and any(w in normalize(line) for w in words)][:5]


# ----------------------------------------------------------------------------
# Build the profile
#   Returns (text for the model, card or None). The text is wrapped as untrusted data, because
#   chat messages are written by other people.
# ----------------------------------------------------------------------------

async def build_profile(directory, facts, tools, name: str, calendar: bool = False,
                        ref=None) -> tuple[str, PersonProposal | None]:
    """ref: the chat when it's already known (a shortcut like "mom"); otherwise found by name."""
    name = name.strip()
    others: list[str] = []
    if ref is None:
        try:
            matches = await asyncio.to_thread(directory.find, name, 3)
        except Exception as exc:
            return f"Couldn't search chats ({type(exc).__name__}).", None
        if not matches:
            return f"No chat matches '{name}'. Try another spelling or part of the number.", None
        ref = matches[0]
        others = [m.label for m in matches[1:] if m.score >= ref.score - 0.05]  # close runners-up, if any

    stats: ChatStats = await asyncio.to_thread(directory.stats, ref.jid)
    convo = await asyncio.to_thread(directory.recent_conversation, ref.jid, hours=24 * 7, max_msgs=60,
                                    max_chars=4000)
    saved = facts.about(ref.jid, ref.label) if facts is not None else []
    events = await _events_with(tools, ref.label, calendar)

    # ------------------------------------------------------------------------
    # Assemble what the model reads
    # ------------------------------------------------------------------------
    kind = "group" if ref.is_group else f"person +{ref.number}"
    parts = [f"{ref.label} ({kind}) | chat JID {ref.jid}"]
    if others:
        parts[0] += " | other close matches: " + ", ".join(others) + " (say which one you used)"
    parts += stats.lines()
    if stats.waiting:
        label = "Questions in the group since the user last wrote" if ref.is_group else "Their messages the user hasn't answered"
        parts.append(f"{label}:\n" + "\n".join(stats.waiting))
    elif stats.sent or stats.received:
        parts.append("Nothing is waiting for the user's reply.")
    parts.append("Last 7 days of the chat, oldest first:\n" + (convo or "(no messages this week)"))
    if events:
        parts.append("Upcoming calendar events with their name:\n" + "\n".join(events))
    text = wrap_untrusted("\n".join(parts), source="person_summary")
    if saved:  # the owner's own notes: not untrusted
        text += "\nFacts the user saved about them:\n" + "\n".join(f"- {f.text}" for f in saved)
    card = PersonProposal(ref.label, ref.jid, ref.is_group, ref.number, stats, len(saved))
    return text + "\n\n" + INSTRUCTIONS, card
