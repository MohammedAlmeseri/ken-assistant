"""Timers: pomodoro, stopwatch and countdown, each with a name.

Pure logic with an injectable clock, shared by the agent (backend thread, through the
start_timer / control_timer / list_timers tools) and the floating timer bubbles (UI thread,
which call poll() every frame). One lock guards everything.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

# ----------------------------------------------------------------------------
# Modes and phases
# ----------------------------------------------------------------------------

POMODORO, STOPWATCH, COUNTDOWN = "pomodoro", "stopwatch", "countdown"
MODES = (POMODORO, STOPWATCH, COUNTDOWN)
FOCUS, SHORT_BREAK, LONG_BREAK = "focus", "break", "long break"


# ----------------------------------------------------------------------------
# Display helpers
# ----------------------------------------------------------------------------

def clock_text(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


@dataclass
class TimerView:
    """What a bubble draws. progress: 0..1 of the current phase (stopwatch: of the current minute)."""
    text: str
    progress: float
    phase: str  # "focus", "break", "long break", "running", "done"
    round: int = 0
    rounds: int = 0
    done: bool = False


# ----------------------------------------------------------------------------
# One timer
#   Time comes from elapsed seconds (no ticking thread), so pausing is exact.
# ----------------------------------------------------------------------------

@dataclass
class Timer:
    name: str
    mode: str
    duration: float = 0.0  # countdown seconds
    focus: float = 25 * 60
    short_break: float = 5 * 60
    long_break: float = 15 * 60
    rounds: int = 4
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:6])
    running: bool = True
    elapsed_before: float = 0.0  # time run before the last resume
    since: float | None = None  # when it was last resumed (None = paused)
    last_phase: str = ""

    def elapsed(self, now: float) -> float:
        return self.elapsed_before + (now - self.since if self.running and self.since is not None else 0.0)

    def phases(self) -> list[tuple[str, float]]:
        """Pomodoro: focus, break, focus, break, ... focus, long break."""
        out = []
        for i in range(1, self.rounds + 1):
            out.append((FOCUS, self.focus))
            out.append((LONG_BREAK, self.long_break) if i == self.rounds else (SHORT_BREAK, self.short_break))
        return out

    def view(self, now: float) -> TimerView:
        t = self.elapsed(now)
        if self.mode == STOPWATCH:
            return TimerView(clock_text(t), (t % 60) / 60, "running")
        if self.mode == COUNTDOWN:
            left = self.duration - t
            if left <= 0:
                return TimerView("00:00", 1.0, "done", done=True)
            return TimerView(clock_text(left), t / self.duration if self.duration else 1.0, "running")
        focus_round = 0
        for phase, length in self.phases():
            if phase == FOCUS:
                focus_round += 1
            if t < length:
                return TimerView(clock_text(length - t), t / length if length else 1.0, phase, focus_round, self.rounds)
            t -= length
        return TimerView("00:00", 1.0, "done", self.rounds, self.rounds, done=True)

    def describe(self, now: float) -> str:
        v = self.view(now)
        state = "done" if v.done else "running" if self.running else "paused"
        if self.mode == STOPWATCH:
            detail = f"{v.text} elapsed"
        elif self.mode == COUNTDOWN:
            detail = f"{v.text} left of {clock_text(self.duration)}"
        else:
            detail = "finished" if v.done else f"{v.phase} {v.round}/{v.rounds}, {v.text} left"
        return f"'{self.name}' ({self.mode}): {detail}, {state}"


# ----------------------------------------------------------------------------
# All timers
#   Shared by the agent (backend thread) and the timer bubbles (UI thread), with a lock.
# ----------------------------------------------------------------------------

class TimerManager:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.timers: list[Timer] = []
        self.lock = threading.Lock()

    def start(self, mode: str, name: str = "", minutes: float = 0, focus_minutes: float = 25,
              break_minutes: float = 5, long_break_minutes: float = 15, rounds: int = 4) -> Timer:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        if (mode == COUNTDOWN and not minutes) or minutes < 0:
            raise ValueError("a countdown needs minutes > 0")
        default = {POMODORO: "Focus", STOPWATCH: "Stopwatch", COUNTDOWN: "Timer"}[mode]
        timer = Timer(name=(name or default).strip()[:40], mode=mode, duration=minutes * 60,
                      focus=focus_minutes * 60, short_break=break_minutes * 60,
                      long_break=long_break_minutes * 60, rounds=max(1, int(rounds)))
        with self.lock:
            timer.since = self.clock()
            timer.last_phase = timer.view(timer.since).phase
            self.timers.append(timer)
        return timer

    def find(self, name: str = "") -> list[Timer]:
        """By name (case-insensitive, partial); empty name = the newest timer."""
        with self.lock:
            if not name:
                return self.timers[-1:]
            n = name.strip().lower()
            exact = [t for t in self.timers if t.name.lower() == n or t.id == n]
            return exact or [t for t in self.timers if n in t.name.lower()]

    def pause(self, timer: Timer) -> None:
        with self.lock:
            if timer.running:
                timer.elapsed_before = timer.elapsed(self.clock())
                timer.running, timer.since = False, None

    def resume(self, timer: Timer) -> None:
        with self.lock:
            if not timer.running and not timer.view(self.clock()).done:
                timer.running, timer.since = True, self.clock()

    def reset(self, timer: Timer) -> None:
        with self.lock:
            timer.elapsed_before = 0.0
            timer.since = self.clock() if timer.running else None
            timer.last_phase = timer.view(self.clock()).phase

    def stop(self, timer: Timer) -> None:
        with self.lock:
            if timer in self.timers:
                self.timers.remove(timer)

    # ------------------------------------------------------------------------
    # For the agent
    # ------------------------------------------------------------------------

    def control(self, action: str, name: str = "") -> str:
        """For the agent: pause / resume / reset / stop one timer, or 'all'."""
        targets = list(self.timers) if name.strip().lower() == "all" else self.find(name)
        if not targets:
            return "No timer is running." if not self.timers else f"No timer called '{name}'. {self.describe()}"
        handler = {"pause": self.pause, "resume": self.resume, "reset": self.reset, "stop": self.stop}.get(action)
        if handler is None:
            return "Error: action must be pause, resume, reset or stop."
        for t in targets:
            handler(t)
        return f"{action}: " + ", ".join(f"'{t.name}'" for t in targets)

    def describe(self) -> str:
        with self.lock:
            now = self.clock()
            return "\n".join(t.describe(now) for t in self.timers) or "No timers."

    def in_focus(self) -> bool:
        """A running pomodoro is in a focus phase (proactive nudges wait for the break)."""
        with self.lock:
            now = self.clock()
            return any(t.mode == POMODORO and t.running and t.view(now).phase == FOCUS for t in self.timers)

    # ------------------------------------------------------------------------
    # For the UI
    # ------------------------------------------------------------------------

    def poll(self) -> list[tuple[Timer, str]]:
        """Phase changes since the last poll: (timer, new phase). 'done' when a timer finishes."""
        events = []
        with self.lock:
            now = self.clock()
            for t in self.timers:
                v = t.view(now)
                if v.phase != t.last_phase:
                    t.last_phase = v.phase
                    events.append((t, v.phase))
                    if v.done:  # freeze at the end so it doesn't keep counting
                        t.elapsed_before, t.running, t.since = t.elapsed(now), False, None
        return events
