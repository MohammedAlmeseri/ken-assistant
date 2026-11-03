"""Shortcuts: your own words for people, groups and email addresses.

  "send a message to my mom"   -> mom = ماما (WhatsApp +20 11…)
  "what's new in uni?"         -> uni = the "Uni friends" group
  "email my boss the report"   -> boss = sara@company.com

A shortcut points ONE word (or a few spellings of it: mom, mama, ماما, أمي) at exactly ONE chat or
email address. There are deliberately no shortcuts for several people ("family"): that would turn
one message into a broadcast, and WhatsApp limits (and may ban) bulk sending.

How it's used:
  - Before the model sees a message, the shortcut words in it are looked up in code and one line
    is added to the prompt: "mom = ماما (WhatsApp, chat JID …)". No guessing, no extra request.
  - find_chat, person_summary and voice_notes check shortcuts first too.
  - A shortcut only says WHO. Sending still needs your request and the confirmation dialog, which
    shows the real name and number.

How they're made (always with a click, since a wrong shortcut would mean messaging the wrong person):
  - by asking Ken ("mom is ماما") -> a card with Save;
  - Settings → Shortcuts, or "＋ shortcut" on a person card;
  - starter suggestions: contacts saved as "Mom", "ماما", "Dad"… are offered once.

Stored in data/shortcuts.json (on this computer only, never in git).
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .contacts import normalize
from .llm import ToolSpec

# ----------------------------------------------------------------------------
# The tool the model calls ("mom is ماما", "call Youssef bro", "boss is sara@x.com")
#   It only proposes: the app shows a card and saves on the owner's click.
# ----------------------------------------------------------------------------

SHORTCUT_TOOL = ToolSpec(
    name="propose_shortcut",
    description="Offer to save the user's own word for ONE person, group or email address (e.g. 'mom' -> their "
                "mother's chat). Shows a card the user saves with a click. Never for several people.",
    parameters={
        "type": "object",
        "properties": {
            "words": {"type": "string", "description": "the word(s), comma-separated spellings, e.g. 'mom, mama, ماما'"},
            "who": {"type": "string", "description": "the chat name / JID, or an email address"},
        },
        "required": ["words", "who"],
    },
)


# ----------------------------------------------------------------------------
# One shortcut
#   words:   the spellings that trigger it ("mom", "ماما"), as typed (matched normalized).
#   channel: "whatsapp" (a person or group chat) or "gmail" (an email address).
#   target:  the chat JID or the email address.
#   label:   what the card and the prompt call it ("ماما", "Uni friends").
#   number:  phone digits for a person's chat ("" for groups and email), shown in confirmations.
# ----------------------------------------------------------------------------

@dataclass
class Shortcut:
    words: list[str]
    channel: str
    target: str
    label: str
    number: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:6])

    def describe(self) -> str:
        where = (f"WhatsApp group, chat JID {self.target}" if self.target.endswith("@g.us")
                 else f"WhatsApp +{self.number}, chat JID {self.target}" if self.channel == "whatsapp"
                 else f"email {self.target}")
        return f"{self.label} ({where})"


@dataclass
class ShortcutProposal:
    """A shortcut waiting for the owner's click (a card in the panel)."""
    shortcut: Shortcut
    suggested: bool = False  # a starter suggestion, not something the owner asked for
    id: str = field(default_factory=lambda: "sc-" + uuid.uuid4().hex[:6])


def iso(text: str) -> str:
    """Wrap text in a Unicode direction isolate, so Arabic words, names and "+20…" numbers keep their
    own order when mixed on one line with English ("mom, ماما → ماما · +20 11…")."""
    # The left-to-right marks around it also pin the commas between two Arabic words.
    return f"‎⁨{text}⁩‎"


def split_words(text: str) -> list[str]:
    """'mom, mama / ماما' -> ['mom', 'mama', 'ماما'] (kept as typed, empty parts dropped)."""
    parts = re.split(r"[,،/;|\n]+", text or "")
    return list(dict.fromkeys(p.strip().strip("'\"“”") for p in parts if p.strip().strip("'\"“”")))[:8]


# ----------------------------------------------------------------------------
# Matching words inside a message
#   Everything is normalized like chat names (case, أ/إ/ا, ة/ه, ى/ي, punctuation). Arabic glues
#   little words to the front ("لماما" = to mama, "وأمي" = and my mom), so those prefixes are
#   also tried. Possessives are handled by normalization ("mom's" -> "mom s").
# ----------------------------------------------------------------------------

_AR_PREFIXES = ("وال", "بال", "لل", "ال", "و", "ل", "ب", "ف")


def _token_variants(token: str) -> set[str]:
    out = {token}
    for p in _AR_PREFIXES:
        if token.startswith(p) and len(token) - len(p) >= 2:
            out.add(token[len(p):])
    return out


def _phrase_in(phrase: str, tokens: list[str]) -> bool:
    words = phrase.split()
    if not words:
        return False
    if len(words) == 1:
        return any(words[0] in _token_variants(t) for t in tokens)
    n = len(words)
    return any(tokens[i:i + n] == words for i in range(len(tokens) - n + 1))


# ----------------------------------------------------------------------------
# Starter suggestions: contacts saved under a family word
#   Each group of spellings becomes one shortcut. When several contacts match ("Mom", "Mom work"),
#   the one you message most is offered. Each target is offered only once.
# ----------------------------------------------------------------------------

FAMILY = [
    ["mom", "mum", "mother", "mama", "ماما", "أمي", "امي", "والدتي"],
    ["dad", "father", "papa", "baba", "بابا", "أبي", "ابويا", "أبويا", "والدي"],
    ["wife", "زوجتي", "مراتي"],
    ["husband", "زوجي", "جوزي"],
    ["brother", "bro", "أخي", "اخويا", "أخويا"],
    ["sister", "sis", "أختي", "اختي"],
    ["grandma", "granny", "تيتة", "جدتي", "ستي"],
    ["grandpa", "جدو", "جدي"],
]


# ----------------------------------------------------------------------------
# The store
# ----------------------------------------------------------------------------

class Shortcuts:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.items: list[Shortcut] = []
        self.offered: list[str] = []  # targets already suggested once
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.items = [Shortcut(**s) for s in data.get("shortcuts", [])]
                self.offered = list(data.get("offered", []))
            except (ValueError, TypeError):
                pass  # a broken file shouldn't stop the app

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"shortcuts": [asdict(s) for s in self.items], "offered": self.offered},
                                        ensure_ascii=False, indent=1), encoding="utf-8")

    # ------------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------------

    def all(self) -> list[Shortcut]:
        with self.lock:
            return list(self.items)

    def lookup(self, name: str) -> Shortcut | None:
        """The shortcut a name IS ('mom', 'my mom', 'ماما'), for find_chat and friends."""
        n = normalize(name)
        n = re.sub(r"^(my|the)\s+", "", n)
        if not n:
            return None
        for s in self.all():
            if any(normalize(w) == n for w in s.words):
                return s
        return None

    def matches(self, text: str) -> list[tuple[str, Shortcut]]:
        """Every (word, shortcut) mentioned in a message, longest words first ("uni friends" before "uni")."""
        tokens = normalize(text).split()
        found: list[tuple[str, Shortcut]] = []
        pairs = sorted(((w, s) for s in self.all() for w in s.words), key=lambda p: -len(p[0]))
        for word, s in pairs:
            if any(x is s for _, x in found):
                continue
            if _phrase_in(normalize(word), tokens):
                found.append((word, s))
        return found

    def hint(self, text: str) -> str:
        """The line added to the prompt for this message ('' if it names no shortcut)."""
        found = self.matches(text)
        if not found:
            return ""
        return ("\nShortcuts the user saved, used in this message (use these exact chats/addresses; no need to "
                "call find_chat for them):\n" + "\n".join(f'- "{w}" = {s.describe()}' for w, s in found))

    # ------------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------------

    def save(self, shortcut: Shortcut) -> Shortcut:
        """Add a shortcut. A word points at one target only: saving 'mom' for someone else moves it.
        Saving more words for a target that already has a shortcut adds them to it."""
        with self.lock:
            words = {normalize(w) for w in shortcut.words}
            for s in self.items:  # the words leave any other shortcut
                if s.target != shortcut.target:
                    s.words = [w for w in s.words if normalize(w) not in words]
            existing = next((s for s in self.items if s.target == shortcut.target), None)
            if existing is not None:
                existing.words = list(dict.fromkeys(existing.words + shortcut.words))
                existing.label = shortcut.label or existing.label
                result = existing
            else:
                self.items.append(shortcut)
                result = shortcut
            self.items = [s for s in self.items if s.words]
            if shortcut.target not in self.offered:
                self.offered.append(shortcut.target)
            self._save()
            return result

    def remove(self, shortcut_id: str) -> None:
        with self.lock:
            self.items = [s for s in self.items if s.id != shortcut_id]
            self._save()

    def mark_offered(self, targets: list[str]) -> None:
        with self.lock:
            self.offered = list(dict.fromkeys(self.offered + targets))
            self._save()

    # ------------------------------------------------------------------------
    # Starter suggestions
    # ------------------------------------------------------------------------

    def suggestions(self, refs: list, include_offered: bool = False) -> list[ShortcutProposal]:
        """Family-looking contacts (ChatRef list) not yet saved, nor offered before (unless
        include_offered: Settings always lists them)."""
        taken = {s.target for s in self.all()} | (set() if include_offered else set(self.offered))
        out = []
        for spellings in FAMILY:
            wanted = {normalize(w) for w in spellings}
            hits = [r for r in refs if not r.is_group and r.name and r.jid not in taken
                    and len(normalize(r.name).split()) <= 3 and wanted & set(normalize(r.name).split())]
            if not hits:
                continue
            best = max(hits, key=lambda r: (r.sent_recently, r.last_time))
            taken.add(best.jid)
            # every spelling, so "my mom", "mama" and "أمي" all work; you can delete any in Settings
            out.append(ShortcutProposal(Shortcut(list(spellings), "whatsapp", best.jid, best.name, best.number),
                                        suggested=True))
        return out


# ----------------------------------------------------------------------------
# Turning "who" into a shortcut (for the tool and for Settings → Add)
#   An email address -> an email shortcut. Otherwise the best WhatsApp chat match, which must be
#   clear (no close second), or the call explains what's ambiguous.
# ----------------------------------------------------------------------------

_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")


def resolve(words: list[str], who: str, directory) -> tuple[Shortcut | None, str]:
    who = who.strip()
    if not words:
        return None, "Give the word(s) for the shortcut."
    if _EMAIL.match(who) and not who.endswith(("@s.whatsapp.net", "@g.us")):
        return Shortcut(words, "gmail", who.lower(), who.lower()), ""
    if directory is None:
        return None, "WhatsApp isn't connected."
    if who.endswith(("@s.whatsapp.net", "@g.us")):
        ref = next((r for r in directory.find(who.split("@")[0], 3) if r.jid == who), None)
        matches = [ref] if ref else []
    else:
        matches = directory.find(who, 3)
    if not matches:
        return None, f"No chat matches '{who}'."
    best = matches[0]
    if len(matches) > 1 and matches[1].score >= best.score - 0.03 and best.score < 1.0:
        names = ", ".join(m.label for m in matches[:3])
        return None, f"'{who}' could be several chats ({names}): say which one."
    return Shortcut(words, "whatsapp", best.jid, best.label, best.number), ""
