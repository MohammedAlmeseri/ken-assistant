"""Example 5: shortcuts ("mom" -> her chat) and saved facts ("remember…").

Shortcuts: your own words for ONE person, group or email. They're resolved in code before the
model sees your message, so "send a message to my mom" can't go to the wrong chat.

Facts: things you ask Ken to remember. They live in the vector store; the ones related to a
request are added to the prompt. Birthdays become yearly reminders.

Run:  python examples/05_shortcuts_and_facts.py
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path

from _common import show, title

from assistant.contacts import MockDirectory
from assistant.facts import Facts, has_remember_intent
from assistant.memory import HashEmbedder, VectorStore
from assistant.reminders import Reminders
from assistant.shortcuts import Shortcut, Shortcuts

TMP = Path(tempfile.mkdtemp())
MOM = "201119876543@s.whatsapp.net"

# ----------------------------------------------------------------------------
# 1. Shortcuts
# ----------------------------------------------------------------------------

shortcuts = Shortcuts(TMP / "shortcuts.json")
shortcuts.save(Shortcut(["mom", "mama", "ماما", "أمي"], "whatsapp", MOM, "ماما", "201119876543"))
shortcuts.save(Shortcut(["boss"], "gmail", "boss@company.example", "boss@company.example"))

title("Shortcut words found in a message")
for message in ["Send a message to my mom that I'm on my way", "ابعت لماما إني جاي",  # "to mama", glued
                "Email my boss the report", "What's the momentum of this project?"]:
    found = [f"{w!r} → {s.label}" for w, s in shortcuts.matches(message)]
    show(message[:40], ", ".join(found) or "none (whole words only)")

title("The line added to the prompt")
print(shortcuts.hint("Send a message to my mom that I'm on my way"))

title("Starter suggestions from contacts saved as family words")
for proposal in shortcuts.suggestions(MockDirectory().contacts(), include_offered=True):
    show(proposal.shortcut.label, ", ".join(proposal.shortcut.words[:5]) + " …")


# ----------------------------------------------------------------------------
# 2. Facts
# ----------------------------------------------------------------------------

async def facts_demo() -> None:
    now = datetime(2026, 4, 1, 9, 0).astimezone()
    reminders = Reminders(TMP / "reminders.json", clock=lambda: now)
    facts = Facts(VectorStore(TMP / "memory.db", HashEmbedder()), MockDirectory(), reminders)

    title("Only the owner's own words can save a fact")
    for text in ["Remember that my brother loves football", "What did my brother say?"]:
        show(text, "saves" if has_remember_intent(text) else "refused (no 'remember')")

    await facts.add("Brother loves football", "Brother")
    await facts.add("Brother's birthday is 3 May", "Brother", yearly_date="05-03")
    await facts.add("I take my coffee black", "me")

    title("Facts recalled for a request")
    for f in await facts.relevant("What should I get my Brother for his birthday?"):
        show("•", f.text)

    title("The yearly reminder it created")
    print("  " + reminders.describe())


asyncio.run(facts_demo())
