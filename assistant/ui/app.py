"""Desktop app entry point: wires the pill, panel, tray, hotkey and backend controller.

    python -m assistant.ui            # real Gmail + WhatsApp
    python -m assistant.ui --mock     # fake data (still uses the real model)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import sys
import threading
import time
from datetime import datetime

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QAction
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QSystemTrayIcon

from ..agent import Draft, SendRequest, TurnResult
from ..config import DATA_DIR, load_settings
from ..web import open_in_browser
from . import theme
from .controller import Controller
from .dialogs import ConfirmSendDialog, InboxDialog, SettingsDialog
from .hotkey import Hotkey, pretty
from .nudge_toast import NudgeToast
from .panel import Panel
from .pill import IDLE, LISTENING, SPEAKING, WORKING, Pill
from .timer_widget import TimerBoard, chime
from .wake_dialog import MODEL_PATH as WAKE_MODEL, WakeTrainDialog
from .winutil import force_foreground

# ----------------------------------------------------------------------------
# Single instance
#   A second launch only opens the panel of the running app.
# ----------------------------------------------------------------------------

INSTANCE_KEY = "personal-assistant-ui"


# ----------------------------------------------------------------------------
# The app
#   Wires the pill, panel, tray, hotkey and the backend controller. Everything here runs on the UI
#   thread; the controller's signals arrive here already queued onto it.
# ----------------------------------------------------------------------------

class App:
    def __init__(self, qt: QApplication, mock: bool):
        self.qt = qt
        self.settings = load_settings()
        self.ctrl = Controller(self.settings, mock=mock)
        self.hotkey_label = pretty(self.settings.hotkey)
        self.pill = Pill(self.hotkey_label)
        self.panel = Panel()
        self.busy = False
        self.recording = False
        self.following = False  # a follow-up window is open (Ken listens without "Ken")
        self.toast = NudgeToast()  # proactive nudges pop up above the pill
        self._confirm_dialog = None
        self._hold = QTimer(singleShot=True, interval=self.settings.hold_to_talk_ms, timeout=self._hold_started)

        self.pill.level_source = self.ctrl.mic_level
        self.pill.clicked.connect(self.toggle_panel)
        self._wire_panel()
        self._wire_controller()
        self._tray()
        self.timer_board = TimerBoard(self.ctrl.timers,
                                      anchor=lambda: self.pill.geometry() if self.pill.isVisible() else None)
        self.timer_board.phase_changed.connect(self._timer_event)

        self.hotkey = Hotkey(self.settings.hotkey)
        self.hotkey.pressed.connect(self._hotkey_down)
        self.hotkey.released.connect(self._hotkey_up)
        self.hotkey.start()

        self._state_timer = QTimer(interval=150, timeout=self._refresh_pill)
        self._state_timer.start()
        saved = QPoint(self.settings.pill_x, self.settings.pill_y) if self.settings.pill_x >= 0 else None
        self.pill.place(saved)
        self.pill.moved.connect(self._pill_moved)
        if self.settings.show_pill:
            self.pill.show()
        self.ctrl.start()
        if self.settings.wake_word and WAKE_MODEL.exists():
            QTimer.singleShot(1500, lambda: self._set_wake(True))

    # ------------------------------------------------------------------------
    # Wiring
    #   Panel buttons -> controller calls; controller signals -> panel updates; background timers.
    # ------------------------------------------------------------------------

    def _wire_panel(self) -> None:
        p = self.panel
        p.ask.connect(self.ctrl.ask)
        p.escape.connect(self.hide_panel)
        p.mic_clicked.connect(self._mic_clicked)
        p.settings_clicked.connect(self.open_settings)
        p.new_chat.connect(self._new_chat)
        p.send_draft.connect(self._send_draft)
        p.regenerate_draft.connect(self._regenerate)
        p.check_new.connect(self.ctrl.check_inbox)
        p.brief.connect(self._brief)
        p.add_event_clicked.connect(self.ctrl.add_event)
        p.open_link.connect(open_in_browser)
        p.quit_app.connect(self.quit)
        p.stop.connect(self._stop)
        p.speak_switch.setChecked(self.settings.speak_replies)
        p.speak_toggled.connect(self._set_speak)
        p.person_reply.connect(self._person_reply)
        p.person_watch.connect(lambda person: self.ctrl.watch_chat(person.jid, person.name))
        p.person_star.connect(self._person_star)
        p.person_shortcut.connect(self._person_shortcut)
        p.save_shortcut.connect(self._save_shortcut)

    def _wire_controller(self) -> None:
        c = self.ctrl
        c.status.connect(self.panel.set_status)
        c.busy.connect(self._set_busy)
        c.reply.connect(self._on_reply)
        c.notice.connect(lambda kind, text: self.panel.add_notice(text, kind))
        c.confirm_requested.connect(self._confirm)
        c.draft_sent.connect(self._on_draft_sent)
        c.usage.connect(self.panel.usage.setText)
        c.connections.connect(self.panel.set_connections)
        c.transcript.connect(self._on_transcript)
        c.listening.connect(self.panel.set_recording)
        c.inbox.connect(self._show_inbox)
        c.unread_count.connect(self._set_unread)
        c.quick_chats.connect(self.panel.set_quick_chats)
        c.wake_heard.connect(self._wake_heard)
        c.reminder_fired.connect(self._notify)
        self._clock = QTimer(interval=15_000, timeout=self._every_15s)  # reminders, morning briefing
        self._clock.start()
        c.wake_ended.connect(self._wake_ended)
        c.event_added.connect(lambda event, ok, text: (card := self.panel.cards.get(event.id)) and card.mark_added(ok, text))
        # Background "anything new?" for the red dot: WhatsApp each minute, Gmail every 3rd time. No AI.
        self._poll_ticks = 0
        self._poll = QTimer(interval=60_000, timeout=self._poll_inbox)
        self._poll.start()
        QTimer.singleShot(15_000, self._poll_inbox)  # first check soon after connecting
        # Follow-up window and proactive nudges.
        c.follow_up.connect(self._follow_up)
        c.nudge.connect(self._show_nudge)
        c.shortcut_suggestions.connect(self._suggest_shortcuts)
        self.toast.draft.connect(self._nudge_draft)
        self.toast.snooze.connect(self.ctrl.snooze_nudge)
        self.toast.mute.connect(lambda n: self.ctrl.mute_nudges(n.jid))
        self._nudge_timer = QTimer(interval=10 * 60_000, timeout=self.ctrl.check_nudges)  # every 10 minutes
        self._nudge_timer.start()
        QTimer.singleShot(2 * 60_000, self.ctrl.check_nudges)  # first look two minutes after starting

    def _tray(self) -> None:
        self.tray = QSystemTrayIcon(theme.app_icon(), self.qt)
        self.tray.setToolTip(f"Ken · {self.hotkey_label}")
        menu = QMenu()
        menu.addAction("Open assistant", self.show_panel)
        menu.addAction("☀  Brief me", self._brief)
        menu.addAction("Check new messages", self.ctrl.check_inbox)
        timers = menu.addMenu("⏱  Timer")
        timers.addAction("Pomodoro (25 min focus / 5 break)", lambda: self.ctrl.timers.start("pomodoro"))
        timers.addAction("Stopwatch", lambda: self.ctrl.timers.start("stopwatch"))
        timers.addAction("Countdown…", self._ask_countdown)
        timers.addSeparator()
        timers.addAction("Stop all timers", lambda: self.ctrl.timers.control("stop", "all"))
        self.speak_action = QAction("Read every reply aloud (also typed ones)", menu, checkable=True,
                                    checked=self.settings.speak_replies)
        self.speak_action.toggled.connect(self._set_speak)
        menu.addAction(self.speak_action)
        self.pill_action = QAction("Show floating pill", menu, checkable=True, checked=self.settings.show_pill)
        self.pill_action.toggled.connect(self._toggle_pill)
        menu.addAction(self.pill_action)
        self.wake_action = QAction("Listen for “Ken”", menu, checkable=True,
                                   checked=self.settings.wake_word and WAKE_MODEL.exists())
        self.wake_action.toggled.connect(self._set_wake)
        menu.addAction(self.wake_action)
        menu.addAction("Teach Ken my voice…", self.train_wake)
        menu.addAction("Settings…", self.open_settings)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.toggle_panel() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()
        self._menu = menu  # keep a reference
        self.pill.menu_requested.connect(lambda pos: menu.popup(pos))

    # ------------------------------------------------------------------------
    # The panel and the pill
    # ------------------------------------------------------------------------

    def _set_speak(self, on: bool) -> None:
        """The header switch and the tray item are the same setting; keep both in sync."""
        if self.settings.speak_replies != on:
            self.ctrl.apply_settings({"speak_replies": on})
        for w in (self.panel.speak_switch, self.speak_action):
            if w.isChecked() != on:
                w.blockSignals(True)
                w.setChecked(on)
                w.blockSignals(False)
        if not on:
            self.ctrl.stop_speaking()

    def _pill_moved(self, pos: QPoint) -> None:
        self.ctrl.apply_settings({"pill_x": pos.x(), "pill_y": pos.y()})
        self.panel.user_moved = False  # the panel follows the pill again
        if self.panel.isVisible():
            self.panel.place_near(self.pill.geometry())

    def _poll_inbox(self) -> None:
        self.ctrl.poll_inbox(include_gmail=self._poll_ticks % 3 == 0)
        self._poll_ticks += 1

    def _set_unread(self, n: int) -> None:
        self.panel.bell.set_count(n)
        self.pill.set_unread(n)
        self.tray.setToolTip(f"Ken · {n} new" if n else f"Ken · {self.hotkey_label}")

    def _show_inbox(self, items: list) -> None:
        dialog = InboxDialog(items, self.panel if self.panel.isVisible() else None)
        dialog.reply.connect(self._reply_to)
        self.ctrl.mark_inbox_seen(items)
        dialog.open()
        force_foreground(dialog)

    def _reply_to(self, item) -> None:
        who = item.sender_name or (f"+{item.sender}" if item.channel == "whatsapp" else item.sender)
        where = f" in {item.group}" if item.group else ""
        self.show_panel()
        self.panel.add_user(f"Reply to {who}{where}: “{item.text[:80]}”")
        self.ctrl.ask(item.reply_prompt(), spoken=False)

    def show_panel(self) -> None:
        self.pill.set_attention(False)
        self.panel.place_near(self.pill.geometry())
        force_foreground(self.panel)
        self.panel.input.setFocus()

    def hide_panel(self) -> None:
        self.panel.hide()

    def toggle_panel(self) -> None:
        if self.ctrl.speaking:
            self.ctrl.stop_speaking()  # tapping while it talks just stops the voice
            return
        self.hide_panel() if self.panel.isVisible() else self.show_panel()

    def _toggle_pill(self, on: bool) -> None:
        self.ctrl.apply_settings({"show_pill": on})
        self.pill.setVisible(on)

    def _brief(self, morning: bool = False) -> None:
        self.show_panel()
        self.panel.add_user("☀ Good morning briefing" if morning else "☀ Brief me")
        self.ctrl.brief(speak_opening=morning)

    # ------------------------------------------------------------------------
    # Every 15 s: due reminders and the morning briefing
    # ------------------------------------------------------------------------

    def _every_15s(self) -> None:
        butler = self.settings.persona == "butler"
        for r in self.ctrl.reminders.due():
            arabic = any("\u0600" <= ch <= "\u06ff" for ch in r.text)
            if r.repeat:  # a yearly date from "remember…": "Sir, today: Youssef's birthday is 3 May"
                spoken = (f"Sir، النهارده: {r.text}" if butler else f"النهارده: {r.text}") if arabic else \
                    (f"Sir, today: {r.text}" if butler else f"Today: {r.text}")
                self._notify("🎂 Today", r.text, spoken)
                continue
            spoken = (f"Sir، تذكير: {r.text}" if butler else f"تذكير: {r.text}") if arabic else \
                (f"Sir, a reminder: {r.text}" if butler else f"Reminder: {r.text}")
            self._notify("⏰ Reminder", r.text, spoken)
        self._maybe_morning_brief()

    def _notify(self, title: str, body: str, spoken: str = "") -> None:
        chime("done")
        self.tray.showMessage(title, body, theme.app_icon(), 10_000)
        self.panel.add_notice(f"{title}: {body}")
        if spoken and (self.settings.speak_replies or self.settings.wake_word or self.settings.speak_after_voice):
            self.ctrl.say(spoken)

    def _maybe_morning_brief(self) -> None:
        if not self.settings.morning_brief or self.busy or self.ctrl.agent is None:
            return
        now = datetime.now()
        try:
            h, m = (int(x) for x in self.settings.morning_brief_time.split(":"))
        except ValueError:
            return
        if not (now.hour, now.minute) >= (h, m) or now.hour >= 12:
            return
        state = DATA_DIR / "morning_brief.txt"
        today = now.date().isoformat()
        if state.exists() and state.read_text(encoding="utf-8").strip() == today:
            return
        state.write_text(today, encoding="utf-8")
        self._brief(morning=True)

    # ------------------------------------------------------------------------
    # Timers
    #   Countdown from the tray; chime and notify when a phase changes.
    # ------------------------------------------------------------------------

    def _ask_countdown(self) -> None:
        text, ok = QInputDialog.getText(None, "Countdown", "Minutes and a name, e.g. \"10 Tea\":", text="10 Timer")
        parts = text.strip().split(maxsplit=1)
        if not ok or not parts:
            return
        try:
            self.ctrl.timers.start("countdown", parts[1] if len(parts) > 1 else "", minutes=float(parts[0]))
        except ValueError:
            self.panel.add_notice("Countdown: start with the minutes, e.g. \"10 Tea\".", "error")

    def _timer_event(self, timer, phase: str) -> None:
        """A countdown or pomodoro finished, or a pomodoro switched between focus and break."""
        if phase == "done":
            chime("done")
            title, body = f"⏰ {timer.name}", ("Pomodoro finished, well done!" if timer.mode == "pomodoro" else "Time's up!")
        elif phase in ("break", "long break"):
            chime("phase")
            title, body = f"☕ {timer.name}", "Take a long break." if phase == "long break" else "Break time."
        else:
            chime("phase")
            title, body = f"🎯 {timer.name}", "Back to focus."
        self.tray.showMessage(title, body, theme.app_icon(), 6000)

    # ------------------------------------------------------------------------
    # Wake word: say "Ken", then the request
    # ------------------------------------------------------------------------

    def _set_wake(self, on: bool) -> None:
        if on and not WAKE_MODEL.exists():
            self._sync_wake_action(False)
            self.train_wake()
            return
        # Not paused while Ken talks: saying "Ken" then cuts him off ("Ken, stop").
        error = self.ctrl.start_wake(paused=lambda: self.busy or self.recording) if on else ""
        if not on:
            self.ctrl.stop_wake()
        if error:
            self.panel.add_notice(error, "error")
            on = False
        if self.settings.wake_word != on:
            self.ctrl.apply_settings({"wake_word": on})
        self._sync_wake_action(on)

    def _sync_wake_action(self, on: bool) -> None:
        if self.wake_action.isChecked() != on:
            self.wake_action.blockSignals(True)
            self.wake_action.setChecked(on)
            self.wake_action.blockSignals(False)

    def train_wake(self) -> None:
        self.ctrl.stop_wake()  # the trainer needs the microphone
        if WakeTrainDialog().exec() == WakeTrainDialog.DialogCode.Accepted and WAKE_MODEL.exists():
            self._set_wake(True)
        elif self.settings.wake_word and WAKE_MODEL.exists():
            self._set_wake(True)  # cancelled: go back to listening with the old model

    def _wake_heard(self) -> None:
        self.ctrl.stop_speaking()
        self.recording = True
        self.panel.set_recording(True)  # Ken says "At your service, Sir." (wake thread), then records
        self.pill.jump()

    def _wake_ended(self, got_speech: bool) -> None:
        self.recording = False
        self.panel.set_recording(False)

    # ------------------------------------------------------------------------
    # Follow-up window: the pill shows "still listening_" with a shrinking bar
    # ------------------------------------------------------------------------

    def _follow_up(self, seconds: float) -> None:
        self.following = seconds > 0
        self.pill.set_follow(seconds)
        self.panel.set_recording(self.following)

    # ------------------------------------------------------------------------
    # Proactive nudges: a card above the pill; spoken only for ★ people (and calendar alerts)
    # ------------------------------------------------------------------------

    def _show_nudge(self, nudge) -> None:
        anchor = self.pill.geometry() if self.pill.isVisible() else None
        self.toast.show_nudge(nudge, anchor)
        self.pill.set_attention(True)
        chime("phase")
        voice_on = self.settings.speak_replies or self.settings.wake_word or self.settings.speak_after_voice
        if nudge.important and voice_on and not self.busy and not self.recording:
            self.ctrl.say(nudge.spoken)

    def _nudge_draft(self, nudge) -> None:
        """The card's "draft reply": the same as asking Ken for a reply to that chat."""
        self.show_panel()
        self.panel.add_user(f"Reply to {nudge.name}")
        self.ctrl.ask(f"Draft a reply to {nudge.name}'s latest WhatsApp messages (chat JID {nudge.jid}). "
                      "Don't ask me anything: call my_past_messages for this chat, then show_draft.", spoken=False)

    # ------------------------------------------------------------------------
    # Person card buttons
    # ------------------------------------------------------------------------

    def _person_reply(self, person) -> None:
        self.panel.add_user(f"Reply to {person.name}")
        self.ctrl.ask(f"Draft a reply to {person.name} on WhatsApp (chat JID {person.jid}). Don't ask me anything: "
                      "call my_past_messages for this chat, then show_draft.", spoken=False)

    def _person_star(self, person) -> None:
        on = self.ctrl.toggle_important(person.jid)
        card = self.panel.cards.get(person.id)
        if card is not None:
            card.set_important(on)
        self.panel.add_notice(f"★ {person.name}: their nudges will be spoken aloud." if on
                              else f"{person.name} is no longer marked important.")

    # ------------------------------------------------------------------------
    # Shortcuts ("mom" -> her chat): saved only from a click
    # ------------------------------------------------------------------------

    def _save_shortcut(self, proposal) -> None:
        saved = self.ctrl.save_shortcut(proposal.shortcut)
        card = self.panel.cards.get(proposal.id)
        if card is not None:
            card.mark_saved()
        self.panel.add_notice(f"Shortcut saved: {', '.join(saved.words)} → {saved.label}.")

    def _suggest_shortcuts(self, proposals: list) -> None:
        """Once, at startup: contacts saved as "Mom", "ماما"… offered as shortcut cards."""
        self.panel.add_notice("These contacts look like family. Save them as shortcuts, so “send a message to "
                              "mom” just works?")
        for proposal in proposals:
            self.panel.add_shortcut(proposal)
        if not self.panel.isVisible():
            self.pill.set_attention(True)

    def _person_shortcut(self, person) -> None:
        """The person card's "＋ shortcut": ask for the word(s), then save (this click confirms)."""
        from ..shortcuts import Shortcut, split_words

        text, ok = QInputDialog.getText(self.panel, "Shortcut", f"Your word(s) for {person.name}, e.g. “mom, ماما”:")
        words = split_words(text) if ok else []
        if words:
            saved = self.ctrl.save_shortcut(Shortcut(words, "whatsapp", person.jid, person.name, person.number))
            self.panel.add_notice(f"Shortcut saved: {', '.join(saved.words)} → {saved.label}.")

    def _new_chat(self) -> None:
        self.ctrl.reset()
        self.panel.clear()

    # ------------------------------------------------------------------------
    # Hotkey: tap = open/close, hold = talk
    # ------------------------------------------------------------------------

    def _hotkey_down(self) -> None:
        self._hold.start()

    def _hold_started(self) -> None:
        if not self.busy and self.ctrl.start_listening():
            self.recording = True

    def _hotkey_up(self) -> None:
        if self._hold.isActive():
            self._hold.stop()
            self.toggle_panel()
        elif self.recording:  # the request runs in the background; the panel stays as it was
            self.recording = False
            self.ctrl.stop_listening()

    def _mic_clicked(self) -> None:
        if self.recording:
            self.recording = False
            self.ctrl.stop_listening()
        elif not self.busy and self.ctrl.start_listening():
            self.recording = True

    def _on_transcript(self, text: str) -> None:
        if not text:
            self.panel.add_notice("I didn't catch that. Try again.")
        elif self.panel.isVisible():
            self.panel.put_transcript(text)  # shown for a moment so it can be corrected, then sent
        else:
            self.panel.send_spoken(text)  # panel closed: send at once, keep it closed

    # ------------------------------------------------------------------------
    # Backend results
    #   A reply becomes bubbles and cards. The panel never pops open by itself; the pill glows instead.
    # ------------------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.panel.set_busy(busy)

    def _on_reply(self, result: TurnResult) -> None:
        self.pill.flash_done()
        self.panel.add_assistant(result.text)
        for draft in result.drafts:
            self.panel.add_draft(draft)
        for event in result.events:
            self.panel.add_event(event)
        for link in result.links:
            self.panel.add_link(link)
        for person in result.people:
            self.panel.add_person(person, important=person.jid in self.settings.important_people)
        for proposal in result.shortcuts:
            self.panel.add_shortcut(proposal)
        for sent in result.sent:
            self.panel.add_notice(f"✓ Sent to {', '.join(sent.recipients)}")
        # Never pops open by itself. Something waiting for a click (a draft, event or link) makes
        # the pill glow until the panel is opened.
        if not self.panel.isVisible() and (result.drafts or result.events or result.links or result.shortcuts):
            self.pill.set_attention(True)

    def _send_draft(self, draft: Draft, text: str) -> None:
        self.ctrl.send_draft(draft, text)

    def _on_draft_sent(self, draft: Draft, result: TurnResult) -> None:
        card = self.panel.cards.get(draft.id)
        if result.sent and card:
            card.mark_sent()
        elif not result.sent:
            self.panel.add_notice(result.text.replace("Blocked: ", "Not sent: "), "error" if "Blocked" in result.text else "info")

    def _regenerate(self, draft: Draft) -> None:
        who = draft.recipient_name or draft.recipient
        text = f"Rewrite the draft to {who} with different wording."
        self.panel.add_user(text)
        self.ctrl.ask(text, spoken=False)

    def _stop(self) -> None:
        if self._confirm_dialog is not None and self._confirm_dialog.isVisible():
            self._confirm_dialog.reject()  # a stopped reply never sends
        self.ctrl.stop()

    def _confirm(self, request: SendRequest, future: concurrent.futures.Future) -> None:
        dialog = ConfirmSendDialog(request, self.panel if self.panel.isVisible() else None)
        self._confirm_dialog = dialog
        dialog.finished.connect(lambda code: future.done() or future.set_result(code == ConfirmSendDialog.DialogCode.Accepted))
        dialog.open()
        force_foreground(dialog)

    # ------------------------------------------------------------------------
    # Pill state
    #   Polled every 150 ms: listening > working > speaking > idle.
    # ------------------------------------------------------------------------

    def _refresh_pill(self) -> None:
        if self.recording or (self.following and self.pill.following):
            state = LISTENING
        elif self.busy:
            state = WORKING
        elif self.ctrl.speaking:
            state = SPEAKING
        else:
            state = IDLE if self.pill.state != "hover" else "hover"
        self.pill.set_state(state)
        if self.panel.isVisible():
            self.panel.set_mode(self.pill.plexus_state())

    # ------------------------------------------------------------------------
    # Settings and quit
    # ------------------------------------------------------------------------

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.ctrl.installed_voices(), self.hotkey_label,
                                on_learn=self._learn_history, on_calendar=self._connect_calendar,
                                on_wake_train=self.train_wake, on_preview=self.ctrl.preview_voice,
                                facts=self.ctrl.list_facts(), on_delete_fact=self.ctrl.delete_fact,
                                shortcuts=self.ctrl.shortcuts.all(), on_remove_shortcut=self.ctrl.remove_shortcut,
                                on_add_shortcut=self.ctrl.add_shortcut,
                                shortcut_suggestions=self.ctrl.shortcut_suggestions_now(),
                                on_save_shortcut=self.ctrl.save_shortcut)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            changes = dialog.changes()
            if changes:
                self.ctrl.apply_settings(changes)
                if "wake_word" in changes:
                    self._set_wake(changes["wake_word"])
                elif self.ctrl.wake is not None and {"tts_engine", "tts_openai_voice", "persona",
                                                     "edge_voice_en", "tts_rate"} & changes.keys():
                    self._set_wake(True)  # new voice: remake "At your service, Sir." in it
                self._set_speak(self.settings.speak_replies)
                self.pill_action.setChecked(self.settings.show_pill)
                self.pill.setVisible(self.settings.show_pill)
                self.panel.add_notice("Settings saved.")

    def _connect_calendar(self) -> None:
        self.ctrl.connect_calendar()
        self.panel.add_notice("Your browser opened Google's consent page. Allow access, then restart the "
                              "assistant (⏻, then start it again) to connect the calendar.")

    def _learn_history(self) -> None:
        self.show_panel()
        self.ctrl.learn_history()

    def quit(self) -> None:
        """Quit for real: stop the hotkey hook, close the MCP servers, end the process."""
        self.pill.hide()
        self.panel.hide()
        self.toast.hide()
        self.tray.hide()
        self.hotkey.stop()
        self.ctrl.shutdown(timeout=3)
        self.qt.quit()
        # Safety net: if a background thread or child process still holds us, exit anyway.
        threading.Timer(4.0, lambda: os._exit(0)).start()


# ----------------------------------------------------------------------------
# Logging and entry point
# ----------------------------------------------------------------------------

def _log_to_file() -> None:
    """pythonw has no console, so warnings (voice fallbacks, MCP errors) went nowhere: keep them in data/app.log."""
    if sys.stdout is not None:
        return  # started from a terminal: print there
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / "app.log"
    mode = "w" if path.exists() and path.stat().st_size > 1_000_000 else "a"  # start over past ~1 MB
    log = open(path, mode, encoding="utf-8", buffering=1)
    log.write(f"\n--- started {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    sys.stdout = sys.stderr = log


def main() -> None:
    _log_to_file()
    parser = argparse.ArgumentParser(prog="assistant.ui")
    parser.add_argument("--mock", action="store_true", help="fake WhatsApp/Gmail data")
    args = parser.parse_args()

    qt = QApplication(sys.argv)
    qt.setApplicationName("Assistant")
    qt.setQuitOnLastWindowClosed(False)  # lives in the tray
    qt.setWindowIcon(theme.app_icon())
    qt.setStyleSheet(theme.STYLESHEET + theme.switch_stylesheet(DATA_DIR / "ui"))
    qt.setFont(theme.font(10))

    # Single instance: a second launch just opens the panel of the running one.
    probe = QLocalSocket()
    probe.connectToServer(INSTANCE_KEY)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.waitForBytesWritten(300)
        return
    QLocalServer.removeServer(INSTANCE_KEY)
    server = QLocalServer()
    server.listen(INSTANCE_KEY)

    app = App(qt, mock=args.mock)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), app.show_panel()))
    qt.aboutToQuit.connect(app.ctrl.shutdown)
    sys.exit(qt.exec())
