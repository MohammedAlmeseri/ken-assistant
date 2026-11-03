"""Tests never reach the real OpenAI API (it costs money): a fake key, and every client gets a
transport that fails any request that a test didn't fake itself. The browser and the YouTube API
are faked too."""

from __future__ import annotations

import httpx2  # the OpenAI SDK ships its own httpx fork
import pytest

import assistant.openai_client as oc
import assistant.web as web


# ----------------------------------------------------------------------------
# The no-spending guard
#   Every OpenAI client in the tests gets a transport that raises on any real request, and the
#   request log goes to a temp folder. A test can never cost money.
# ----------------------------------------------------------------------------

def _refuse(request):
    raise RuntimeError(f"tests must not call the real OpenAI API ({request.url.path}): fake it")


@pytest.fixture(autouse=True)
def no_real_openai(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")  # .env never overrides an existing value
    monkeypatch.setattr(oc, "LOG", tmp_path / "openai_requests.jsonl")  # the real request log stays untouched
    monkeypatch.setattr(oc, "_today", {})
    real_async, real_sync = oc.async_client, oc.sync_client

    def guarded(real):
        def make(purpose: str, **http):
            http.setdefault("transport", httpx2.MockTransport(_refuse))
            return real(purpose, **http)
        return make

    monkeypatch.setattr(oc, "async_client", guarded(real_async))
    monkeypatch.setattr(oc, "sync_client", guarded(real_sync))


# ----------------------------------------------------------------------------
# Fake browser
#   Links 'opened' during tests are recorded instead of opening a browser.
# ----------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def opened(monkeypatch):
    """No real browser tabs or YouTube API calls: returns the list of URLs 'opened'."""
    urls: list[str] = []
    monkeypatch.setattr(web, "open_in_browser", urls.append)

    def no_network(url):
        raise RuntimeError("tests must not call the real YouTube API: fake web._get_json")

    monkeypatch.setattr(web, "_get_json", no_network)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    return urls
