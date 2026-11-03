"""Reminders ("remind me to answer Sara at 6") and chat watches ("tell me when Youssef replies").

Kept in data/reminders.json so they survive a restart. The agent adds them from the backend
thread; the app checks them every few seconds on the UI thread; one lock guards both.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now().astimezone()


def _leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


# ----------------------------------------------------------------------------
# Records
#   Reminders (one-off or yearly) and chat watches, kept in data/reminders.json.
# ----------------------------------------------------------------------------

@dataclass
class Reminder:
    text: str
    at: str  # ISO local time
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:6])
    repeat: str = ""  # "" = once; "yearly:MM-DD" = birthdays and other yearly dates from facts.py


@dataclass
class Watch:
    jid: str
    name: str
    since: str  # ISO: only messages after this count
    until: str  # ISO: stop watching after this
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:6])


# ----------------------------------------------------------------------------
# The store
#   The agent adds from the backend thread, the app checks from the UI thread: one lock.
# ----------------------------------------------------------------------------

class Reminders:
    def __init__(self, path: Path, clock: Callable[[], datetime] = _now):
        self.path = path
        self.clock = clock
        self.lock = threading.Lock()
        self.reminders: list[Reminder] = []
        self.watches: list[Watch] = []
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.reminders = [Reminder(**r) for r in data.get("reminders", [])]
                self.watches = [Watch(**w) for w in data.get("watches", [])]
            except (ValueError, TypeError):
                pass  # a broken file shouldn't stop the app

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"reminders": [asdict(r) for r in self.reminders],
                                         "watches": [asdict(w) for w in self.watches]},
                                        ensure_ascii=False, indent=1), encoding="utf-8")

    # ------------------------------------------------------------------------
    # One-off reminders
    # ------------------------------------------------------------------------

    def remind(self, text: str, at: datetime) -> Reminder:
        if at.tzinfo is None:
            at = at.astimezone()
        if at <= self.clock():
            raise ValueError("that time has already passed")
        r = Reminder(text.strip()[:200], at.isoformat(timespec="minutes"))
        with self.lock:
            self.reminders.append(r)
            self._save()
        return r

    # ------------------------------------------------------------------------
    # Yearly reminders ("Youssef's birthday is 3 May")
    #   Stored like any reminder, at the next time that date comes round. When it fires, due()
    #   moves it on a year instead of deleting it. 29 Feb falls on 28 Feb in other years.
    # ------------------------------------------------------------------------

    def _next_yearly(self, month: int, day: int, hour: int, minute: int, after: datetime) -> datetime:
        for year in (after.year, after.year + 1, after.year + 2):
            d = day if (month, day) != (2, 29) or _leap(year) else 28
            at = after.replace(year=year, month=month, day=d, hour=hour, minute=minute, second=0, microsecond=0)
            if at > after:
                return at
        raise ValueError("no next date")  # unreachable: one of three years is always later

    def remind_yearly(self, text: str, month: int, day: int, hour: int = 10, minute: int = 0) -> Reminder:
        at = self._next_yearly(month, day, hour, minute, self.clock())
        r = Reminder(text.strip()[:200], at.isoformat(timespec="minutes"), repeat=f"yearly:{month:02d}-{day:02d}")
        with self.lock:
            self.reminders.append(r)
            self._save()
        return r

    # ------------------------------------------------------------------------
    # Chat watches ('tell me when X replies')
    # ------------------------------------------------------------------------

    def watch(self, jid: str, name: str, hours: float = 24) -> Watch:
        now = self.clock()
        w = Watch(jid, name.strip()[:60] or jid, now.isoformat(timespec="seconds"),
                  (now + timedelta(hours=max(0.1, min(hours, 24 * 7)))).isoformat(timespec="minutes"))
        with self.lock:
            self.watches = [x for x in self.watches if x.jid != jid]  # one watch per chat
            self.watches.append(w)
            self._save()
        return w

    # ------------------------------------------------------------------------
    # Listing and cancelling
    # ------------------------------------------------------------------------

    def cancel(self, key: str) -> str:
        """By id, or by words in the reminder text / watched name; 'all' clears everything."""
        k = key.strip().lower()
        with self.lock:
            before = len(self.reminders) + len(self.watches)
            if k == "all":
                self.reminders, self.watches = [], []
            else:
                self.reminders = [r for r in self.reminders if r.id != k and k not in r.text.lower()]
                self.watches = [w for w in self.watches if w.id != k and k not in w.name.lower()]
            removed = before - len(self.reminders) - len(self.watches)
            self._save()
        return f"Cancelled {removed}." if removed else f"Nothing matches '{key}'."

    def describe(self) -> str:
        with self.lock:
            lines = [f"[{r.id}] {datetime.fromisoformat(r.at):%a %d %b %H:%M}: remind \"{r.text}\""
                     f"{' (every year)' if r.repeat else ''}" for r in sorted(self.reminders, key=lambda r: r.at)]
            lines += [f"[{w.id}] watching {w.name} for a reply until {datetime.fromisoformat(w.until):%a %H:%M}"
                      for w in self.watches]
        return "\n".join(lines) or "No reminders or watches."

    # ------------------------------------------------------------------------
    # Firing
    #   Polled by the app: due() every 15 s, replies() every minute.
    # ------------------------------------------------------------------------

    def due(self) -> list[Reminder]:
        """Reminders whose time has come. One-off ones are removed once returned; yearly ones move
        on to the same date next year."""
        now = self.clock()
        with self.lock:
            fired = [r for r in self.reminders if datetime.fromisoformat(r.at) <= now]
            if fired:
                self.reminders = [r for r in self.reminders if r not in fired]
                for r in fired:
                    if r.repeat.startswith("yearly:"):
                        month, day = (int(x) for x in r.repeat.split(":")[1].split("-"))
                        at = datetime.fromisoformat(r.at)
                        nxt = self._next_yearly(month, day, at.hour, at.minute, max(now, at))
                        self.reminders.append(Reminder(r.text, nxt.isoformat(timespec="minutes"), r.id, r.repeat))
                self._save()
        return fired

    def replies(self, incoming_since: Callable[[str, datetime], str]) -> list[tuple[Watch, str]]:
        """Watched chats with a new incoming message: (watch, its text). Expired watches are dropped.
        incoming_since(jid, since) returns the latest message from them after `since`, or ""."""
        now = self.clock()
        with self.lock:
            watches = list(self.watches)
        fired, keep = [], []
        for w in watches:
            text = incoming_since(w.jid, datetime.fromisoformat(w.since))
            if text:
                fired.append((w, text))
            elif datetime.fromisoformat(w.until) > now:
                keep.append(w)
        if len(keep) != len(watches):
            with self.lock:
                ids = {w.id for w in keep}
                self.watches = [w for w in self.watches if w.id in ids or w not in watches]
                self._save()
        return fired
