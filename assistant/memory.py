"""Local vector memory (simple RAG) in one SQLite file: data/memory.db.

Each item has a `kind` so more sources can be added later. Today the only kind is
"whatsapp_sent": every WhatsApp message you approved and sent. Before each turn the agent
retrieves the most similar ones and shows them to the model as style examples, so drafts
sound like you.

Embeddings: OpenAI text-embedding-3-small when the OpenAI backend is used (message text
already goes to OpenAI), else a local hashing embedder (free, private, keyword-level).
Every row records which embedder made its vector, and search only compares like with like.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import numpy as np

# ----------------------------------------------------------------------------
# Kinds of items
#   One table holds several kinds: sent messages and reply pairs (here), facts (facts.py) and
#   Ken's docs (knowledge.py).
# ----------------------------------------------------------------------------

WHATSAPP_SENT = "whatsapp_sent"  # your sent message; vector = your message
WHATSAPP_PAIR = "whatsapp_pair"  # text = your reply, meta["them"] = what you answered; vector = THEIR message


# ----------------------------------------------------------------------------
# Embedders
#   OpenAI text-embedding-3-small when the brain is OpenAI, a local hashing embedder otherwise and as
#   fallback. Each row records its embedder, and only like is compared with like.
# ----------------------------------------------------------------------------

class Embedder(Protocol):
    name: str

    async def embed(self, texts: list[str]) -> list[np.ndarray]: ...


class HashEmbedder:
    """Words plus character 3-grams (helps Arabic morphology), hashed into a fixed vector."""

    name = "hash-1024"
    dims = 1024

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dims, dtype=np.float32)
        words = re.findall(r"\w+", text.lower())
        grams = [w[i:i + 3] for w in words if len(w) > 3 for i in range(len(w) - 2)]
        for tok, weight in [(w, 1.0) for w in words] + [(g, 0.5) for g in grams]:
            h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "little")
            v[h % self.dims] += weight if (h >> 63) & 1 else -weight
        n = np.linalg.norm(v)
        return v / n if n else v

    async def embed(self, texts: list[str]) -> list[np.ndarray]:
        return [self._vec(t) for t in texts]


class OpenAIEmbedder:
    name = "openai:text-embedding-3-small"

    def __init__(self) -> None:
        from .openai_client import async_client

        self.client = async_client("embeddings")

    async def embed(self, texts: list[str]) -> list[np.ndarray]:
        resp = await self.client.embeddings.create(model="text-embedding-3-small", input=texts)
        out = []
        for d in resp.data:
            v = np.asarray(d.embedding, dtype=np.float32)
            out.append(v / (np.linalg.norm(v) or 1.0))
        return out


# ----------------------------------------------------------------------------
# The vector store (one SQLite file)
#   Vectors are float32 blobs; search is a brute-force dot product in numpy, which is plenty (and
#   exact) for a few thousand items.
# ----------------------------------------------------------------------------

class VectorStore:
    def __init__(self, path: Path | str, embedder: Embedder | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder or HashEmbedder()
        self.fallback = HashEmbedder()
        self._queries: dict[tuple[str, str], np.ndarray] = {}  # (embedder, text) -> vector
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL, meta TEXT,
                created TEXT NOT NULL, embedder TEXT NOT NULL, vec BLOB NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind, embedder)")

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    async def _embed(self, texts: list[str]) -> tuple[str, list[np.ndarray]]:
        try:
            return self.embedder.name, await self.embedder.embed(texts)
        except Exception:  # offline or quota: still remember it, with the local embedder
            return self.fallback.name, await self.fallback.embed(texts)

    # ------------------------------------------------------------------------
    # Adding items
    #   Batched embedding, exact duplicates skipped.
    # ------------------------------------------------------------------------

    async def add(self, kind: str, text: str, meta: dict | None = None, embed_text: str | None = None) -> None:
        """embed_text: what the vector is made from, if not `text` (pairs embed THEIR message)."""
        await self.add_many(kind, [(text, meta or {}, embed_text)])

    async def add_many(self, kind: str, items: list[tuple[str, dict, str | None]], batch: int = 100) -> int:
        """Bulk insert (text, meta, embed_text); embeds in batches. Skips exact duplicates
        (same text, same 'them', same chat). Returns how many were added."""
        existing = await asyncio.to_thread(self._keys, kind)
        fresh, seen = [], set(existing)
        for text, meta, embed_text in items:
            text = text.strip()
            key = (text, meta.get("them", ""), _norm((meta.get("to") or [""])[0]))
            if text and key not in seen:
                seen.add(key)
                fresh.append((text, meta, (embed_text or text).strip()))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for i in range(0, len(fresh), batch):
            chunk = fresh[i:i + batch]
            name, vecs = await self._embed([e for _, _, e in chunk])
            rows = [(kind, t, json.dumps(m, ensure_ascii=False), now, name, v.astype(np.float32).tobytes())
                    for (t, m, _), v in zip(chunk, vecs)]
            await asyncio.to_thread(self._insert_many, rows)
        return len(fresh)

    def _keys(self, kind: str) -> set[tuple]:
        if kind == WHATSAPP_SENT:
            return set()  # sending the same thing twice is real; only bulk imports dedupe
        with self._db() as db:
            rows = db.execute("SELECT text, meta FROM items WHERE kind = ?", (kind,)).fetchall()
        keys = set()
        for text, meta in rows:
            m = json.loads(meta or "{}")
            keys.add((text, m.get("them", ""), _norm((m.get("to") or [""])[0])))
        return keys

    def _insert_many(self, rows: list[tuple]) -> None:
        with self._db() as db:
            db.executemany("INSERT INTO items (kind, text, meta, created, embedder, vec) VALUES (?,?,?,?,?,?)", rows)

    def delete_kind(self, kind: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM items WHERE kind = ?", (kind,))

    # ------------------------------------------------------------------------
    # Single items (used by facts.py)
    #   items():        every item of a kind as (text, meta, created), oldest first.
    #   delete_where(): remove the items of a kind whose meta[key] equals value.
    # ------------------------------------------------------------------------

    def items(self, kind: str) -> list[tuple[str, dict, str]]:
        with self._db() as db:
            rows = db.execute("SELECT text, meta, created FROM items WHERE kind = ? ORDER BY id", (kind,)).fetchall()
        return [(text, json.loads(meta or "{}"), created) for text, meta, created in rows]

    def delete_where(self, kind: str, key: str, value: str) -> int:
        with self._db() as db:
            rows = db.execute("SELECT id, meta FROM items WHERE kind = ?", (kind,)).fetchall()
            ids = [(i,) for i, meta in rows if json.loads(meta or "{}").get(key) == value]
            db.executemany("DELETE FROM items WHERE id = ?", ids)
        return len(ids)

    def first_meta(self, kind: str) -> dict:
        """Meta of one item of this kind ({} if none): e.g. which version of the docs is indexed."""
        with self._db() as db:
            row = db.execute("SELECT meta FROM items WHERE kind = ? LIMIT 1", (kind,)).fetchone()
        return json.loads(row[0] or "{}") if row else {}

    def count(self, kind: str | None = None) -> int:
        with self._db() as db:
            sql, args = ("SELECT COUNT(*) FROM items WHERE kind = ?", (kind,)) if kind else ("SELECT COUNT(*) FROM items", ())
            return db.execute(sql, args).fetchone()[0]

    # ------------------------------------------------------------------------
    # Searching
    #   prime() embeds several queries in ONE request; search() reuses those vectors and filters by kind
    #   and recipient before ranking.
    # ------------------------------------------------------------------------

    async def prime(self, queries: list[str]) -> None:
        """Embed several search texts in ONE request (a draft looks up their message and the topic)."""
        todo = list(dict.fromkeys(q for q in queries if q.strip() and (self.embedder.name, q) not in self._queries))
        if not todo:
            return
        try:
            vecs = await self.embedder.embed(todo)
        except Exception:
            return  # search() will try again (or fall back) one by one
        self._remember({(self.embedder.name, q): v for q, v in zip(todo, vecs)})

    def _remember(self, items: dict) -> None:
        if len(self._queries) > 500:
            self._queries.clear()
        self._queries.update(items)

    async def _query_vec(self, emb, query: str) -> np.ndarray:
        key = (emb.name, query)
        if key not in self._queries:
            (vec,) = await emb.embed([query])
            self._remember({key: vec})
        return self._queries[key]

    async def search(self, query: str, k: int = 4, kind: str | None = None, to: str | None = None) -> list[dict]:
        """`to` keeps only items sent to that recipient (phone/JID digits or email)."""
        rows = await asyncio.to_thread(self._rows, kind)
        if to:
            want = _norm(to)
            rows = [r for r in rows if any(_norm(x) == want for x in json.loads(r[1] or "{}").get("to", []))]
        if not rows or not query.strip():
            return []
        by_embedder: dict[str, list] = {}
        for r in rows:
            by_embedder.setdefault(r[3], []).append(r)
        scored = []
        for name, group in by_embedder.items():
            emb = self.embedder if name == self.embedder.name else self.fallback if name == self.fallback.name else None
            if emb is None:
                continue  # made by an embedder we no longer have; can't compare
            try:
                q = await self._query_vec(emb, query)
            except Exception:
                continue
            mat = np.frombuffer(b"".join(r[4] for r in group), dtype=np.float32).reshape(len(group), -1)
            if mat.shape[1] != q.shape[0]:
                continue
            for r, score in zip(group, mat @ q):
                scored.append({"text": r[0], "meta": json.loads(r[1] or "{}"), "created": r[2], "score": float(score)})
        return sorted(scored, key=lambda d: d["score"], reverse=True)[:k]

    def _rows(self, kind: str | None) -> list[tuple]:
        with self._db() as db:
            sql = "SELECT text, meta, created, embedder, vec FROM items" + (" WHERE kind = ?" if kind else "")
            return db.execute(sql, (kind,) if kind else ()).fetchall()


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _norm(recipient: str) -> str:
    """'201001234567@s.whatsapp.net' and '+20 100 123 4567' -> '201001234567'."""
    r = str(recipient).strip().lower()
    if "@" in r and not r.endswith(("@s.whatsapp.net", "@c.us")):
        return r  # email or group JID
    return re.sub(r"\D", "", r.split("@")[0])


# ----------------------------------------------------------------------------
# Backfilling from WhatsApp history
# ----------------------------------------------------------------------------

async def learn_history(directory, memory: VectorStore, chats: int = 15, per_chat: int = 150,
                        progress=lambda done, total, name: None) -> tuple[int, int]:
    """Backfill (their message -> your reply) pairs from past conversations in your most-messaged
    chats. Returns (pairs added, chats read). Already-imported pairs are skipped, so it's safe to rerun."""
    refs = directory.frequent(chats)
    added = 0
    for i, ref in enumerate(refs):
        progress(i, len(refs), ref.label)
        pairs = await asyncio.to_thread(directory.history_pairs, ref.jid, per_chat)
        items = [(mine, {"to": [ref.jid], "them": theirs, "source": "history"}, theirs) for theirs, mine in pairs]
        added += await memory.add_many(WHATSAPP_PAIR, items)
    progress(len(refs), len(refs), "")
    return added, len(refs)


# ----------------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------------

def make_memory(path: Path | str, backend: str) -> VectorStore:
    embedder: Embedder = HashEmbedder()
    if backend == "openai":
        try:
            embedder = OpenAIEmbedder()
        except Exception:
            pass
    return VectorStore(path, embedder)
