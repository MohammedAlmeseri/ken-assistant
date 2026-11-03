"""Timers, Google Calendar helpers, the briefing prompt and the agent's event/timer tools (offline)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

import pytest

from assistant.agent import Agent
from assistant.briefing import build_prompt
from assistant.calendar_mcp import format_events, free_slots, local_tz, parse_day
from assistant.config import Settings
from assistant.llm import LLMReply, ToolCall, ToolSpec
from assistant.mock_tools import MockToolSource
from assistant.timers import TimerManager, clock_text
from assistant.tools import HIDDEN, READ, classify
from tests.test_backend import FakeBackend, call

# ----------------------------------------------------------------------------
# Timers
# ----------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_clock_text():
    assert clock_text(0) == "00:00"
    assert clock_text(65) == "01:05"
    assert clock_text(3725) == "1:02:05"


def test_countdown_runs_pauses_and_finishes():
    clock = Clock()
    m = TimerManager(clock)
    t = m.start("countdown", "Tea", minutes=2)
    clock.t += 30
    assert t.view(clock()).text == "01:30"
    m.pause(t)
    clock.t += 500  # paused time doesn't count
    assert t.view(clock()).text == "01:30"
    m.resume(t)
    clock.t += 90
    assert m.poll() == [(t, "done")]
    assert t.view(clock()).done and not t.running
    assert m.poll() == []  # reported once


def test_pomodoro_phases():
    clock = Clock()
    m = TimerManager(clock)
    t = m.start("pomodoro", "Study", focus_minutes=25, break_minutes=5, rounds=2)
    v = t.view(clock())
    assert (v.phase, v.round, v.rounds, v.text) == ("focus", 1, 2, "25:00")
    clock.t += 25 * 60
    assert m.poll() == [(t, "break")]
    clock.t += 5 * 60
    assert m.poll() == [(t, "focus")] and t.view(clock()).round == 2
    clock.t += 25 * 60
    assert m.poll() == [(t, "long break")]
    clock.t += 15 * 60
    assert m.poll() == [(t, "done")]


def test_stopwatch_and_controls():
    clock = Clock()
    m = TimerManager(clock)
    sw = m.start("stopwatch", "Run")
    clock.t += 75
    assert sw.view(clock()).text == "01:15"
    m.reset(sw)
    assert sw.view(clock()).text == "00:00"
    m.start("countdown", "Eggs", minutes=7)
    assert "'Eggs' (countdown): 07:00 left of 07:00, running" in m.describe()
    assert m.control("pause", "egg").startswith("pause: 'Eggs'")  # partial name
    assert m.control("stop", "nobody").startswith("No timer called 'nobody'")
    m.control("stop", "all")
    assert m.timers == []
    with pytest.raises(ValueError):
        m.start("countdown", "x")  # no minutes
    with pytest.raises(ValueError):
        m.start("alarm")


# ----------------------------------------------------------------------------
# Calendar helpers
# ----------------------------------------------------------------------------


def test_parse_day():
    wed = date(2026, 10, 28)  # a Wednesday
    assert parse_day("today", wed) == wed
    assert parse_day("tomorrow", wed) == date(2026, 10, 29)
    assert parse_day("friday", wed) == date(2026, 10, 30)
    assert parse_day("wednesday", wed) == date(2026, 11, 4)  # the NEXT Wednesday
    assert parse_day("2026-12-01", wed) == date(2026, 12, 1)


def test_free_slots():
    tz = local_tz()

    def at(h, m=0):
        return datetime(2026, 10, 30, h, m, tzinfo=tz)

    busy = [(at(13), at(14)), (at(10), at(11)), (at(10, 30), at(12))]  # unsorted, overlapping
    assert free_slots(busy, at(9), at(18), 60) == [(at(9), at(10)), (at(12), at(13)), (at(14), at(18))]
    assert free_slots(busy, at(9), at(18), 90) == [(at(14), at(18))]


def test_format_events():
    events = [
        {"summary": "Class", "start": {"dateTime": "2026-10-30T14:00:00+03:00"},
         "end": {"dateTime": "2026-10-30T15:30:00+03:00"}, "location": "Hall 3"},
        {"summary": "Holiday", "start": {"date": "2026-10-31"}, "end": {"date": "2026-11-01"}, "_calendar": "Holidays"},
        {"summary": "Invite", "start": {"dateTime": "2026-10-30T18:00:00+03:00"},
         "end": {"dateTime": "2026-10-30T19:00:00+03:00"}, "_response": "needsAction"},
    ]
    text = format_events(events)
    assert "Class" in text and "@ Hall 3" in text
    assert "(all day) · Holiday · (Holidays)" in text
    assert "Invite · [you haven't accepted]" in text


def test_calendar_create_is_hidden_from_the_model():
    s = Settings()
    assert classify("calendar__list_events", s) == READ
    assert classify("calendar__find_free_time", s) == READ
    assert classify("calendar__create_event", s) == HIDDEN


# ----------------------------------------------------------------------------
# Briefing
# ----------------------------------------------------------------------------


def test_briefing_prompt_wraps_data_and_picks_language():
    text = build_prompt("egyptian_friendly", "Fri 30 Oct 14:00-15:00 · Class",
                        [("Youssef", "[Tue 10:00] Them: are you coming?")], ["[09:00] From Sara: Invoice due"])
    assert "Egyptian Arabic" in text and "Needs your reply" in text
    assert text.count("<<<UNTRUSTED_DATA") == 3  # calendar, WhatsApp and email are all data
    assert "Chat: Youssef" in text and "Invoice due" in text
    assert "No new WhatsApp messages." in build_prompt("us_professional", "", [], [])
    assert "in English" in build_prompt("us_professional", "", [], [])


# ----------------------------------------------------------------------------
# Calendar and timer tools in the agent
# ----------------------------------------------------------------------------


class CalendarTools(MockToolSource):
    """Mock tools plus a calendar server that records adds."""

    def __init__(self):
        super().__init__()
        self.added: list[dict] = []

    async def list_tools(self):
        specs = await super().list_tools()
        props = {"type": "object", "properties": {}}
        return specs + [ToolSpec("calendar__list_events", "List events", props),
                        ToolSpec("calendar__create_event", "Add an event", props)]

    async def call_tool(self, name, arguments):
        if name == "calendar__create_event":
            self.added.append(arguments)
            return f"Added: {arguments['summary']}"
        if name == "calendar__list_events":
            return "Fri 30 Oct 14:00-15:00 · Class"
        return await super().call_tool(name, arguments)


def make(replies, tmp_path, tools=None, timers=None):
    settings = Settings(audit_log_path=str(tmp_path / "audit.jsonl"))
    agent = Agent(FakeBackend(replies), tools or CalendarTools(), settings, None, timers=timers)
    asyncio.run(agent.start())
    return agent


def test_event_card_needs_a_click(tmp_path):
    tools = CalendarTools()
    agent = make([LLMReply(tool_calls=[call("propose_event", summary="Dinner with Ali", start="2026-10-30T20:00",
                                            minutes=90, location="Zamalek")]),
                  LLMReply(text="Card is ready.")], tmp_path, tools)
    names = [t.name for t in agent.specs]
    assert "propose_event" in names and "calendar__list_events" in names
    assert "calendar__create_event" not in names  # the model can't add events itself
    result = asyncio.run(agent.run("add dinner with Ali friday 8pm for 90 min"))
    assert tools.added == []  # nothing added by the model's turn
    event = result.events[0]
    assert (event.summary, event.start.hour, event.end - event.start) == ("Dinner with Ali", 20, timedelta(minutes=90))
    assert "NOT on the calendar" in agent.history[-2]["content"]
    # the user clicks Add
    assert asyncio.run(agent.add_event(event)) == "Added: Dinner with Ali"
    assert tools.added[0]["start"].startswith("2026-10-30T20:00")
    assert "calendar__create_event" in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")


def test_bad_event_is_refused(tmp_path):
    agent = make([LLMReply(tool_calls=[call("propose_event", summary="X", start="next friday")]),
                  LLMReply(text="ok")], tmp_path)
    result = asyncio.run(agent.run("add X"))
    assert result.events == [] and "must be a local date-time" in agent.history[-2]["content"]


def test_no_event_or_timer_tools_without_them(tmp_path):
    agent = make([], tmp_path, tools=MockToolSource())
    names = [t.name for t in agent.specs]
    assert "propose_event" not in names  # no calendar server
    assert "start_timer" not in names  # no TimerManager given


def test_ai_starts_and_controls_timers(tmp_path):
    timers = TimerManager(Clock())
    start = {"mode": "pomodoro", "name": "Study", "focus_minutes": 50, "break_minutes": 10, "rounds": 3}
    agent = make([LLMReply(tool_calls=[ToolCall("t1", "start_timer", start)]),
                  LLMReply(text="Started."),
                  LLMReply(tool_calls=[ToolCall("t2", "control_timer", {"action": "pause", "name": "study"})]),
                  LLMReply(text="Paused.")], tmp_path, timers=timers)
    asyncio.run(agent.run("start a 50/10 pomodoro called Study, 3 rounds"))
    t = timers.timers[0]
    assert (t.name, t.focus, t.short_break, t.rounds) == ("Study", 3000, 600, 3)
    asyncio.run(agent.run("pause it"))
    assert not t.running


def test_now_is_in_the_system_prompt(tmp_path):
    agent = make([LLMReply(text="hi")], tmp_path)
    asyncio.run(agent.run("hi"))
    assert f"Now: {datetime.now():%A %d %B %Y}" in agent.backend.seen[0][0]["content"]


# ----------------------------------------------------------------------------
# Reminders and watches
# ----------------------------------------------------------------------------


def test_reminders_fire_once_and_persist(tmp_path):
    from assistant.reminders import Reminders

    now = [datetime(2026, 10, 29, 17, 0).astimezone()]
    r = Reminders(tmp_path / "rem.json", clock=lambda: now[0])
    with pytest.raises(ValueError):
        r.remind("too late", now[0] - timedelta(minutes=1))
    r.remind("answer Sara", now[0] + timedelta(hours=1))
    r.remind("call mom", now[0] + timedelta(hours=3))
    assert "answer Sara" in r.describe() and "call mom" in r.describe()
    assert Reminders(tmp_path / "rem.json").describe() == r.describe()  # survives a restart
    now[0] += timedelta(hours=1, minutes=1)
    assert [x.text for x in r.due()] == ["answer Sara"]
    assert r.due() == []  # only once
    assert r.cancel("mom") == "Cancelled 1." and r.describe() == "No reminders or watches."


def test_watch_fires_on_their_reply_and_expires(tmp_path):
    from assistant.reminders import Reminders

    now = [datetime(2026, 10, 29, 17, 0).astimezone()]
    r = Reminders(tmp_path / "rem.json", clock=lambda: now[0])
    r.watch("2010@s.whatsapp.net", "Youssef", hours=2)
    r.watch("2011@s.whatsapp.net", "Ali", hours=1)
    replies = {"2010@s.whatsapp.net": "", "2011@s.whatsapp.net": ""}
    assert r.replies(lambda jid, since: replies[jid]) == []
    replies["2010@s.whatsapp.net"] = "on my way"
    fired = r.replies(lambda jid, since: replies[jid])
    assert [(w.name, text) for w, text in fired] == [("Youssef", "on my way")]
    now[0] += timedelta(hours=1, minutes=5)  # Ali's watch runs out without a reply
    assert r.replies(lambda jid, since: "") == [] and r.watches == []


def test_agent_sets_reminders_and_watches(tmp_path):
    from assistant.contacts import MockDirectory
    from assistant.reminders import Reminders

    rem = Reminders(tmp_path / "rem.json")
    settings = Settings(audit_log_path=str(tmp_path / "audit.jsonl"))
    replies = [LLMReply(tool_calls=[ToolCall("r1", "set_reminder", {"text": "answer Sara", "in_minutes": 30})]),
               LLMReply(text="Done."),
               LLMReply(tool_calls=[ToolCall("w1", "watch_chat", {"recipient": "201001234567@s.whatsapp.net",
                                                                  "name": "Brother"})]),
               LLMReply(text="Watching.")]
    agent = Agent(FakeBackend(replies), MockToolSource(), settings, None, directory=MockDirectory(), reminders=rem)
    asyncio.run(agent.start())
    assert {"set_reminder", "watch_chat", "reminders"} <= {t.name for t in agent.specs}
    asyncio.run(agent.run("remind me to answer Sara in 30 minutes"))
    asyncio.run(agent.run("tell me when my brother replies"))
    assert len(rem.reminders) == 1 and rem.reminders[0].text == "answer Sara"
    assert rem.watches[0].name == "Brother"


# ----------------------------------------------------------------------------
# Briefing opening line and answer language
# ----------------------------------------------------------------------------

def test_briefing_has_a_spoken_opening_line():
    assert "Good morning, Sir" in build_prompt("egyptian_friendly", "", [], [], persona="butler")
    assert "Sir" not in build_prompt("us_professional", "", [], [], persona="plain").split("CALENDAR")[0]


def test_answers_follow_the_users_language(tmp_path):
    from assistant.agent import language_hint

    assert "answer in English" in language_hint("What's new on WhatsApp?", "egyptian_friendly")
    assert "answer in Arabic" in language_hint("ايه الجديد على الواتساب؟", "egyptian_friendly")
    assert "answer in English" in language_hint("what did يوسف say?", "egyptian_friendly")  # mostly English
    assert language_hint("ايه الجديد؟", "us_professional") == ""  # that tone is always English
    agent = make([LLMReply(text="Sir, nothing new.")], tmp_path)
    asyncio.run(agent.run("What's new on WhatsApp?"))
    assert "The user wrote in English: answer in English" in agent.backend.seen[0][0]["content"]
