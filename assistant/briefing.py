"""The ☀ briefing: last 24 h of email and WhatsApp plus today's and tomorrow's calendar, gathered
in code (no tool round-trips), then one model call that summarizes and flags what needs a reply."""

from __future__ import annotations

from .safety import wrap_untrusted

# ----------------------------------------------------------------------------
# The briefing prompt
#   Instructions for one model call: a speakable opening line, calendar, WhatsApp, email and a
#   'Needs your reply' list.
# ----------------------------------------------------------------------------

LANGUAGE = {"egyptian_friendly": "Egyptian Arabic (عامية مصرية)", "saudi_professional": "polite Saudi Arabic",
            "us_professional": "English"}

INSTRUCTIONS = """Give me my briefing for the last 24 hours, in {language}. Use only the data below.
Begin with ONE short opening line, under 20 words, that can be read aloud on its own: {opening}
1. Calendar: today's and tomorrow's events with times (skip this part if there are none).
2. WhatsApp: one short line per chat that matters (who + what).
3. Email: one short line per email that matters. Skip newsletters, promotions and automatic notifications.
Then a part called "Needs your reply": messages or emails where someone is waiting for an answer from me (a
question, a request, an invitation, a deadline) that I haven't answered yet. In WhatsApp, a "You:" line after
their message means I already answered. Most important first; for each: who, what they want, and why it
matters. If nothing needs a reply, say so in one line.
Short lines, no tables. Don't draft replies; I'll ask for those."""


OPENING = {"butler": 'e.g. "Good morning, Sir. Two messages need your reply, and class is at 2."',
           "plain": 'e.g. "Two messages need your reply, and class is at 2."'}


# ----------------------------------------------------------------------------
# Building it
#   All data is gathered in code (no tool round-trips) and wrapped as untrusted, then sent once.
# ----------------------------------------------------------------------------

def build_prompt(tone: str, calendar: str, whatsapp: list[tuple[str, str]], emails: list[str],
                 persona: str = "plain", today: list[str] = ()) -> str:
    """whatsapp: (chat label, its last 24 h of conversation); emails: one line each.
    today: saved yearly facts that fall on today (birthdays), mentioned near the top."""
    parts = [INSTRUCTIONS.format(language=LANGUAGE.get(tone, "English"), opening=OPENING.get(persona, OPENING["plain"]))]
    if today:  # the owner's own notes (facts.py), not untrusted
        parts.append("TODAY (from the user's saved notes; mention these right after the opening line):\n"
                     + "\n".join(f"- {t}" for t in today))
    parts.append("CALENDAR (today and tomorrow):\n" + wrap_untrusted(calendar or "No events.", source="calendar"))
    chats = "\n\n".join(f"Chat: {label}\n{convo}" for label, convo in whatsapp) or "No new WhatsApp messages."
    parts.append("WHATSAPP (chats with new messages):\n" + wrap_untrusted(chats, source="whatsapp"))
    parts.append("EMAIL (received):\n" + wrap_untrusted("\n".join(emails) or "No new email.", source="gmail"))
    return "\n\n".join(parts)
