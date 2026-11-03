"""Example 2: Ken answers questions about himself from his own documentation (RAG).

docs/PROJECT_BRIEF.md and docs/ARCHITECTURE.md are split into one chunk per heading, embedded
once, and stored in the same vector database as the style memory (kind "ken_docs"). The
about_ken tool returns the closest sections, and the model answers from them instead of guessing.

Run:  python examples/02_docs_rag.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from _common import show, title

from assistant.knowledge import DOCS, KIND, chunk_markdown, ensure_index, lookup
from assistant.memory import HashEmbedder, VectorStore

QUESTIONS = [
    "How does the wake word work?",
    "Is anything sent without my confirmation?",
    "How do shortcuts like mom work?",
    "What does Ken cost to run?",
]


async def main() -> None:
    # ------------------------------------------------------------------------
    # 1. Chunking: one chunk per section, titled with its heading path
    # ------------------------------------------------------------------------
    title("Chunking the docs")
    for path in DOCS:
        chunks = chunk_markdown(path.read_text(encoding="utf-8"), path.name)
        show(path.name, f"{len(chunks)} chunks, e.g. {chunks[3][0]!r}")

    # ------------------------------------------------------------------------
    # 2. Indexing, only when the files changed (a hash of both files is stored)
    # ------------------------------------------------------------------------
    store = VectorStore(Path(tempfile.mkdtemp()) / "memory.db", HashEmbedder())
    first = await ensure_index(store)
    again = await ensure_index(store)
    title("Indexing")
    show("first run embedded", f"{first} chunks")
    show("second run embedded", f"{again} chunks (docs unchanged, nothing to do)")
    show("stored as kind", f"{KIND!r}: {store.count(KIND)} items")

    # ------------------------------------------------------------------------
    # 3. Retrieval: what the about_ken tool hands the model
    # ------------------------------------------------------------------------
    for q in QUESTIONS:
        title(f"Q: {q}")
        answer = await lookup(store, q, k=2)
        for line in answer.splitlines():
            if line.startswith("["):
                print("  →", line)


if __name__ == "__main__":
    asyncio.run(main())
