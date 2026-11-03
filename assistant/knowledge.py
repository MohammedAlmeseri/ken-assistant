"""Ken's knowledge about himself: docs/PROJECT_BRIEF.md and docs/ARCHITECTURE.md as RAG.

The two files are split into their sections (one chunk per ## / ### heading, long sections cut at
paragraphs), embedded and kept in the style-memory database under their own kind. The
`about_ken` tool finds the sections closest to a question, so Ken can answer "how does the wake
word work?" or "what do you send to OpenAI?" from the docs instead of guessing. The index is
rebuilt automatically when either file changes (one embedding request).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .config import PROJECT_ROOT

# ----------------------------------------------------------------------------
# Ken's own documentation as RAG
#   The two docs are split per heading, embedded once, and searched by the about_ken tool.
# ----------------------------------------------------------------------------

KIND = "ken_docs"
DOCS = [PROJECT_ROOT / "docs" / "PROJECT_BRIEF.md", PROJECT_ROOT / "docs" / "ARCHITECTURE.md"]
MAX_CHUNK = 1400


# ----------------------------------------------------------------------------
# Chunking
#   One chunk per ## / ### section, titled with its heading path; long ones are cut at paragraphs.
# ----------------------------------------------------------------------------

def chunk_markdown(text: str, source: str) -> list[tuple[str, str]]:
    """(title, body) per section: 'ARCHITECTURE.md › 8. Voice in detail', its text. Sections longer
    than MAX_CHUNK are split at blank lines (each part keeps the title)."""
    chunks, title_stack, body = [], [], []

    def flush():
        text_ = "\n".join(body).strip()
        if text_:
            title = " › ".join([source] + [t for t in title_stack if t])
            parts, part = [], ""
            for para in re.split(r"\n\s*\n", text_):
                if part and len(part) + len(para) > MAX_CHUNK:
                    parts.append(part)
                    part = ""
                part = f"{part}\n\n{para}" if part else para
            parts.append(part)
            chunks.extend((title, p.strip()) for p in parts if p.strip())
        body.clear()

    for line in text.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            flush()
            level = len(m.group(1))
            title_stack[:] = (title_stack + [""] * 3)[: level - 1] + [m.group(2).strip()]
            continue
        body.append(line)
    flush()
    return chunks


# ----------------------------------------------------------------------------
# Indexing only when the docs change
#   A hash of both files is stored with the chunks: same hash = nothing to embed, no request.
# ----------------------------------------------------------------------------

def docs_version(paths: list[Path] = DOCS) -> str:
    h = hashlib.sha1()
    for p in paths:
        h.update(p.read_bytes() if p.exists() else b"")
    return h.hexdigest()[:12]


async def ensure_index(memory, paths: list[Path] = DOCS) -> int:
    """(Re)build the index if the docs changed. Returns how many chunks were indexed (0 = up to date)."""
    version = docs_version(paths)
    if memory.first_meta(KIND).get("version") == version:
        return 0
    items = []
    for path in paths:
        if path.exists():
            for title, body in chunk_markdown(path.read_text(encoding="utf-8"), path.name):
                items.append((body, {"title": title, "version": version}, f"{title}\n{body}"))
    memory.delete_kind(KIND)
    return await memory.add_many(KIND, items)


# ----------------------------------------------------------------------------
# Search
# ----------------------------------------------------------------------------

async def lookup(memory, question: str, k: int = 3) -> str:
    hits = await memory.search(question, k=k, kind=KIND)
    if not hits:
        return "Nothing about that in Ken's documentation."
    return "\n\n".join(f"[{h['meta'].get('title', '')}]\n{h['text']}" for h in hits)
