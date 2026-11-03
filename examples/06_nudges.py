"""Example 6: proactive nudges, decided by rules first and a tiny AI check second.

Every 10 minutes the app asks the NudgeEngine what (if anything) to say. Rules in code pick
candidates (someone waited 3 h for your reply; your question went unanswered for 2 days; an event
starts in 20 minutes), limits filter them (quiet hours, daily cap, never twice, focus time), and
only then one small model call checks "does this really need a reply?". Here that check is a stub.

Run:  python examples/06_nudges.py
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from _common import show, title

from assistant.config import Settings
from assistant.contacts import ChatRef, ChatStats
from assistant.nudges import NudgeEngine, is_acknowledgement, parse_events

NOW = datetime(2026, 10, 31, 14, 0).astimezone()


# ----------------------------------------------------------------------------
# 1. A fake chat directory: three people, three situations
# ----------------------------------------------------------------------------

class Chats:
    refs = [ChatRef("1@s.whatsapp.net", "Youssef", "1", False, 30),   # close: asked a question 4 h ago
            ChatRef("2@s.whatsapp.net", "Ali", "2", False, 2),        # said "ok 👍" yesterday
            ChatRef("3@s.whatsapp.net", "Sara", "3", False, 20)]      # you asked her something 3 days ago

    def frequent(self, limit=6):
        return [self.refs[0], self.refs[2]]

    def active(self, days=3):
        return self.refs

    def stats(self, jid, days=30):
        if jid.startswith("1"):
            return ChatStats(jid, last_time=NOW - timedelta(hours=4), waiting_texts=["are you coming to class tomorrow?"],
                             waiting_since=NOW - timedelta(hours=4))
        if jid.startswith("2"):
            return ChatStats(jid, last_time=NOW - timedelta(hours=26), waiting_texts=["ok 👍"],
                             waiting_since=NOW - timedelta(hours=26))
        return ChatStats(jid, last_time=NOW - timedelta(days=3), last_from_me=True, my_last_text="did you send the form?")


# ----------------------------------------------------------------------------
# 2. The AI check, stubbed: "does this need a reply?" (one tiny request in the app)
# ----------------------------------------------------------------------------

async def judge(nudge) -> bool:
    return True


async def main() -> None:
    title("Acknowledgements never need a reply")
    for text in ["ok 👍", "تمام", "😂😂", "are you coming tomorrow?"]:
        show(text, "acknowledgement" if is_acknowledgement(text) else "may need a reply")

    engine = NudgeEngine(Chats(), Settings(), Path(tempfile.mkdtemp()) / "nudges.json", clock=lambda: NOW)
    title("Rule candidates (before limits and the AI check)")
    for n in engine.candidates(NOW):
        show(n.kind, n.title)

    events = parse_events("Sat 31 Oct 14:15-15:30 · Lecture · @ Hall B", NOW)
    title("What the 14:00 check shows")
    for n in await engine.check(judge, events):
        show(n.kind, f"{n.title}  →  says: {n.spoken!r}" if n.kind == "event" else n.title)

    # At most ONE message nudge per check, so they never pile up; what was shown never repeats.
    for minute in (10, 20):
        title(f"{minute} minutes later")
        shown = await engine.check(judge, events)
        for n in shown:
            show(n.kind, n.title)
        if not shown:
            print("  (nothing new)")


if __name__ == "__main__":
    asyncio.run(main())
