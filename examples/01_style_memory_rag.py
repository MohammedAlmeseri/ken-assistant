"""Example 1: the style memory, the RAG that makes drafts sound like you.

Ken keeps "their message -> your reply" pairs in a vector store. The vector is made from THEIR
message, so when a new message arrives, the closest past messages from that person come back
together with how you answered them. Those answers become style examples in the draft prompt.

Run:  python examples/01_style_memory_rag.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from _common import show, title

from assistant.memory import WHATSAPP_PAIR, HashEmbedder, VectorStore

SARA = "201005550000@s.whatsapp.net"
ALI = "201006660000@s.whatsapp.net"

# ----------------------------------------------------------------------------
# 1. Made-up history: (chat, their message, your reply)
#    In the app these come from your real WhatsApp history (Settings → Learn from my history)
#    and from every message you send through Ken.
# ----------------------------------------------------------------------------

HISTORY = [
    (SARA, "are you coming to the lecture tomorrow?", "yes inshallah, see you there"),
    (SARA, "can you send me the slides from today", "sure sending now"),
    (SARA, "did you finish the assignment?", "almost, tonight"),
    (SARA, "want to grab coffee after class", "yes! 2pm?"),
    (ALI, "bro are you free for football friday", "yalla count me in"),
    (ALI, "did you watch the match last night", "what a game 🔥"),
    (ALI, "can you send me the slides from today", "check the group, I posted them"),
]


async def main() -> None:
    store = VectorStore(Path(tempfile.mkdtemp()) / "memory.db", HashEmbedder())

    # ------------------------------------------------------------------------
    # 2. Fill the store. text = YOUR reply, embed_text = THEIR message, meta.to = the chat.
    # ------------------------------------------------------------------------
    added = await store.add_many(WHATSAPP_PAIR, [(mine, {"to": [jid], "them": theirs}, theirs)
                                                 for jid, theirs, mine in HISTORY])
    title("The vector store")
    show("pairs stored", added)
    show("embedder", store.embedder.name + " (OpenAI text-embedding-3-small in the app)")

    # ------------------------------------------------------------------------
    # 3. A new message arrives from Sara. Retrieve the 3 closest past exchanges IN HER CHAT.
    #    The `to` filter matters: Ali's answer to the same question ("check the group") is a
    #    different relationship and a different style.
    # ------------------------------------------------------------------------
    incoming = "can you send me today's slides?"
    title(f'Sara writes: "{incoming}"')
    for hit in await store.search(incoming, k=3, kind=WHATSAPP_PAIR, to=SARA):
        print(f"  {hit['score']:.2f}  them: {hit['meta']['them']!r:45}  you: {hit['text']!r}")

    title("Same question, without the per-chat filter (what Ken does NOT do first)")
    for hit in await store.search(incoming, k=3, kind=WHATSAPP_PAIR):
        chat = "Sara" if hit["meta"]["to"] == [SARA] else "Ali"
        print(f"  {hit['score']:.2f}  [{chat}] you: {hit['text']!r}")

    # ------------------------------------------------------------------------
    # 4. What the model receives (simplified). The real prompt also has the chat's last 24 h,
    #    a length target from your own messages and how often you split replies into two lines.
    # ------------------------------------------------------------------------
    hits = await store.search(incoming, k=3, kind=WHATSAPP_PAIR, to=SARA)
    title("The style block added to the draft prompt")
    print("  How the user answered similar messages in this chat. Answer the new message the same way:")
    for h in hits:
        print(f"  ---\n  Them: {h['meta']['them']}\n  User: {h['text']}")


if __name__ == "__main__":
    asyncio.run(main())
