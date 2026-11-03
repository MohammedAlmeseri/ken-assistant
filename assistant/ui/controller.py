"""Runs the backend (agent, MCP servers, voice) on its own asyncio thread and talks to
the Qt UI through signals. Qt signals emitted from that thread are queued onto the UI
thread automatically, so widgets are only ever touched from the UI thread.

Why a thread and not a Qt-integrated asyncio loop: the Gmail server is a stdio
subprocess, and subprocess support in Qt/asyncio bridges on Windows is unreliable."""

from __future__ import annotations

import asyncio
import concurrent.futures
import re
import socket
import subprocess
import sys
import threading
from contextlib import AsyncExitStack
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from ..agent import Agent, Draft, EventProposal, SendRequest, TurnResult
from ..calendar_mcp import TOKEN as CALENDAR_TOKEN
from ..config import DATA_DIR, PROJECT_ROOT, Settings, save_settings
from ..llm import OpenAIBackend, Usage, make_backend, usage_line
from ..contacts import ChatDirectory, MockDirectory
from ..facts import Facts
from ..inbox import InboxItem, InboxState, parse_gmail_search, whatsapp_new
from ..mcp_client import MCPManager
from ..memory import learn_history, make_memory
from ..nudges import JUDGE_SYSTEM, Nudge, NudgeEngine, judge_answer, parse_events
from ..openai_client import requests_today
from ..reminders import Reminders
from ..shortcuts import Shortcut, Shortcuts, resolve as resolve_shortcut, split_words
from ..timers import TimerManager
from ..voicenotes import TranscriptCache, VoiceNotes

# ----------------------------------------------------------------------------
# Paths, word lists and constants
#   STOP_WORDS end a request ('Ken, stop'), CLOSE_WORDS end a follow-up window, WAKE_PREFIX strips
#   'Ken,' from the start of a transcript.
# ----------------------------------------------------------------------------

WHATSAPP_EXE = PROJECT_ROOT / "whatsapp-mcp" / "bin" / "whatsapp-mcp.exe"
WHATSAPP_STORE = PROJECT_ROOT / "whatsapp-mcp" / "store"
# Said right after "Ken" to just make him stop talking (nothing is sent to the model).
STOP_WORDS = re.compile(r"^\s*(please\s+)?(stop( it| talking)?|quiet|be quiet|shut up|enough|that'?s enough|cancel|"
                        r"never ?mind|ok(ay)?|اسكت|خلاص|بس|كفاية|كفايه|وقف|استنى)([\s,.!?،؟]+(يا\s+)?(please|sir|ken|كين))?"
                        r"[\s,.!?،؟]*$", re.IGNORECASE)
# Said in a follow-up window to end the conversation: closes it quietly, no AI call.
CLOSE_WORDS = re.compile(r"^\s*(ok(ay)?[\s,]+)?(thanks?( you)?|thank you( ken| sir)?|that'?s (all|it)|no thanks|"
                         r"nothing|all good|cheers|شكرا|شكراً|متشكر|تسلم|خلاص كده|لا شكرا|مفيش)[\s,.!?،؟]*"
                         r"(ken|كين)?[\s,.!?،؟]*$", re.IGNORECASE)
WAKE_PREFIX = re.compile(r"^\s*(hey[\s,]+)?(ken|كين)(?=[\s,.!?،]|$)[\s,.!?،]*", re.IGNORECASE)
WHATSAPP_ADDR = ("127.0.0.1", 8765)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def port_open(addr: tuple[str, int], timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection(addr, timeout=timeout):
            return True
    except OSError:
        return False


# ----------------------------------------------------------------------------
# The controller
#   Runs the agent, MCP servers and voice on their own asyncio thread and talks to the UI only
#   through Qt signals (queued onto the UI thread), so no widget is touched from another thread.
# ----------------------------------------------------------------------------

class Controller(QObject):
    status = Signal(str)  # short line like "Reading WhatsApp…", "" when idle
    busy = Signal(bool)
    reply = Signal(object)  # TurnResult
    notice = Signal(str, str)  # kind ("info" | "error"), text
    confirm_requested = Signal(object, object)  # SendRequest, concurrent.futures.Future[bool]
    draft_sent = Signal(object, object)  # Draft, TurnResult
    usage = Signal(str)
    connections = Signal(dict)  # {"gmail": True, "whatsapp": False, ...}
    transcript = Signal(str)
    listening = Signal(bool)
    inbox = Signal(list)  # list[InboxItem] for the "what's new" popup
    unread_count = Signal(int)  # for the red dot on the bell and the pill
    quick_chats = Signal(list)  # list[ChatRef]: the people you message most lately
    event_added = Signal(object, bool, str)  # EventProposal, ok, result text
    reminder_fired = Signal(str, str, str)  # title, body, line to say aloud
    wake_heard = Signal()  # "Ken" was said: now recording the request
    wake_ended = Signal(bool)  # the request was recorded (True) or nothing followed (False)
    follow_up = Signal(float)  # a follow-up window opened (seconds) or closed (0)
    nudge = Signal(object)  # Nudge: Ken speaks up on his own (nudges.py)
    shortcut_suggestions = Signal(list)  # list[ShortcutProposal]: "Mom", "ماما"… offered once

    def __init__(self, settings: Settings, mock: bool = False):
        super().__init__()
        self.settings = settings
        self.mock = mock
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="assistant-backend", daemon=True)
        self._stack = AsyncExitStack()
        self._wa_process: subprocess.Popen | None = None
        self.agent: Agent | None = None
        self.tools = None
        self.directory = None
        self.inbox_state = InboxState(DATA_DIR / "inbox_state.json")
        self._gmail_new: list[InboxItem] = []  # last Gmail poll result, reused between Gmail polls
        self._mock_seen = False
        self.speech = None
        self.session = None
        self.recorder = None
        self.transcriber = None
        self.wake = None  # WakeListener while "Ken" listening is on
        self.timers = TimerManager()  # shared with the bubbles on the UI thread
        self.reminders = Reminders(DATA_DIR / "reminders.json")
        self.shortcuts = Shortcuts(DATA_DIR / ("shortcuts-mock.json" if mock else "shortcuts.json"))  # "mom" -> her chat
        self.facts: Facts | None = None  # "remember that…", made at startup (needs the memory)
        self.nudges: NudgeEngine | None = None  # proactive nudges, made at startup (needs the directory)
        self._barged_in = False  # "Ken" was said while Ken was talking: skip the greeting
        self.session_usage = Usage()
        self.stt_seconds = 0.0  # audio transcribed by OpenAI this session (survives transcriber rebuilds)
        self.stt_cost = 0.0
        # Shared with the speech engine and read on every sentence, so changes apply at once.
        self.tts_prefs = {
            "en": settings.tts_voice_en, "ar": settings.tts_voice_ar, "rate": settings.tts_rate,
            "engine": settings.tts_engine, "openai_voice": settings.tts_openai_voice,
            "openai_model": settings.tts_openai_model, "tone": settings.tone, "persona": settings.persona,
            "edge_en": settings.edge_voice_en, "edge_ar": settings.edge_voice_ar,
        }
        self._current: concurrent.futures.Future | None = None  # the running reply, for Stop

    # ------------------------------------------------------------------------
    # Lifecycle and startup
    #   Starts the WhatsApp server if needed, connects the MCP servers, builds memory, facts, voice notes,
    #   nudges and the agent. A server that fails is reported; everything else keeps working.
    # ------------------------------------------------------------------------

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro) -> concurrent.futures.Future:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def start(self) -> None:
        self._thread.start()
        self.submit(self._startup())

    def shutdown(self, timeout: float = 5.0) -> None:
        if not self._thread.is_alive():
            return
        try:
            self.submit(self._stack.aclose()).result(timeout=timeout)
        except Exception:
            pass
        if self._wa_process and self._wa_process.poll() is None:
            self._wa_process.terminate()
        if self.speech:
            self.speech.stop()
        self.stop_wake()
        self.loop.call_soon_threadsafe(self.loop.stop)

    async def _startup(self) -> None:
        self.busy.emit(True)
        self.status.emit("Connecting…")
        try:
            if self.mock:
                from ..mock_tools import MockToolSource

                tools = MockToolSource()
                conn = {"gmail": True, "whatsapp": True}
            else:
                if self.settings.autostart_whatsapp:
                    await self._ensure_whatsapp()
                tools = await self._stack.enter_async_context(MCPManager(self.settings.mcp_config_path))
                conn = {name: name in tools.clients for name in tools.servers}
                if conn.get("calendar") and not CALENDAR_TOKEN.exists():
                    conn["calendar"] = False
                    self.notice.emit("info", "Google Calendar isn't connected yet: Settings → Connect Google Calendar.")
                for name, err in tools.errors.items():
                    self.notice.emit("error", f"{name.capitalize()} isn't connected: {err}")
            self.tools = tools
            memory = make_memory(DATA_DIR / ("memory-mock.db" if self.mock else "memory.db"), self.settings.backend)
            self.directory = MockDirectory() if self.mock else ChatDirectory(WHATSAPP_STORE)
            # "Remember that…": facts live in the same memory.db; birthdays become yearly reminders.
            self.facts = Facts(memory, self.directory, self.reminders, self.settings.birthday_time)
            # Voice notes, transcribed only on request; cached transcripts also show in chat history.
            voice_notes = None
            if not self.mock and conn.get("whatsapp"):
                voice_notes = VoiceNotes(WHATSAPP_STORE, tools, self._transcribe_note,
                                         TranscriptCache(DATA_DIR / "voice_notes.db"))
                self.directory.transcripts = voice_notes.cache
            self.nudges = NudgeEngine(self.directory, self.settings, DATA_DIR / "nudges.json")
            self.agent = Agent(make_backend(self.settings), tools, self.settings, self._confirm, self._on_status,
                               memory, self.directory, timers=self.timers, reminders=self.reminders,
                               facts=self.facts, voice_notes=voice_notes, shortcuts=self.shortcuts)
            await self.agent.start()
            await self._suggest_shortcuts()
            asyncio.get_running_loop().create_task(self._index_docs(memory))
            await self._refresh_quick_chats()
            self.connections.emit(conn)
            if not self.mock and conn.get("whatsapp"):
                await self._check_whatsapp_paired(tools)
            if self.settings.speak_replies:
                self._ensure_speech()
        except Exception as exc:
            self.notice.emit("error", f"Startup failed: {type(exc).__name__}: {exc}")
        finally:
            self.status.emit("")
            self.busy.emit(False)

    # ------------------------------------------------------------------------
    # Shortcuts ("mom" -> her chat)
    #   At startup, contacts saved under a family word ("Mom", "ماما", "Dad"…) are offered once as
    #   cards. Saving always comes from a click: a card's Save, Settings, or the person card.
    # ------------------------------------------------------------------------

    async def _suggest_shortcuts(self) -> None:
        if self.directory is None or not hasattr(self.directory, "contacts"):
            return
        try:
            found = self.shortcuts.suggestions(await asyncio.to_thread(self.directory.contacts))
        except Exception as exc:
            print(f"  \u26a0  couldn't look for shortcut suggestions: {exc}")
            return
        if found:
            self.shortcuts.mark_offered([p.shortcut.target for p in found])  # offered once, saved or not
            self.shortcut_suggestions.emit(found)

    def save_shortcut(self, shortcut: Shortcut) -> Shortcut:
        return self.shortcuts.save(shortcut)

    def remove_shortcut(self, shortcut_id: str) -> None:
        self.shortcuts.remove(shortcut_id)

    def add_shortcut(self, words_text: str, who: str) -> tuple[Shortcut | None, str]:
        """Settings → Shortcuts → Add (the click there is the confirmation). Returns (saved, why not)."""
        shortcut, why = resolve_shortcut(split_words(words_text), who, self.directory)
        return (self.shortcuts.save(shortcut), "") if shortcut else (None, why)

    def shortcut_suggestions_now(self) -> list:
        """For Settings: family-looking contacts without a shortcut yet (even if offered before)."""
        try:
            return self.shortcuts.suggestions(self.directory.contacts(), include_offered=True) if self.directory else []
        except Exception:
            return []

    async def _index_docs(self, memory) -> None:
        """Ken's own docs as RAG for the about_ken tool; only re-embedded when the files change."""
        from ..knowledge import ensure_index

        try:
            n = await ensure_index(memory)
            if n:
                print(f"  indexed {n} sections of Ken's docs")
        except Exception as exc:
            print(f"  ⚠  couldn't index Ken's docs: {type(exc).__name__}: {exc}")

    async def _ensure_whatsapp(self) -> None:
        if port_open(WHATSAPP_ADDR) or not WHATSAPP_EXE.exists():
            return
        self.status.emit("Starting WhatsApp…")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        log = open(DATA_DIR / "whatsapp-server.log", "ab")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._wa_process = subprocess.Popen(
            [str(WHATSAPP_EXE), "-store", str(WHATSAPP_STORE), "serve"],
            stdout=log, stderr=subprocess.STDOUT, creationflags=flags, cwd=str(WHATSAPP_EXE.parent.parent),
        )
        for _ in range(60):  # up to ~15 s
            if port_open(WHATSAPP_ADDR):
                return
            await asyncio.sleep(0.25)
        self.notice.emit("error", "The WhatsApp server didn't start. See data/whatsapp-server.log.")

    async def _check_whatsapp_paired(self, tools: MCPManager) -> None:
        try:
            status = await tools.call_tool("whatsapp__get_status", {})  # hidden from the model; app-only
        except Exception:
            return
        if '"paired": false' in status:
            self.notice.emit("error", "WhatsApp isn't linked. Open http://127.0.0.1:8765/pair and scan the QR code.")

    # ------------------------------------------------------------------------
    # Agent bridge
    #   ask() runs one turn; Stop cancels it; the confirmation dialog is awaited through a Future.
    # ------------------------------------------------------------------------

    def _on_status(self, text: str) -> None:
        self.status.emit(text)

    async def _confirm(self, request: SendRequest) -> bool:
        future: concurrent.futures.Future = concurrent.futures.Future()
        self.confirm_requested.emit(request, future)
        return await asyncio.wrap_future(future)

    def ask(self, text: str, spoken: bool = False) -> None:
        self._current = self.submit(self._ask(text, spoken))

    def stop(self) -> None:
        """Stop button: cancel the running reply (the agent rolls its history back) and the voice."""
        self.stop_speaking()
        if self._current is not None and not self._current.done():
            self._current.cancel()  # cancels the asyncio task on the backend loop
            self.notice.emit("info", "Stopped.")
        self.status.emit("")
        self.busy.emit(False)

    async def _ask(self, text: str, spoken: bool = False) -> None:
        if self.agent is None:
            self.notice.emit("error", "Still connecting, try again in a moment.")
            return
        self.busy.emit(True)
        try:
            speak = self.settings.speak_replies or (spoken and self.settings.speak_after_voice)
            if speak:
                if self.session is None:
                    self._ensure_speech()
                result = await self.session.handle(text, speak=True)  # also asks the model to be very short
            else:
                result = await self.agent.run(text)
            self.reply.emit(result)
            self._account(result)
            if spoken and speak:  # only answers to spoken requests open a follow-up window
                asyncio.get_running_loop().create_task(self._follow_up_after_speech())
        except Exception as exc:
            self.notice.emit("error", f"{type(exc).__name__}: {exc}")
        finally:
            self.status.emit("")
            self.busy.emit(False)

    # ------------------------------------------------------------------------
    # Follow-up window
    #   Waits (without blocking anything) until Ken has finished saying the answer, then asks the
    #   wake listener to keep listening for settings.follow_up_seconds. Nothing happens when
    #   "Listen for Ken" is off, the setting is 0, or Ken never got to speak (e.g. stopped).
    # ------------------------------------------------------------------------

    async def _follow_up_after_speech(self) -> None:
        seconds = float(self.settings.follow_up_seconds or 0)
        if seconds <= 0 or self.wake is None:
            return
        await asyncio.sleep(0.3)  # the reply is queued for speech a moment after it's returned
        for _ in range(1200):  # up to 2 minutes of talking
            if not self.speaking:
                break
            await asyncio.sleep(0.1)
        else:
            return
        await asyncio.sleep(0.25)  # let the speaker's last syllable die away
        if self.wake is not None:
            self.wake.follow_up(seconds)

    def send_draft(self, draft: Draft, text: str) -> None:
        self._current = self.submit(self._send_draft(draft, text))

    async def _send_draft(self, draft: Draft, text: str) -> None:
        self.busy.emit(True)
        try:
            result = await self.agent.send_draft(draft, text)
            self.draft_sent.emit(draft, result)
        except Exception as exc:
            self.notice.emit("error", f"Send failed: {type(exc).__name__}: {exc}")
        finally:
            self.status.emit("")
            self.busy.emit(False)

    # ------------------------------------------------------------------------
    # Calendar
    # ------------------------------------------------------------------------

    def add_event(self, event: EventProposal) -> None:
        self.submit(self._add_event(event))

    async def _add_event(self, event: EventProposal) -> None:
        text = await self.agent.add_event(event) if self.agent else "Tool error: still connecting."
        ok = not text.startswith(("Tool error", "Error"))
        self.event_added.emit(event, ok, text)
        if not ok:
            self.notice.emit("error", f"Couldn't add the event: {text.removeprefix('Tool error: ')}")

    def connect_calendar(self) -> subprocess.Popen:
        """Opens the browser for Google's consent screen; the app reconnects after a restart."""
        return subprocess.Popen([sys.executable.replace("pythonw.exe", "python.exe"),
                                 str(PROJECT_ROOT / "assistant" / "calendar_mcp.py"), "--auth"],
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    # ------------------------------------------------------------------------
    # ☀ Briefing
    # ------------------------------------------------------------------------

    def brief(self, speak_opening: bool = False) -> None:
        self._current = self.submit(self._brief(speak_opening))

    async def _brief(self, speak_opening: bool = False) -> None:
        if self.agent is None:
            self.notice.emit("error", "Still connecting, try again in a moment.")
            return
        self.busy.emit(True)
        try:
            self.status.emit("Gathering your messages and calendar…")
            prompt = await self._briefing_prompt()
            self.status.emit("Summarizing…")
            result = await self.agent.run(prompt)
            self.reply.emit(result)
            self._account(result)
            if speak_opening:  # the morning briefing greets you with its first line
                self.say(result.text.strip().splitlines()[0] if result.text.strip() else "")
        except Exception as exc:
            self.notice.emit("error", f"Briefing failed: {type(exc).__name__}: {exc}")
        finally:
            self.status.emit("")
            self.busy.emit(False)

    async def _briefing_prompt(self) -> str:
        from datetime import datetime, timedelta, timezone

        from ..briefing import build_prompt

        clients = getattr(self.tools, "clients", {}) if not self.mock else {"gmail": 1}
        calendar = ""
        if "calendar" in clients and CALENDAR_TOKEN.exists():
            calendar = await self.tools.call_tool("calendar__list_events", {"start": "today", "days": 2})
        if self.mock:
            items = await self._collect_inbox(include_gmail=True)
        else:
            since = datetime.now(timezone.utc) - timedelta(hours=24)
            items = await asyncio.to_thread(whatsapp_new, WHATSAPP_STORE, since, 20)
        chats = []
        for item in [i for i in items if i.channel == "whatsapp"][:12]:
            label = item.group or item.sender_name or f"+{item.sender}"
            convo = await asyncio.to_thread(self.directory.recent_conversation, item.chat_jid, hours=24,
                                            max_msgs=10, max_chars=900)
            chats.append((label, convo or f"[{item.time}] Them: {item.text}"))
        emails = [f"[{i.time}] From {i.sender_name or i.sender}: {i.text}" for i in items if i.channel == "gmail"]
        if not self.mock and "gmail" in clients:
            raw = await self.tools.call_tool("gmail__search_emails", {
                "query": "newer_than:1d -category:promotions -category:social -category:forums", "maxResults": 15})
            emails = [f"[{i.time}] From {i.sender_name or i.sender} <{i.sender}>: {i.text}" for i in parse_gmail_search(raw)]
        today = [f.text for f in self.facts.on_date(datetime.now().date())] if self.facts else []
        return build_prompt(self.settings.tone, calendar, chats, emails, self.settings.persona, today)

    # ------------------------------------------------------------------------
    # "What's new" (no AI)
    #   The red dot and the inbox popup read the WhatsApp store and unread Gmail; nothing goes to a model.
    # ------------------------------------------------------------------------

    def check_inbox(self) -> None:
        self.submit(self._check_inbox())

    async def _check_inbox(self) -> None:
        self.status.emit("Checking for new messages…")
        items: list[InboxItem] = []
        try:
            items = await self._collect_inbox(include_gmail=True)
        except Exception as exc:
            self.notice.emit("error", f"Couldn't check messages: {exc}")
        finally:
            self.status.emit("")
        self.inbox.emit(items)

    async def _collect_inbox(self, include_gmail: bool) -> list[InboxItem]:
        if self.mock:
            if self._mock_seen:
                return []
            return [InboxItem("whatsapp", "Brother", "201001234567", "Are you free Friday?", "09:12",
                              chat_jid="201001234567@s.whatsapp.net"),
                    InboxItem("whatsapp", "Ali", "201005550000", "lecture tomorrow?", "10:40",
                              chat_jid="1203630-1600@g.us", group="Uni friends", more=2),
                    InboxItem("gmail", "Sara Ahmed", "sara@example.com", "Project meeting moved", "11:02", email_id="m1")]
        items = await asyncio.to_thread(whatsapp_new, WHATSAPP_STORE, self.inbox_state.whatsapp_since)
        if include_gmail and "gmail" in getattr(self.tools, "clients", {}):
            raw = await self.tools.call_tool("gmail__search_emails", {"query": "is:unread newer_than:3d", "maxResults": 10})
            self._gmail_new = [i for i in parse_gmail_search(raw) if i.email_id not in self.inbox_state.seen_emails]
        return items + [i for i in self._gmail_new if i.email_id not in self.inbox_state.seen_emails]

    def poll_inbox(self, include_gmail: bool) -> None:
        """Background check for the red dot. No AI, nothing marked as seen."""
        if self.agent is not None:  # wait until connected
            self.submit(self._poll_inbox(include_gmail))

    async def _poll_inbox(self, include_gmail: bool) -> None:
        await self._refresh_quick_chats()
        try:
            items = await self._collect_inbox(include_gmail)
        except Exception:
            return  # a failed background check stays quiet; the 🔔 button reports errors
        self.unread_count.emit(sum(1 + i.more for i in items))
        await self._check_watches()

    async def _check_watches(self) -> None:
        """"Tell me when X replies": fire for watched chats with a new message from them."""
        if self.directory is None or not hasattr(self.directory, "incoming_since"):
            return
        for watch, text in await asyncio.to_thread(self.reminders.replies, self.directory.incoming_since):
            arabic = any("\u0600" <= ch <= "\u06ff" for ch in watch.name)
            butler = self.settings.persona == "butler"
            spoken = (f"Sir، {watch.name} رد عليك." if butler else f"{watch.name} رد عليك.") if arabic else \
                (f"Sir, {watch.name} just replied." if butler else f"{watch.name} just replied.")
            self.reminder_fired.emit(f"💬 {watch.name} replied", text[:160], spoken)

    # ------------------------------------------------------------------------
    # Proactive nudges (every 10 minutes, from the app's timer)
    #   The engine applies the rules and limits; this side supplies what it can't know by itself:
    #   today's calendar (one MCP call, no AI), whether a Pomodoro focus phase is running, and the
    #   tiny AI check. Each nudge is handed to the UI as a card (and spoken for ★ people).
    # ------------------------------------------------------------------------

    def check_nudges(self) -> None:
        if self.agent is not None and self.nudges is not None:
            self.submit(self._check_nudges())

    async def _check_nudges(self) -> None:
        try:
            events = []
            if "calendar" in getattr(self.tools, "clients", {}) and CALENDAR_TOKEN.exists():
                raw = await self.tools.call_tool("calendar__list_events", {"start": "today", "days": 1})
                events = parse_events(raw, datetime.now().astimezone())
            for n in await self.nudges.check(self._judge, events, focus=self.timers.in_focus(),
                                             gather=asyncio.to_thread):
                self.nudge.emit(n)
        except Exception as exc:
            print(f"  \u26a0  nudge check failed: {type(exc).__name__}: {exc}")  # background: stay quiet

    async def _judge(self, n: Nudge) -> bool | None:
        """One small request: does this need a reply? Logged as "nudge" in the request log."""
        from ..safety import wrap_untrusted

        if self.settings.backend != "openai":
            return True  # the local model is too slow for background checks: trust the rules
        if not hasattr(self, "_judge_backend"):
            self._judge_backend = OpenAIBackend(self.settings, model=self.settings.ack_model, purpose="nudge")
        before = Usage() + self._judge_backend.usage
        reply = await self._judge_backend.chat(
            [{"role": "system", "content": JUDGE_SYSTEM},
             {"role": "user", "content": "Messages from " + n.name + ":\n" + wrap_untrusted(n.evidence, source="whatsapp")}],
            [])
        self.session_usage = self.session_usage + (self._judge_backend.usage - before)
        self.usage.emit(self.usage_text())
        return judge_answer(reply.text)

    def snooze_nudge(self, n: Nudge, hours: float = 2) -> None:
        if self.nudges is not None and n.jid:
            self.nudges.snooze(n.jid, hours)

    def mute_nudges(self, jid: str) -> None:
        if self.nudges is not None:
            self.nudges.mute(jid)

    def toggle_important(self, jid: str) -> bool:
        """★ on a person card. Returns whether they're important now."""
        people = list(self.settings.important_people)
        on = jid not in people
        people = people + [jid] if on else [j for j in people if j != jid]
        self.apply_settings({"important_people": people})
        return on

    def watch_chat(self, jid: str, name: str) -> None:
        """The person card's Watch button: same as "tell me when X replies", without the model."""
        w = self.reminders.watch(jid, name)
        self.notice.emit("info", f"Watching {w.name}: I'll tell you when they write.")

    # ------------------------------------------------------------------------
    # Saved facts, for Settings → Memory
    # ------------------------------------------------------------------------

    def list_facts(self) -> list:
        return self.facts.all() if self.facts is not None else []

    def delete_fact(self, fact) -> None:
        if self.facts is not None:
            self.facts.delete(fact)

    def say(self, text: str) -> None:
        """Speak a line outside a conversation turn (reminders, the morning briefing)."""
        if text.strip():
            if self.speech is None:
                self._ensure_speech()
            self.speech.say(text)

    async def _refresh_quick_chats(self) -> None:
        try:
            self.quick_chats.emit(await asyncio.to_thread(self.directory.frequent, 6))
        except Exception:
            pass  # the bar just keeps its previous chats

    def mark_inbox_seen(self, items: list[InboxItem]) -> None:
        if self.mock:
            self._mock_seen = True
        else:
            self.inbox_state.mark_seen(items)
        self.unread_count.emit(0)

    # ------------------------------------------------------------------------
    # Learning from WhatsApp history
    # ------------------------------------------------------------------------

    def learn_history(self) -> None:
        self.submit(self._learn_history())

    async def _learn_history(self) -> None:
        if self.agent is None or self.agent.memory is None or self.directory is None:
            self.notice.emit("error", "Still connecting, try again in a moment.")
            return
        self.busy.emit(True)
        try:
            added, chats = await learn_history(
                self.directory, self.agent.memory,
                progress=lambda done, total, name: self.status.emit(
                    f"Learning from your chats {done}/{total}{' · ' + name if name else ''}"))
            self.notice.emit("info", f"Learned {added:,} new reply examples from {chats} chats. "
                                     "Drafts now copy how you answer people.")
        except Exception as exc:
            self.notice.emit("error", f"Couldn't learn from history: {type(exc).__name__}: {exc}")
        finally:
            self.status.emit("")
            self.busy.emit(False)

    def reset(self) -> None:
        if self.agent:
            self.loop.call_soon_threadsafe(self.agent.reset)

    def _account(self, result: TurnResult) -> None:
        self.session_usage = self.session_usage + result.usage + result.ack_usage
        self.usage.emit(self.usage_text())

    def usage_text(self) -> str:
        backend = self.agent.backend if self.agent else None
        tts = getattr(getattr(self.speech, "_engine", None), "_openai", None)
        tts_seconds = tts.audio_seconds if tts else 0.0
        tts_price = self.settings.audio_prices.get(self.tts_prefs.get("openai_model") or "gpt-4o-mini-tts", 0.0)
        return usage_line(self.session_usage, self.settings.prices.get(getattr(backend, "model", "")),
                          local=getattr(backend, "name", "") == "local",
                          voice_seconds=self.stt_seconds + tts_seconds,
                          voice_cost=self.stt_cost + tts_seconds / 60 * tts_price,
                          requests_today=requests_today())

    # ------------------------------------------------------------------------
    # Voice
    #   The speech queue, the push-to-talk recorder, and one speech-to-text path with cost accounting.
    # ------------------------------------------------------------------------

    def _ensure_speech(self) -> None:
        from ..voice.session import VoiceSession
        from ..voice.tts import SpeechQueue, default_engine

        if self.speech is None:
            self.speech = SpeechQueue(lambda: default_engine(self.tts_prefs))
        ack = None
        if self.settings.backend == "openai" and self.settings.ack_style == "model":
            ack = OpenAIBackend(self.settings, model=self.settings.ack_model, purpose="ack")
        self.session = VoiceSession(self.agent, self.speech, self.settings, ack)

    @property
    def speaking(self) -> bool:
        return bool(self.speech and self.speech.speaking)

    def stop_speaking(self) -> None:
        if self.speech:
            self.speech.stop()

    def start_listening(self) -> bool:
        from ..voice.stt import Recorder

        try:
            if self.recorder is None:
                self.recorder = Recorder()
            self.stop_speaking()  # barge-in
            self.recorder.start()
        except Exception as exc:
            self.notice.emit("error", f"Microphone error: {exc}")
            return False
        self.listening.emit(True)
        return True

    def mic_level(self) -> float:
        if self.wake is not None and self.wake.capturing:
            return self.wake.level
        return self.recorder.level if self.recorder else 0.0

    # ------------------------------------------------------------------------
    # Wake word ("Ken")
    # ------------------------------------------------------------------------

    def start_wake(self, paused) -> str:
        """Start listening for "Ken". paused() says when not to (busy, recording, speaking).
        Returns "" when listening, else why not."""
        from ..voice.greeting import Greeter
        from ..voice.wake import WakeListener, WakeModel
        from .wake_dialog import MODEL_PATH

        self.stop_wake()
        if not MODEL_PATH.exists():
            return "Ken hasn't learned your voice yet."
        greeter = Greeter(self.tts_prefs, DATA_DIR / "voice")
        greeter.warm()
        try:
            self.wake = WakeListener(WakeModel.load(MODEL_PATH), on_wake=self._on_wake_word,
                                     on_audio=self._on_wake_audio, paused=paused,
                                     greet=lambda: None if self._barged_in else greeter.play(),
                                     on_follow=self.follow_up.emit)
            self.wake.start()
        except Exception as exc:
            self.wake = None
            return f"Couldn't start listening: {type(exc).__name__}: {exc}"
        return ""

    def preview_voice(self, prefs: dict, text: str) -> None:
        """Settings ▶ Preview: say a sample line in that voice and personality (cached per voice)."""
        from ..voice.greeting import Greeter

        threading.Thread(target=Greeter(prefs, DATA_DIR / "voice", text).play, daemon=True).start()

    def stop_wake(self) -> None:
        if self.wake is not None:
            self.wake.stop()
            self.wake = None

    def _on_wake_word(self) -> None:
        """On the wake-word thread. While Ken is talking, his name cuts him off ("Ken, stop") and he
        listens without saying "At your service" first."""
        self._barged_in = self.speaking
        self.stop_speaking()
        self.wake_heard.emit()

    def _on_wake_audio(self, audio, follow_up: bool = False) -> None:
        """On the wake-word thread: hand the recorded request to the normal voice path.
        follow_up: it came from a follow-up window (no "Ken" was said, nothing heard is fine)."""
        if not follow_up:
            self.wake_ended.emit(audio is not None)
        if audio is not None:
            self.submit(self._transcribe(audio, strip_wake=True, follow_up=follow_up))

    def stop_listening(self) -> None:
        if self.recorder is None:
            return
        audio = self.recorder.stop()
        self.listening.emit(False)
        self.submit(self._transcribe(audio))

    async def _stt(self, audio) -> str:
        """Speech to text with cost accounting: the owner's requests AND voice notes go through here,
        so both show in the panel's cost counter."""
        from ..voice.stt import make_transcriber

        if self.transcriber is None:
            self.transcriber = make_transcriber(self.settings)
        before = getattr(self.transcriber, "audio_seconds", 0.0)
        text, _ = await self.transcriber.transcribe(audio)  # local Whisper loads on first use
        spent = getattr(self.transcriber, "audio_seconds", 0.0) - before  # 0 if local or it fell back
        if spent:
            self.stt_seconds += spent
            self.stt_cost += spent / 60 * self.settings.audio_prices.get(self.settings.openai_stt_model, 0.0)
            self.usage.emit(self.usage_text())
        return text

    async def _transcribe_note(self, audio) -> str:
        """VoiceNotes calls this (on the backend loop) for each note it has to transcribe."""
        return await self._stt(audio)

    async def _transcribe(self, audio, strip_wake: bool = False, follow_up: bool = False) -> None:
        self.status.emit("Listening…")
        try:
            text = await self._stt(audio)
        except Exception as exc:
            self.notice.emit("error", f"Couldn't transcribe: {exc}")
            text = ""
        finally:
            self.status.emit("")
        if strip_wake:  # "Ken, what's new?" -> "what's new?"
            text = WAKE_PREFIX.sub("", text).strip()
            if STOP_WORDS.match(text):  # "Ken, stop": he already stopped talking; nothing to send
                return
        if follow_up and (not text or CLOSE_WORDS.match(text)):
            return  # "thanks" / nothing understood: the conversation just ends, no AI call
        self.transcript.emit(text)

    # ------------------------------------------------------------------------
    # Settings
    #   Saves changes and updates the live objects (voices, backend, transcriber) without a restart.
    # ------------------------------------------------------------------------

    def apply_settings(self, changes: dict) -> None:
        """Called by the settings window: save, update the live objects, rebuild what changed."""
        save_settings(changes)
        for key, value in changes.items():
            setattr(self.settings, key, value)
        prefs_map = {"tts_voice_en": "en", "tts_voice_ar": "ar", "tts_rate": "rate", "tts_engine": "engine",
                     "tts_openai_voice": "openai_voice", "tone": "tone", "persona": "persona",
                     "edge_voice_en": "edge_en", "edge_voice_ar": "edge_ar"}
        for key, pref in prefs_map.items():
            if key in changes:
                self.tts_prefs[pref] = changes[key]
        if {"backend", "stt_backend"} & changes.keys():
            self.transcriber = None
        if "birthday_time" in changes and self.facts is not None:
            self.facts.reminder_time = changes["birthday_time"]  # for facts saved from now on
        if changes.get("follow_up_seconds") == 0 and self.wake is not None:
            self.wake.cancel_follow_up()
        if "backend" in changes and self.agent:
            self.submit(self._swap_backend())
        if self.agent and (self.session is not None or self.settings.speak_replies):
            self.loop.call_soon_threadsafe(self._ensure_speech)  # rebuild with the new ack/voice settings

    async def _swap_backend(self) -> None:
        try:
            self.agent.backend = make_backend(self.settings)
            self.notice.emit("info", f"Model: {self.agent.backend.name} ({self.agent.backend.model})")
        except Exception as exc:
            self.notice.emit("error", f"Couldn't switch model: {exc}")

    def installed_voices(self) -> list[tuple[str, str]]:
        try:
            from winrt.windows.media.speechsynthesis import SpeechSynthesizer

            return [(v.display_name, v.language) for v in SpeechSynthesizer.all_voices]
        except Exception:
            return []
