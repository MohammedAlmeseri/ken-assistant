"""Ken's docs as RAG: chunking, indexing only when the files change, and the about_ken tool (offline)."""

from __future__ import annotations

import asyncio

from assistant.agent import Agent
from assistant.config import Settings
from assistant.knowledge import DOCS, KIND, chunk_markdown, ensure_index, lookup
from assistant.llm import LLMReply, ToolCall
from assistant.memory import HashEmbedder, VectorStore
from assistant.mock_tools import MockToolSource
from tests.test_backend import FakeBackend

# ----------------------------------------------------------------------------
# Ken's docs as RAG
# ----------------------------------------------------------------------------

DOC = """# Ken

Intro line.

## 7. Voice

### Wake word
Say Ken and he listens. The microphone is checked locally.

### Speaking
Edge voices: Ryan and Shakir.

## 9. Privacy
Message text goes to OpenAI.
"""


def test_chunks_follow_the_headings():
    chunks = chunk_markdown(DOC, "BRIEF.md")
    titles = [t for t, _ in chunks]
    assert titles == ["BRIEF.md › Ken", "BRIEF.md › Ken › 7. Voice › Wake word", "BRIEF.md › Ken › 7. Voice › Speaking",
                      "BRIEF.md › Ken › 9. Privacy"]
    assert "checked locally" in dict(chunks)["BRIEF.md › Ken › 7. Voice › Wake word"]


def test_long_sections_are_split_at_paragraphs():
    long = "## Big\n\n" + "\n\n".join("word " * 120 for _ in range(4))
    parts = chunk_markdown(long, "X.md")
    assert len(parts) > 1 and all(len(body) <= 1500 for _, body in parts)
    assert {t for t, _ in parts} == {"X.md › Big"}


def test_index_rebuilds_only_when_the_docs_change(tmp_path):
    doc = tmp_path / "BRIEF.md"
    doc.write_text(DOC, encoding="utf-8")
    store = VectorStore(tmp_path / "m.db", HashEmbedder())
    assert asyncio.run(ensure_index(store, [doc])) == 4
    assert asyncio.run(ensure_index(store, [doc])) == 0  # unchanged: nothing re-embedded
    doc.write_text(DOC + "\n## 10. Costs\nEdge voices are free.\n", encoding="utf-8")
    assert asyncio.run(ensure_index(store, [doc])) == 5
    assert store.count(KIND) == 5  # the old version was replaced, not added to
    answer = asyncio.run(lookup(store, "is the microphone checked locally for the wake word?", k=1))
    assert "Wake word" in answer and "checked locally" in answer


def test_the_real_docs_chunk_cleanly():
    for path in DOCS:
        chunks = chunk_markdown(path.read_text(encoding="utf-8"), path.name)
        assert len(chunks) > 10 and all(body for _, body in chunks)


def test_ken_answers_questions_about_himself_from_his_docs(tmp_path):
    doc = tmp_path / "BRIEF.md"
    doc.write_text(DOC, encoding="utf-8")
    store = VectorStore(tmp_path / "m.db", HashEmbedder())
    asyncio.run(ensure_index(store, [doc]))
    agent = Agent(FakeBackend([LLMReply(tool_calls=[ToolCall("a1", "about_ken", {"question": "which voices do you use?"})]),
                               LLMReply(text="Sir, I speak with Ryan and Shakir.")]),
                  MockToolSource(), Settings(audit_log_path=str(tmp_path / "a.jsonl")), None, memory=store)
    asyncio.run(agent.start())
    assert "about_ken" in [t.name for t in agent.specs]
    asyncio.run(agent.run("Which voices do you use?"))
    assert "Ryan and Shakir" in agent.history[-2]["content"]
