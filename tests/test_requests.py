"""Every OpenAI request is logged; draft lookups embed their texts in one request."""

from __future__ import annotations

import asyncio
import json

import httpx2 as httpx  # the OpenAI SDK ships its own httpx fork
import numpy as np

import assistant.openai_client as oc
from assistant.memory import VectorStore


# ----------------------------------------------------------------------------
# The request log
# ----------------------------------------------------------------------------

def test_every_request_is_logged_and_counted(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(oc, "LOG", tmp_path / "openai_requests.jsonl")
    monkeypatch.setattr(oc, "_today", {})
    calls = []

    def reply(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:  # the SDK retries a 500 once: that's two real requests
            return httpx.Response(500, json={"error": {"message": "boom"}})
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 0, "model": "gpt-6-luna",
                                         "choices": [{"index": 0, "finish_reason": "stop",
                                                      "message": {"role": "assistant", "content": "hi"}}]})

    client = oc.async_client("chat", transport=httpx.MockTransport(reply))
    client = client.with_options(max_retries=1)
    resp = asyncio.run(client.chat.completions.create(model="gpt-6-luna", messages=[{"role": "user", "content": "x"}]))
    assert resp.choices[0].message.content == "hi"
    lines = [json.loads(l) for l in oc.LOG.read_text(encoding="utf-8").splitlines()]
    assert [(l["endpoint"], l["model"], l["status"], l["purpose"], l["tag"]) for l in lines] == [
        ("chat/completions", "gpt-6-luna", 500, "chat", "app"), ("chat/completions", "gpt-6-luna", 200, "chat", "app")]
    assert oc.requests_today() == 2


# ----------------------------------------------------------------------------
# One embedding request per draft
# ----------------------------------------------------------------------------

class CountingEmbedder:
    name = "counting"

    def __init__(self):
        self.requests = []

    async def embed(self, texts):
        self.requests.append(list(texts))
        return [np.ones(4, np.float32) / 2 for _ in texts]


def test_prime_embeds_both_lookups_in_one_request(tmp_path):
    emb = CountingEmbedder()
    store = VectorStore(tmp_path / "m.db", emb)
    asyncio.run(store.add("whatsapp_sent", "see you friday", {"to": ["2010"]}))
    emb.requests.clear()
    asyncio.run(store.prime(["are you free friday?", "plans", "plans", ""]))
    assert emb.requests == [["are you free friday?", "plans"]]  # one request, no duplicates or blanks
    asyncio.run(store.search("are you free friday?", kind="whatsapp_sent"))
    asyncio.run(store.search("plans", kind="whatsapp_sent"))
    asyncio.run(store.search("plans", kind="whatsapp_sent"))
    assert len(emb.requests) == 1  # searches reuse the primed vectors
