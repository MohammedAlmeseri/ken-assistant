"""Opening sites, searches and YouTube videos, and the link safety rule (no network, no browser)."""

from __future__ import annotations

import asyncio

import assistant.web as web
from assistant.agent import Agent
from assistant.config import Settings
from assistant.llm import LLMReply, ToolCall
from assistant.mock_tools import MockToolSource
from tests.test_backend import FakeBackend


# ----------------------------------------------------------------------------
# URL rules
# ----------------------------------------------------------------------------

def test_only_web_addresses_are_opened():
    assert web.normalize_url("github.com") == "https://github.com"
    assert web.normalize_url("http://example.org/x") == "http://example.org/x"
    for bad in ("javascript:alert(1)", "file:///C:/Windows", "data:text/html,hi", "notaurl"):
        assert web.normalize_url(bad) is None, bad


def test_link_rule():
    assert web.asked_for("https://github.com", "Ken, open github")  # the user named it
    assert web.asked_for("https://www.github.com/me", "افتح github")
    assert web.asked_for(web.google_search_url("flights"), "search flights")  # built by Ken
    assert web.asked_for(web.video_url("abc"), "play it")
    assert not web.asked_for("https://free-prize.example.com", "open the link Sara sent")  # from a message
    assert not web.asked_for("https://www.google.com/url?q=https://evil.example", "open google")  # redirect
    assert not web.asked_for("https://www.youtube.com/redirect?q=https://evil.example", "open youtube")


# ----------------------------------------------------------------------------
# The browser tools in the agent
# ----------------------------------------------------------------------------

def make(replies, tmp_path):
    agent = Agent(FakeBackend(replies), MockToolSource(), Settings(audit_log_path=str(tmp_path / "a.jsonl")), None)
    asyncio.run(agent.start())
    return agent


def test_named_site_opens_but_message_link_needs_a_click(tmp_path, opened):
    agent = make([LLMReply(tool_calls=[ToolCall("o1", "open_website", {"url": "github.com"})]),
                  LLMReply(text="Opened."),
                  LLMReply(tool_calls=[ToolCall("o2", "open_website", {"url": "https://free-prize.example.com/claim"})]),
                  LLMReply(text="Here it is.")], tmp_path)
    asyncio.run(agent.run("open github"))
    assert opened == ["https://github.com"]
    result = asyncio.run(agent.run("open the link in Sara's email"))
    assert opened == ["https://github.com"]  # not opened by itself
    assert [link.url for link in result.links] == ["https://free-prize.example.com/claim"]


def test_searches_open_results_pages(tmp_path, opened):
    agent = make([LLMReply(tool_calls=[ToolCall("s1", "web_search", {"query": "cheap flights cairo"}),
                                       ToolCall("s2", "web_search", {"query": "binary trees", "site": "youtube"})]),
                  LLMReply(text="Done.")], tmp_path)
    asyncio.run(agent.run("search cheap flights cairo and youtube binary trees"))
    assert opened == ["https://www.google.com/search?q=cheap+flights+cairo",
                      "https://www.youtube.com/results?search_query=binary+trees"]


def test_play_youtube_opens_the_top_video(tmp_path, opened, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    asked = []

    def fake_api(url):
        asked.append(url)
        return {"items": [{"id": {"videoId": "AAA"}, "snippet": {"title": "Binary Trees &amp; Heaps", "channelTitle": "CS"}},
                          {"id": {"videoId": "BBB"}, "snippet": {"title": "Trees 2", "channelTitle": "Uni"}}]}

    monkeypatch.setattr(web, "_get_json", fake_api)
    agent = make([LLMReply(tool_calls=[ToolCall("p1", "play_youtube", {"query": "binary trees"})]),
                  LLMReply(text="Playing.")], tmp_path)
    asyncio.run(agent.run("play a video about binary trees"))
    assert opened == ["https://www.youtube.com/watch?v=AAA"]
    assert "q=binary+trees" in asked[0] and "type=video" in asked[0]
    tool_output = agent.history[-2]["content"]
    assert "Binary Trees & Heaps" in tool_output and "UNTRUSTED" in tool_output  # titles are data


def test_play_without_a_key_opens_results(tmp_path, opened):
    agent = make([LLMReply(tool_calls=[ToolCall("p1", "play_youtube", {"query": "amr diab"})]),
                  LLMReply(text="Opened the results.")], tmp_path)
    asyncio.run(agent.run("play amr diab"))
    assert opened == ["https://www.youtube.com/results?search_query=amr+diab"]
    assert "YOUTUBE_API_KEY" in agent.history[-2]["content"]
