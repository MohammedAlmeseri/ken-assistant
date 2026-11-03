"""Proactive nudges: Ken speaks up on his own, a few times a day at most.

  "Sir, Youssef asked twice about tomorrow's class (3 h ago)."
  "Sir, Sara hasn't answered your question for 2 days."
  "Sir, Lecture starts in 20 minutes."

How a check works (every 10 minutes, from the app):
  1. Rules in code find candidates, with no AI and nothing sent anywhere:
       - unanswered: a one-to-one chat where they wrote last and you haven't replied for 3 h
         (people you message often) or 24 h (everyone else); "ok", "👍", "تمام" don't count.
       - owed: you asked a question in a close chat and got no reply for 2 days.
       - event: a calendar event starts within 20 minutes.
  2. Limits: quiet hours, at most N message nudges a day, the same thing never twice, a chat at
     most once every 6 hours, snoozed and muted chats skipped, nothing during a Pomodoro focus
     phase or a meeting (events still come through).
  3. A message candidate that passes gets one tiny AI check ("does this need a reply?"); the
     answer is cached, so the same message is never asked about twice.
  4. At most one message nudge is shown per check, so they never pile up.

Nudges never send anything: the card's "Draft reply" only asks Ken for a draft.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable

# ----------------------------------------------------------------------------
# What counts as "just an acknowledgement" (never needs a reply)
#   Short words in English and Arabic, laughter, or only emoji / punctuation.
# ----------------------------------------------------------------------------

_ACK = re.compile(r"^(ok(ay)?|k+|kk|okk+|thanks?|thank you|thx|ty|np|cool|nice|great|sure|yes|yep|no|nope|"
                  r"lol|ha(ha)+|hh+|done|good night|gn|bye|"
                  r"تمام|ماشي|اوك|أوك|اوكي|شكرا|شكراً|مشكور|حاضر|طيب|تمم|اه|أه|ايوه|لا|ه+|ههه+|خلاص|"
                  r"تصبح على خير|باي|حلو|جميل)[\s!.,،؟?]*$", re.IGNORECASE)
_NOT_WORDS = re.compile(r"^[\W\d_]*$")  # emoji, punctuation, numbers only


def is_acknowledgement(text: str) -> bool:
    t = (text or "").strip()
    if not t or t.startswith("[") or _NOT_WORDS.match(t):  # empty, "[image]", or only emoji/punctuation
        return True
    words = re.sub(r"[^\w\s]", " ", t).strip()  # "ok 👍" -> "ok"
    return len(words) <= 2 or bool(_ACK.match(words))


def is_question(text: str) -> bool:
    return "?" in text or "؟" in text


# ----------------------------------------------------------------------------
# One nudge
#   key:  stable id of "this situation", so it's never shown twice (e.g. their unanswered
#         burst that started at a given time).
#   kind: "unanswered", "owed" or "event". Events skip the AI check and the daily cap.
# ----------------------------------------------------------------------------

@dataclass
class Nudge:
    key: str
    kind: str
    title: str  # the card's first line, e.g. "Youssef is waiting for your reply"
    body: str  # the message(s) in question, short
    spoken: str  # what Ken says, if this one is spoken
    jid: str = ""
    name: str = ""
    important: bool = False  # a ★ person: spoken aloud, not just a card
    evidence: str = ""  # what the AI check reads (their messages)


def _ago(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{max(1, round(hours * 60))} min"
    if hours < 48:
        return f"{round(hours)} h"
    return f"{round(hours / 24)} days"


def _ago_ar(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    return f"{max(1, round(hours))} ساعة" if hours < 48 else f"{round(hours / 24)} أيام"


def _arabic(text: str) -> bool:
    return bool(re.search(r"[؀-ۿ]", text or ""))


# ----------------------------------------------------------------------------
# Calendar lines -> (start, end, title)
#   calendar_mcp.format_events writes 'Thu 30 Oct 14:00-15:00 · Title · @ place'. Lines without
#   a time (all-day events) are skipped. The year is taken from `now`.
# ----------------------------------------------------------------------------

_EVENT_LINE = re.compile(r"^\w{3} (\d{1,2} \w{3}) (\d{2}:\d{2})(?:-(\d{2}:\d{2}))? · ([^·]+)")


def parse_events(raw: str, now: datetime) -> list[tuple[datetime, datetime, str]]:
    out = []
    for line in (raw or "").splitlines():
        m = _EVENT_LINE.match(line.strip())
        if not m:
            continue
        try:
            start = datetime.strptime(f"{m.group(1)} {now.year} {m.group(2)}", "%d %b %Y %H:%M").replace(tzinfo=now.tzinfo)
            end = datetime.strptime(f"{m.group(1)} {now.year} {m.group(3) or m.group(2)}", "%d %b %Y %H:%M").replace(tzinfo=now.tzinfo)
        except ValueError:
            continue
        if end <= start:
            end = start + timedelta(hours=1)
        out.append((start, end, m.group(4).strip()))
    return out


def in_window(spec: str, now: datetime) -> bool:
    """'23:00-09:00' (may wrap past midnight): is `now` inside it?"""
    try:
        (h1, m1), (h2, m2) = [tuple(int(x) for x in part.strip().split(":")) for part in spec.split("-")]
        start, end = now.replace(hour=h1, minute=m1), now.replace(hour=h2, minute=m2)
    except ValueError:
        return False
    return start <= now < end if start <= end else (now >= start or now < end)


# ----------------------------------------------------------------------------
# Saved state: data/nudges.json
#   day/count: message nudges shown today (for the daily cap).
#   shown:     key -> when shown (kept 14 days), so nothing is repeated.
#   verdicts:  key -> the AI check's answer (true = needs a reply).
#   last_chat: jid -> when that chat was last nudged (once per 6 h).
#   snoozed:   jid -> until when.   muted: chats never nudged again ("Not this chat").
# ----------------------------------------------------------------------------

class NudgeState:
    def __init__(self, path: Path):
        self.path = path
        self.data = {"day": "", "count": 0, "shown": {}, "verdicts": {}, "last_chat": {}, "snoozed": {}, "muted": []}
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, TypeError):
                pass  # a broken file just starts fresh

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")

    def today_count(self, now: datetime) -> int:
        return self.data["count"] if self.data["day"] == now.date().isoformat() else 0

    def prune(self, now: datetime) -> None:
        cutoff = (now - timedelta(days=14)).isoformat()
        self.data["shown"] = {k: v for k, v in self.data["shown"].items() if v >= cutoff}
        self.data["verdicts"] = dict(list(self.data["verdicts"].items())[-300:])  # the newest 300 answers
        self.data["snoozed"] = {j: t for j, t in self.data["snoozed"].items() if t > now.isoformat()}


# ----------------------------------------------------------------------------
# The engine
#   directory: ChatDirectory (frequent(), active(), stats()).
#   settings:  nudge_* thresholds, quiet hours, daily cap, important_people.
#   clock:     injectable for tests.
# ----------------------------------------------------------------------------

Judge = Callable[[Nudge], Awaitable[bool | None]]


class NudgeEngine:
    def __init__(self, directory, settings, state_path: Path, clock: Callable[[], datetime] | None = None):
        self.directory = directory
        self.settings = settings
        self.state = NudgeState(state_path)
        self.clock = clock or (lambda: datetime.now().astimezone())

    # ------------------------------------------------------------------------
    # Step 1: rules
    # ------------------------------------------------------------------------

    def candidates(self, now: datetime) -> list[Nudge]:
        """Unanswered and owed messages, oldest wait first. Reads the local WhatsApp cache only."""
        s = self.settings
        close = {r.jid for r in self.directory.frequent(15)}
        important = set(s.important_people)
        out: list[Nudge] = []
        for ref in self.directory.active(days=3):
            if ref.is_group:
                continue
            stats = self.directory.stats(ref.jid, days=7)
            name = ref.label
            if not stats.last_from_me and stats.waiting_since is not None:
                real = [t for t in stats.waiting_texts if not is_acknowledgement(t)]
                waited = now - stats.waiting_since
                limit = timedelta(hours=s.nudge_hours_close if ref.jid in close else s.nudge_hours_other)
                if not real or waited < limit or waited > timedelta(days=3):
                    continue
                asked = any(map(is_question, real))
                what = "asked twice" if asked and len(real) >= 2 else "asked you something" if asked \
                    else "is waiting for your reply"
                title = f"{name} {what} · {_ago(waited)} ago"
                if _arabic(name):
                    spoken = f"Sir، {name} مستني ردك من {_ago_ar(waited)}."
                else:
                    spoken = f"Sir, {name} has been waiting {_ago(waited)} for your reply."
                out.append(Nudge(f"wa:{ref.jid}:{stats.waiting_since.isoformat()}", "unanswered", title,
                                 " / ".join(real[-2:])[:180], spoken, ref.jid, name, ref.jid in important,
                                 evidence="\n".join(real[-4:])))
            elif stats.last_from_me and ref.jid in close and is_question(stats.my_last_text) and stats.last_time:
                waited = now - stats.last_time
                if timedelta(days=2) <= waited <= timedelta(days=6):
                    spoken = (f"Sir، {name} مردش على سؤالك من {_ago_ar(waited)}." if _arabic(name)
                              else f"Sir, {name} hasn't answered your question for {_ago(waited)}.")
                    out.append(Nudge(f"owed:{ref.jid}:{stats.last_time.isoformat()}", "owed",
                                     f"{name} hasn't answered you · {_ago(waited)}", stats.my_last_text[:180], spoken,
                                     ref.jid, name, ref.jid in important))
        return out

    def event_nudges(self, events: list[tuple[datetime, datetime, str]], now: datetime) -> list[Nudge]:
        out = []
        for start, _, title in events:
            minutes = (start - now).total_seconds() / 60
            if 0 < minutes <= 20:
                out.append(Nudge(f"event:{title}:{start.isoformat()}", "event", f"{title} at {start:%H:%M}",
                                 f"Starts in {round(minutes)} min", f"Sir, {title} starts in {round(minutes)} minutes.",
                                 important=True))
        return out

    # ------------------------------------------------------------------------
    # Step 2: limits
    # ------------------------------------------------------------------------

    def _allowed(self, n: Nudge, now: datetime) -> bool:
        d = self.state.data
        if n.key in d["shown"] or n.jid in d["muted"]:
            return False
        if n.jid and d["snoozed"].get(n.jid, "") > now.isoformat():
            return False
        last = d["last_chat"].get(n.jid)
        return not (n.jid and last and now - datetime.fromisoformat(last) < timedelta(hours=6))

    # ------------------------------------------------------------------------
    # Steps 3 and 4: the AI check, then pick
    # ------------------------------------------------------------------------

    async def check(self, judge: Judge, events: list[tuple[datetime, datetime, str]] = (),
                    focus: bool = False, gather=None) -> list[Nudge]:
        """What to show now: due event alerts, plus at most one message nudge.
        gather: how to run the (blocking) rules, e.g. asyncio.to_thread; None runs them inline."""
        s, now = self.settings, self.clock()
        if not s.nudges or in_window(s.nudge_quiet, now):
            return []
        self.state.prune(now)
        shown = [n for n in self.event_nudges(list(events), now) if self._allowed(n, now)]
        busy = focus or any(start <= now < end for start, end, _ in events)
        if not busy and self.state.today_count(now) < s.nudge_max_per_day:
            found = await gather(self.candidates, now) if gather else self.candidates(now)
            for n in sorted(found, key=lambda n: not n.important):  # ★ people first
                if not self._allowed(n, now):
                    continue
                verdict = self.state.data["verdicts"].get(n.key)
                if verdict is None and n.evidence:
                    try:
                        verdict = await judge(n)
                    except Exception:
                        verdict = None  # the check failed: try again next time
                    if verdict is not None:
                        self.state.data["verdicts"][n.key] = verdict
                if verdict is False:
                    continue
                if verdict is None and n.evidence:
                    continue  # couldn't check: better silent than silly
                shown.append(n)
                break
        for n in shown:
            self.mark_shown(n, now)
        self.state.save()
        return shown

    # ------------------------------------------------------------------------
    # Bookkeeping, and the card's buttons
    # ------------------------------------------------------------------------

    def mark_shown(self, n: Nudge, now: datetime) -> None:
        d = self.state.data
        d["shown"][n.key] = now.isoformat()
        if n.jid:
            d["last_chat"][n.jid] = now.isoformat()
        if n.kind != "event":
            if d["day"] != now.date().isoformat():
                d["day"], d["count"] = now.date().isoformat(), 0
            d["count"] += 1

    def snooze(self, jid: str, hours: float = 2) -> None:
        self.state.data["snoozed"][jid] = (self.clock() + timedelta(hours=hours)).isoformat()
        self.state.save()

    def mute(self, jid: str) -> None:
        if jid and jid not in self.state.data["muted"]:
            self.state.data["muted"].append(jid)
            self.state.save()


# ----------------------------------------------------------------------------
# The AI check's prompt
#   The message is other people's text: wrapped as untrusted, and the answer is read only as
#   yes / no, so nothing in it can make Ken do anything.
# ----------------------------------------------------------------------------

JUDGE_SYSTEM = ("You decide whether WhatsApp messages need a reply from the user. A question, request, invitation "
                "or something they're waiting on needs one; a greeting already answered, a joke, a link or an "
                "acknowledgement doesn't. Answer with one word: yes or no.")


def judge_answer(text: str) -> bool | None:
    t = (text or "").strip().lower()
    if t.startswith(("yes", "نعم", "اه")):
        return True
    if t.startswith(("no", "لا")):
        return False
    return None
