"""Example 4: finding a chat by any spelling (no AI).

People type names every which way: "youssef", "yousef", "يوسف", "3abdo" (Franco-Arabic, where 3 = ع).
Ken normalizes Arabic letter variants, transliterates Arabic <-> Latin, and scores matches, with
people you message often winning ties. This runs on the local WhatsApp cache in milliseconds.

Run:  python examples/04_chat_finder.py
"""

from __future__ import annotations

import math

from _common import show, title

from assistant.contacts import ChatRef, _similarity, latin, normalize

# ----------------------------------------------------------------------------
# 1. Normalization and transliteration
# ----------------------------------------------------------------------------

title("Normalizing and transliterating")
for text in ["أحمد", "إسلام", "فاطمة", "3abdo", "el7agga", "Youssef!!"]:
    show(text, f"normalize → {normalize(text)!r:14}  latin → {latin(text)!r}")

# ----------------------------------------------------------------------------
# 2. A tiny made-up address book, and how typed names score against it
#    (ChatDirectory.find does this over every chat and saved contact)
# ----------------------------------------------------------------------------

BOOK = [ChatRef("1@s.whatsapp.net", "Youssef", "1", False, sent_recently=40),
        ChatRef("2@s.whatsapp.net", "يوسف علي", "2", False, sent_recently=1),
        ChatRef("3@s.whatsapp.net", "عبدو", "3", False, sent_recently=12),
        ChatRef("4@s.whatsapp.net", "ماما", "4", False, sent_recently=25),
        ChatRef("5@g.us", "Uni friends", "", True, sent_recently=8)]

title("Typed name → best matches (score = name similarity + small bonus for active chats)")
for typed in ["yousef", "يوسف", "3abdo", "mama", "uni"]:
    # the same formula as ChatDirectory.find: similarity, plus up to 0.15 for chats you write in a lot
    scored = sorted(((_similarity(typed, r.name) + min(0.15, math.log1p(r.sent_recently) * 0.04), r.name)
                     for r in BOOK), reverse=True)[:2]
    show(typed, "   ".join(f"{name} ({s:.2f})" for s, name in scored))
