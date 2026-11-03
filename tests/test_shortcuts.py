"""Shortcuts: "send a message to my mom" -> her exact chat (all offline)."""

from __future__ import annotations

import asyncio

import pytest

from assistant.agent import Agent
from assistant.config import Settings
from assistant.contacts import MockDirectory
from assistant.llm import LLMReply, ToolCall
from assistant.mock_tools import MockToolSource
from assistant.shortcuts import Shortcut, Shortcuts, resolve, split_words
from tests.test_backend import FakeBackend

MOM = "201119876543@s.whatsapp.net"  # "ماما" in the mock directory
UNI = "1203630-1600@g.us"


# ----------------------------------------------------------------------------
# Matching words in a message
# ----------------------------------------------------------------------------

def _store(tmp_path) -> Shortcuts:
    s = Shortcuts(tmp_path / "shortcuts.json")
    s.save(Shortcut(["mom", "mama", "ماما", "أمي"], "whatsapp", MOM, "ماما", "201119876543"))
    s.save(Shortcut(["uni", "uni friends"], "whatsapp", UNI, "Uni friends"))
    s.save(Shortcut(["boss"], "gmail", "sara@company.com", "sara@company.com"))
    return s


def test_shortcut_words_are_found_in_english_and_arabic(tmp_path):
    s = _store(tmp_path)
    assert [x.target for _, x in s.matches("Send a message to my mom")] == [MOM]
    assert [x.target for _, x in s.matches("what did mom's voice note say?")] == [MOM]
    assert [x.target for _, x in s.matches("ابعت لماما إني جاي")] == [MOM]  # "to mama", glued
    assert [x.target for _, x in s.matches("ابعت لأمي")] == [MOM]
    assert [x.target for _, x in s.matches("email my boss the report")] == ["sara@company.com"]
    assert [x.target for _, x in s.matches("what's new in uni friends?")] == [UNI]
    assert s.matches("momentum and bossa nova") == []  # whole words only
    assert s.lookup("my mom").target == MOM and s.lookup("Ahmed") is None


def test_a_word_points_at_one_target_only(tmp_path):
    s = _store(tmp_path)
    s.save(Shortcut(["mom"], "whatsapp", "201000000000@s.whatsapp.net", "Other"))
    assert s.lookup("mom").target == "201000000000@s.whatsapp.net"
    assert "mom" not in next(x for x in s.all() if x.target == MOM).words
    s.save(Shortcut(["mother"], "whatsapp", MOM, "ماما"))  # more words for the same chat: merged
    assert len([x for x in s.all() if x.target == MOM]) == 1
    assert Shortcuts(tmp_path / "shortcuts.json").lookup("mother").target == MOM  # saved to disk


def test_resolve_needs_one_clear_target():
    d = MockDirectory()
    shortcut, why = resolve(split_words("mom, ماما"), "ماما", d)
    assert shortcut.target == MOM and shortcut.words == ["mom", "ماما"] and not why
    email, _ = resolve(["boss"], "Sara@Company.com", d)
    assert email.channel == "gmail" and email.target == "sara@company.com"
    none, why = resolve(["x"], "zzzz", d)
    assert none is None and "No chat" in why


def test_family_contacts_are_suggested_once(tmp_path):
    s = Shortcuts(tmp_path / "shortcuts.json")
    found = s.suggestions(MockDirectory().contacts())
    by_target = {p.shortcut.target: p.shortcut for p in found}
    assert MOM in by_target and "mom" in by_target[MOM].words and "ماما" in by_target[MOM].words
    assert "201001234567@s.whatsapp.net" in by_target  # saved as "Brother"
    assert all(p.suggested for p in found)
    s.mark_offered([p.shortcut.target for p in found])
    assert Shortcuts(tmp_path / "shortcuts.json").suggestions(MockDirectory().contacts()) == []
    assert s.suggestions(MockDirectory().contacts(), include_offered=True)  # Settings still lists them


# ----------------------------------------------------------------------------
# The agent
# ----------------------------------------------------------------------------

def _agent(tmp_path, replies, shortcuts):
    backend = FakeBackend(replies)
    agent = Agent(backend, MockToolSource(), Settings(audit_log_path=str(tmp_path / "a.jsonl")), None,
                  directory=MockDirectory(), shortcuts=shortcuts)
    asyncio.run(agent.start())
    return agent, backend


def test_message_with_a_shortcut_gets_the_exact_chat(tmp_path):
    agent, backend = _agent(tmp_path, [LLMReply(text="ok")], _store(tmp_path))
    asyncio.run(agent.run("Send a message to my mom that I'm coming"))
    system = backend.seen[-1][0]["content"]
    assert '"mom" = ماما (WhatsApp +201119876543, chat JID 201119876543@s.whatsapp.net)' in system
    assert not agent.guard.unknown([MOM])  # her chat may receive the (confirmed) send


def test_find_chat_puts_the_shortcut_first(tmp_path):
    agent, _ = _agent(tmp_path, [LLMReply(tool_calls=[ToolCall("f", "find_chat", {"name": "uni"})]),
                                 LLMReply(text="ok")], _store(tmp_path))
    asyncio.run(agent.run("what's new?"))
    result = agent.history[2]["content"]
    assert f"1. Uni friends (your shortcut \"uni\")" in result and UNI in result


def test_propose_shortcut_shows_a_card_and_saves_nothing(tmp_path):
    store = Shortcuts(tmp_path / "shortcuts.json")
    agent, _ = _agent(tmp_path, [LLMReply(tool_calls=[ToolCall("s", "propose_shortcut", {"words": "mom, mama", "who": "ماما"})]),
                                 LLMReply(text="Sir, save it below.")], store)
    result = asyncio.run(agent.run("mom is ماما"))
    assert [p.shortcut.target for p in result.shortcuts] == [MOM]
    assert store.all() == []  # only the card's Save click saves


# ----------------------------------------------------------------------------
# UI: the card and the Settings page
# ----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qt():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_shortcut_card_and_settings(qt, tmp_path):
    from assistant.shortcuts import ShortcutProposal
    from assistant.ui.dialogs import SettingsDialog
    from assistant.ui.panel import Panel

    panel = Panel()
    proposal = ShortcutProposal(Shortcut(["mom", "ماما"], "whatsapp", MOM, "ماما", "201119876543"))
    card = panel.add_shortcut(proposal)
    saved = []
    panel.save_shortcut.connect(saved.append)
    card.btn_save.click()
    assert saved == [proposal]
    card.mark_saved()
    assert card.state_label.text() == "SAVED ✓"

    store = _store(tmp_path)
    removed = []
    d = SettingsDialog(Settings(), [], "Ctrl+Shift+Space", shortcuts=store.all(), on_remove_shortcut=removed.append,
                       on_add_shortcut=lambda w, who: resolve(split_words(w), who, MockDirectory()))
    assert len(d._shortcut_widgets) == 3
    panel.close()
    d.close()
