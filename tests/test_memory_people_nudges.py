"""Remember / person summary / voice notes / follow-up window / proactive nudges (all offline)."""

from __future__ import annotations

import asyncio
import json
import queue
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from assistant.agent import Agent
from assistant.config import Settings
from assistant.contacts import ChatDirectory, ChatRef, ChatStats, MockDirectory
from assistant.facts import Facts, has_remember_intent, parse_yearly
from assistant.llm import LLMReply, ToolCall
from assistant.memory import HashEmbedder, VectorStore
from assistant.mock_tools import MockToolSource
from assistant.nudges import NudgeEngine, in_window, is_acknowledgement, judge_answer, parse_events
from assistant.people import build_profile
from assistant.reminders import Reminders
from assistant.voicenotes import TranscriptCache, VoiceNotes, has_voice_intent
from tests.test_backend import FakeBackend

LOCAL = datetime.now().astimezone().tzinfo


# ----------------------------------------------------------------------------
# 1. Remember that…
# ----------------------------------------------------------------------------

def _facts(tmp_path, clock=None):
    store = VectorStore(tmp_path / "m.db", HashEmbedder())
    reminders = Reminders(tmp_path / "r.json", clock=clock or (lambda: datetime(2026, 4, 1, 9, 0, tzinfo=LOCAL)))
    return Facts(store, MockDirectory(), reminders), reminders


def test_remember_intent_needs_the_owners_words():
    assert has_remember_intent("Ken, remember Youssef's birthday is 3 May")
    assert has_remember_intent("افتكر إن عيد ميلاد يوسف ٣ مايو")
    assert not has_remember_intent("What did Youssef say?")
    assert parse_yearly("05-03") == (5, 3) and parse_yearly("2026-02-29") == (2, 29) and parse_yearly("13-01") is None


def test_birthday_makes_a_yearly_reminder_that_comes_back(tmp_path):
    now = [datetime(2026, 4, 1, 9, 0, tzinfo=LOCAL)]
    facts, reminders = _facts(tmp_path, clock=lambda: now[0])
    fact = asyncio.run(facts.add("Youssef's birthday is 3 May", "Youssef", "05-03"))
    assert fact.date == "05-03" and fact.reminder_id
    assert reminders.due() == []
    now[0] = datetime(2026, 5, 3, 10, 1, tzinfo=LOCAL)
    fired = reminders.due()
    assert [r.text for r in fired] == ["Youssef's birthday is 3 May"]
    # still there, moved on a year
    assert [datetime.fromisoformat(r.at).year for r in reminders.reminders] == [2027]
    assert [f.text for f in facts.on_date(date(2027, 5, 3))] == ["Youssef's birthday is 3 May"]


def test_forget_removes_the_fact_and_its_reminder(tmp_path):
    facts, reminders = _facts(tmp_path)
    asyncio.run(facts.add("Youssef's birthday is 3 May", "Youssef", "05-03"))
    asyncio.run(facts.add("I take my coffee black", "me"))
    gone = asyncio.run(facts.forget("youssef birthday"))
    assert gone and "Youssef" in gone.text
    assert [f.text for f in facts.all()] == ["I take my coffee black"]
    assert reminders.reminders == []


def test_facts_about_a_named_person_are_recalled(tmp_path):
    facts, _ = _facts(tmp_path)
    asyncio.run(facts.add("Brother loves football", "Brother"))
    asyncio.run(facts.add("I take my coffee black", "me"))
    found = asyncio.run(facts.relevant("what should I get my Brother as a gift?"))
    assert "Brother loves football" in [f.text for f in found]
    assert asyncio.run(Facts(VectorStore(tmp_path / "empty.db", HashEmbedder())).relevant("anything")) == []


def test_agent_saves_only_when_asked_and_uses_saved_notes(tmp_path):
    facts, _ = _facts(tmp_path)
    remember = LLMReply(tool_calls=[ToolCall("r1", "remember_fact", {"fact": "Brother loves football", "about": "Brother"})])
    backend = FakeBackend([remember, LLMReply(text="ok"),  # injected: the chat "told" Ken to remember
                           remember, LLMReply(text="Noted, Sir."),
                           LLMReply(text="A football shirt, Sir.")])
    agent = Agent(backend, MockToolSource(), Settings(audit_log_path=str(tmp_path / "a.jsonl")), None,
                  directory=MockDirectory(), facts=facts)
    asyncio.run(agent.start())
    asyncio.run(agent.run("Summarize my brother's chat"))
    assert facts.count() == 0 and "Not saved" in agent.history[2]["content"]
    asyncio.run(agent.run("Remember that my brother loves football"))
    assert facts.count() == 1
    asyncio.run(agent.run("What gift for my Brother?"))
    assert "Brother loves football" in backend.seen[-1][0]["content"]  # in the system prompt


# ----------------------------------------------------------------------------
# 2. Person / group summary
# ----------------------------------------------------------------------------

def test_person_profile_has_stats_waiting_and_facts(tmp_path):
    facts, _ = _facts(tmp_path)
    asyncio.run(facts.add("Brother is moving to Cairo in June", "Brother"))
    text, card = asyncio.run(build_profile(MockDirectory(), facts, MockToolSource(), "brother"))
    assert card is not None and card.jid == "201001234567@s.whatsapp.net" and card.facts == 1
    assert "you sent 14, received 19" in text
    assert "hasn't answered" in text and "dinner at 8" in text
    assert "moving to Cairo" in text
    none_text, none_card = asyncio.run(build_profile(MockDirectory(), facts, MockToolSource(), "zzzz"))
    assert none_card is None and "No chat matches" in none_text


def test_agent_returns_a_person_card(tmp_path):
    backend = FakeBackend([LLMReply(tool_calls=[ToolCall("p1", "person_summary", {"name": "Brother"})]),
                           LLMReply(text="Sir, your brother wants dinner on Friday.")])
    agent = Agent(backend, MockToolSource(), Settings(audit_log_path=str(tmp_path / "a.jsonl")), None,
                  directory=MockDirectory())
    asyncio.run(agent.start())
    result = asyncio.run(agent.run("Who is my brother?"))
    assert [p.name for p in result.people] == ["Brother"]
    assert not agent.guard.unknown(["201001234567@s.whatsapp.net"])  # "reply to him" may send there


def _whatsapp_store(tmp_path, rows: list[tuple]) -> ChatDirectory:
    """A tiny messages.db: rows of (id, chat_jid, sender, content, timestamp, is_from_me, media_type)."""
    store = tmp_path / "store"
    store.mkdir()
    db = sqlite3.connect(store / "messages.db")
    db.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TEXT, "
               "is_from_me INTEGER, media_type TEXT)")
    db.execute("CREATE TABLE chats (jid TEXT, name TEXT, last_message_time TEXT)")
    db.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", rows)
    db.execute("INSERT INTO chats VALUES (?,?,?)", (rows[0][1], "Sara", rows[-1][4]))
    db.commit()
    db.close()
    return ChatDirectory(store)


def test_chat_stats_from_the_whatsapp_cache(tmp_path):
    now = datetime.now(timezone.utc)
    ts = lambda h: (now - timedelta(hours=h)).isoformat()  # noqa: E731
    jid = "201005550000@s.whatsapp.net"
    directory = _whatsapp_store(tmp_path, [
        ("1", jid, jid, "hi", ts(30), 0, ""), ("2", jid, "", "hey", ts(29.5), 1, ""),
        ("3", jid, jid, "are you coming tomorrow?", ts(5), 0, ""), ("4", jid, jid, "??", ts(4), 0, "")])
    s = directory.stats(jid)
    assert (s.sent, s.received, s.last_from_me, s.reply_minutes) == (1, 3, False, 30)
    assert s.waiting_texts == ["are you coming tomorrow?", "??"]
    assert abs((now - s.waiting_since).total_seconds() - 5 * 3600) < 5


# ----------------------------------------------------------------------------
# 3. Voice notes, only on request
# ----------------------------------------------------------------------------

class FakeWhatsApp:
    def __init__(self, path):
        self.path, self.calls = path, 0

    async def call_tool(self, name, args):
        assert name == "whatsapp__download_media"
        self.calls += 1
        return json.dumps({"Success": True, "Message": "", "MediaType": "audio", "Path": str(self.path)})


def test_voice_note_is_transcribed_once_and_shows_in_the_chat(tmp_path, monkeypatch):
    jid = "201005550000@s.whatsapp.net"
    now = datetime.now(timezone.utc)
    directory = _whatsapp_store(tmp_path, [("n1", jid, jid, "", (now - timedelta(minutes=5)).isoformat(), 0, "audio")])
    note_file = tmp_path / "store" / "n1.ogg"
    note_file.write_bytes(b"ogg")
    monkeypatch.setattr("assistant.voicenotes.decode", lambda data: np.zeros(16000 * 42, dtype=np.float32))
    heard = []

    async def transcribe(audio):
        heard.append(len(audio))
        return "I'll be late, start without me"

    tools = FakeWhatsApp(note_file)
    notes = VoiceNotes(tmp_path / "store", tools, transcribe, TranscriptCache(tmp_path / "vn.db"))
    directory.transcripts = notes.cache
    first = asyncio.run(notes.transcribe_chat(jid))
    second = asyncio.run(notes.transcribe_chat(jid))
    assert "(0:42)" in first and "start without me" in first and first == second
    assert tools.calls == 1 and len(heard) == 1  # cached: never paid for twice
    assert "[voice 0:42] «I'll be late, start without me»" in directory.recent_conversation(jid)


def test_voice_note_path_must_stay_in_the_store(tmp_path):
    jid = "201005550000@s.whatsapp.net"
    _whatsapp_store(tmp_path, [("n1", jid, jid, "", datetime.now(timezone.utc).isoformat(), 0, "audio")])
    outside = tmp_path / "secret.txt"
    outside.write_text("x")

    async def transcribe(audio):
        raise AssertionError("must not get here")

    notes = VoiceNotes(tmp_path / "store", FakeWhatsApp(outside), transcribe, TranscriptCache(tmp_path / "vn.db"))
    assert "isn't in the WhatsApp store" in asyncio.run(notes.transcribe_chat(jid))


def test_voice_notes_need_the_owner_to_ask(tmp_path):
    assert has_voice_intent("what did Ahmed say in his voice note?") and has_voice_intent("اسمعلي الفويس بتاع احمد")
    assert not has_voice_intent("what did Ahmed say?")

    class Notes:
        called = False

        async def transcribe_chat(self, *a):
            Notes.called = True
            return "x"

    backend = FakeBackend([LLMReply(tool_calls=[ToolCall("v1", "voice_notes", {"chat": "Brother"})]), LLMReply(text="ok")])
    agent = Agent(backend, MockToolSource(), Settings(audit_log_path=str(tmp_path / "a.jsonl")), None,
                  directory=MockDirectory(), voice_notes=Notes())
    asyncio.run(agent.start())
    asyncio.run(agent.run("What's new with my brother?"))
    assert not Notes.called and "Not transcribed" in agent.history[2]["content"]


# ----------------------------------------------------------------------------
# 4. Follow-up window
# ----------------------------------------------------------------------------

class ScriptedVAD:
    def __init__(self, speech: list[bool]):
        self.speech = list(speech)

    def predict(self, chunk, frame_size=640):
        return 0.9 if (self.speech.pop(0) if self.speech else False) else 0.0


def _listener():
    from assistant.voice.wake import CHUNK, WakeListener

    w = WakeListener(model=None, on_wake=lambda: None, on_audio=lambda *a, **k: None)
    return w, CHUNK


def test_follow_up_ignores_a_cough_but_keeps_speech():
    w, chunk = _listener()
    for speech, expect_audio in (([True] * 3 + [False] * 14, False), ([True] * 8 + [False] * 14, True)):
        w._q = queue.Queue()
        for _ in speech:
            w._q.put(np.zeros(chunk, dtype=np.int16))
        audio = w._capture(ScriptedVAD(speech), wait_s=3, min_speech=0.4)
        assert (audio is not None) == expect_audio


def test_follow_up_request_expires_and_waits_while_paused():
    w, _ = _listener()
    w.follow_up(8, start_within=-1)  # too late
    assert w._take_follow_up() == 0.0 and w._follow is None
    busy = [True]
    w.paused = lambda: busy[0]
    w.follow_up(8)
    assert w._take_follow_up() == 0.0 and w._follow is not None  # kept until Ken is free
    busy[0] = False
    assert w._take_follow_up() == 8


def test_thanks_closes_a_follow_up():
    from assistant.ui.controller import CLOSE_WORDS

    for text in ("Thanks", "thank you Ken", "that's all", "شكرا", "ok thanks"):
        assert CLOSE_WORDS.match(text), text
    assert not CLOSE_WORDS.match("thanks, and reply yes to him")


# ----------------------------------------------------------------------------
# 5. Proactive nudges
# ----------------------------------------------------------------------------

class NudgeDirectory:
    """Two people: Youssef (close, asked a question 4 h ago) and Ali (said "ok 👍")."""

    def __init__(self, now: datetime):
        self.now = now
        self.refs = [ChatRef("1@s.whatsapp.net", "Youssef", "1", False, 30), ChatRef("2@s.whatsapp.net", "Ali", "2", False, 2)]

    def frequent(self, limit=6):
        return self.refs[:1]

    def active(self, days=3):
        return self.refs

    def stats(self, jid, days=30):
        if jid.startswith("1"):
            return ChatStats(jid, last_time=self.now - timedelta(hours=4), waiting_texts=["are you coming to class?"],
                             waiting_since=self.now - timedelta(hours=4))
        return ChatStats(jid, last_time=self.now - timedelta(hours=30), waiting_texts=["ok 👍"],
                         waiting_since=self.now - timedelta(hours=30))


def _engine(tmp_path, now, **settings):
    s = Settings(**settings)
    return NudgeEngine(NudgeDirectory(now), s, tmp_path / "nudges.json", clock=lambda: now)


def test_nudge_rules_limits_and_ai_check(tmp_path):
    now = datetime(2026, 10, 31, 14, 0, tzinfo=LOCAL)
    asked = []

    async def judge(n):
        asked.append(n.key)
        return True

    engine = _engine(tmp_path, now)
    shown = asyncio.run(engine.check(judge))
    assert [n.name for n in shown] == ["Youssef"] and "asked you something" in shown[0].title  # Ali's "ok" doesn't count
    assert asyncio.run(engine.check(judge)) == []  # never twice
    assert len(asked) == 1
    # a fresh engine on the same state file remembers what was shown
    assert asyncio.run(_engine(tmp_path, now).check(judge)) == []


def test_nudges_stay_quiet_when_they_should(tmp_path):
    async def yes(n):
        return True

    async def no(n):
        return False

    late = datetime(2026, 10, 31, 23, 30, tzinfo=LOCAL)
    assert asyncio.run(_engine(tmp_path / "a", late).check(yes)) == []  # quiet hours
    noon = datetime(2026, 10, 31, 12, 0, tzinfo=LOCAL)
    assert asyncio.run(_engine(tmp_path / "b", noon).check(no)) == []  # the AI check said no
    assert asyncio.run(_engine(tmp_path / "c", noon).check(yes, focus=True)) == []  # pomodoro focus
    assert asyncio.run(_engine(tmp_path / "d", noon, nudges=False).check(yes)) == []  # turned off
    capped = _engine(tmp_path / "e", noon, nudge_max_per_day=0)
    assert asyncio.run(capped.check(yes)) == []
    muted = _engine(tmp_path / "f", noon)
    muted.mute("1@s.whatsapp.net")
    assert asyncio.run(muted.check(yes)) == []


def test_calendar_alert_comes_through_even_in_focus(tmp_path):
    now = datetime(2026, 10, 31, 13, 45, tzinfo=LOCAL)
    events = parse_events("Sat 31 Oct 14:00-15:00 · Lecture · @ Hall B\nSat 31 Oct (all day) · Holiday", now)
    assert [(e[0].hour, e[2]) for e in events] == [(14, "Lecture")]

    async def yes(n):
        return True

    shown = asyncio.run(_engine(tmp_path, now).check(yes, events, focus=True))
    assert [n.kind for n in shown] == ["event"] and "15 minutes" in shown[0].spoken


def test_nudge_helpers():
    assert is_acknowledgement("ok 👍") and is_acknowledgement("تمام") and is_acknowledgement("😂😂")
    assert not is_acknowledgement("are you free tomorrow?")
    assert in_window("23:00-09:00", datetime(2026, 1, 1, 2, 0)) and not in_window("23:00-09:00", datetime(2026, 1, 1, 12, 0))
    assert judge_answer("Yes.") is True and judge_answer("no") is False and judge_answer("maybe") is None


# ----------------------------------------------------------------------------
# UI: person card star and the nudge pop-up
# ----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qt():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_person_card_and_nudge_toast(qt):
    from assistant.nudges import Nudge
    from assistant.people import PersonProposal
    from assistant.ui.nudge_toast import NudgeToast
    from assistant.ui.panel import Panel

    panel = Panel()
    stats = MockDirectory().stats("201001234567@s.whatsapp.net")
    card = panel.add_person(PersonProposal("Brother", "201001234567@s.whatsapp.net", False, "201001234567", stats, 1))
    starred = []
    panel.person_star.connect(starred.append)
    card.btn_star.click()
    assert starred and card.btn_star.text() == "☆"
    card.set_important(True)
    assert card.btn_star.text() == "★"

    toast = NudgeToast()
    drafts = []
    toast.draft.connect(drafts.append)
    nudge = Nudge("k", "unanswered", "Youssef asked you something · 4 h ago", "are you coming?", "Sir…", "1@s", "Youssef")
    toast.show_nudge(nudge, None)
    assert toast.isVisible()
    toast.findChildren(type(card.btn_reply))[-1].click()  # "draft reply"
    assert drafts == [nudge] and not toast.isVisible()
    toast.close()
    panel.close()
