"""Panel behaviour that needs Qt (runs headless)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from assistant.agent import Draft, EventProposal  # noqa: E402
from assistant.ui.panel import Panel  # noqa: E402


# ----------------------------------------------------------------------------
# Qt (headless)
# ----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


# ----------------------------------------------------------------------------
# Panel, cards and wake-word helpers
# ----------------------------------------------------------------------------

def test_draft_buttons_come_back_after_busy_with_an_event_card(qt):
    """An event card in the panel used to crash set_busy, so new drafts stayed disabled."""
    panel = Panel()
    start = datetime(2026, 10, 30, 14, 0).astimezone()
    panel.add_event(EventProposal("Class", start, start + timedelta(hours=1)))
    panel.set_busy(True)  # the reply arrives while the assistant is still busy
    cards = [panel.add_draft(Draft("whatsapp", "201001234567@s.whatsapp.net", "تمام")),
             panel.add_draft(Draft("gmail", "sara@example.com", "Thanks, see you then.", subject="Re: meeting"))]
    assert not any(c.btn_send.isEnabled() for c in cards)
    panel.set_busy(False)
    for card in cards:
        assert card.btn_send.isEnabled() and card.btn_regen.isEnabled() and card.btn_discard.isEnabled()


def test_wake_prefix_is_stripped_only_when_it_is_the_name():
    from assistant.ui.controller import WAKE_PREFIX

    assert WAKE_PREFIX.sub("", "Ken, what's new?").strip() == "what's new?"
    assert WAKE_PREFIX.sub("", "hey ken check whatsapp").strip() == "check whatsapp"
    assert WAKE_PREFIX.sub("", "كين، ايه الجديد").strip() == "ايه الجديد"
    for keep in ("كنت عايز أقولك", "Kenny called", "can you check"):
        assert WAKE_PREFIX.sub("", keep) == keep


def test_wake_training_window_opens(qt):
    from assistant.ui.wake_dialog import WakeTrainDialog

    dialog = WakeTrainDialog()
    assert dialog.step == "intro" and dialog.go.isEnabled()
    dialog.close()


def test_stop_words_only_stop():
    from assistant.ui.controller import STOP_WORDS

    for stop in ("stop", "Stop it.", "stop talking please", "enough, sir", "خلاص", "اسكت يا Ken", "كفاية"):
        assert STOP_WORDS.match(stop), stop
    for request in ("stop the timer", "what's new?", "بس كده ابعتها", "cancel my meeting"):
        assert not STOP_WORDS.match(request), request


def test_link_card_opens_only_on_click(qt):
    from assistant.agent import LinkProposal

    panel = Panel()
    urls = []
    panel.open_link.connect(urls.append)
    card = panel.add_link(LinkProposal("https://example.com/claim"))
    panel.set_busy(True)
    panel.set_busy(False)  # cards without model buttons must not break this
    assert urls == []
    card.btn_open.click()
    assert urls == ["https://example.com/claim"]


def test_spoken_request_sends_without_opening_the_panel(qt):
    panel = Panel()
    asked = []
    panel.ask.connect(lambda text, spoken: asked.append((text, spoken)))
    panel.send_spoken("what's new on whatsapp?")
    assert asked == [("what's new on whatsapp?", True)] and not panel.isVisible()


def test_pill_glows_when_something_waits(qt):
    from assistant.ui.pill import Pill

    pill = Pill("Ctrl+Shift+Space")
    pill.set_attention(True)
    pill.grab()  # paints the glow without errors
    assert pill.attention
    pill.set_attention(False)
    assert not pill.attention
