"""Instant acknowledgment: a short spoken line while the main call works (brief section 8)."""

from __future__ import annotations

import random
import re

from ..llm import LLMBackend
from ..safety import has_send_intent
from .tts import detect_language

# ----------------------------------------------------------------------------
# Acknowledgment prompts and phrases
#   The optional spoken 'Right away, Sir' while the answer is made. Off by default: the owner found
#   it annoying, and it cost one extra request per spoken question.
# ----------------------------------------------------------------------------

ACK_PROMPT = """The user just spoke a request to their personal assistant. Another process is handling it.
Reply with ONE short, natural spoken acknowledgment, under 12 words, in {language}.
Do not answer the question. Do not claim anything is done, found or sent. Do not promise results.
Example: {example}"""

LANGUAGES = {
    "en": ("English", "Sure, checking your brother's messages now."),
    "ar": ("Egyptian colloquial Arabic (عامية مصرية)", "حاضر، بشوف رسايل أخوك دلوقتي."),
}

CANNED = {
    "en": ["Got it, working on it.", "Sure, one moment.", "On it.", "Okay, checking now."],
    "ar": ["حاضر، لحظة.", "تمام، بشتغل عليها.", "ثواني بس.", "حاضر، بشوف دلوقتي."],
    "ar_saudi": ["أبشر، لحظة.", "تم، جاري التحقق.", "حاضر، ثواني.", "أبشر، أشوفها الحين."],
}

BUTLER = {
    "en": ["Right away, Sir.", "Very good, Sir. One moment.", "Certainly, Sir.", "At once, Sir."],
    "ar": ["حاضر يا Sir، لحظة واحدة.", "تحت أمرك يا Sir، ثواني.", "حالًا يا Sir.", "Sir، ثواني وأشوفهالك."],
    "ar_saudi": ["أبشر يا Sir، لحظة.", "تحت أمرك يا Sir.", "حالًا يا Sir.", "Sir، ثواني وأشوفها."],
}
BUTLER_PROMPT = ("\nSpeak as a refined, polite English butler and start with \"Sir\" "
                 "(in Arabic too, e.g. \"حاضر يا Sir\").")

# Language name (for the prompt) and canned set per tone, for Arabic requests.
TONE_ARABIC = {
    "egyptian_friendly": ("Egyptian colloquial Arabic (عامية مصرية)", "ar", "حاضر، بشوف رسايل أخوك دلوقتي."),
    "saudi_professional": ("polite Saudi Arabic (لهجة سعودية مهذبة)", "ar_saudi", "أبشر، أشوف رسائل أخوك الحين."),
}


# ----------------------------------------------------------------------------
# Choosing the language
# ----------------------------------------------------------------------------

def ack_language(request: str, tone: str) -> str:
    """'en', 'ar' or 'ar_saudi'. US English tone answers in English even to Arabic."""
    if tone == "us_professional" or detect_language(request) == "en":
        return "en"
    return TONE_ARABIC.get(tone, TONE_ARABIC["egyptian_friendly"])[1]

# ----------------------------------------------------------------------------
# An ack must never claim a result
#   'Sent', 'done' or 'found' would be untrue before the work happens: such acks are dropped.
# ----------------------------------------------------------------------------

# Words that would make the ack claim a result.
_CLAIMS = re.compile(r"\b(sent|sending|done|found|finished|delivered|تم|اتبعت|خلصت|اترسلت|هابعتها|هبعتها|ببعتها)\b", re.I)


def canned_ack(request: str, tone: str = "egyptian_friendly", persona: str = "plain") -> str:
    if has_send_intent(request):
        return ""  # a send pops the confirmation dialog at once; talking over it is just noise
    return random.choice((BUTLER if persona == "butler" else CANNED)[ack_language(request, tone)])


# ----------------------------------------------------------------------------
# Making one
# ----------------------------------------------------------------------------

async def make_ack(request: str, backend: LLMBackend | None, tone: str = "egyptian_friendly",
                   persona: str = "plain") -> str:
    """Model-generated ack, a canned phrase, or "" (say nothing) for sends."""
    if backend is None or has_send_intent(request):
        return canned_ack(request, tone, persona)
    lang = ack_language(request, tone)
    if lang == "en":
        language, example = LANGUAGES["en"]
    else:
        language, _, example = TONE_ARABIC.get(tone, TONE_ARABIC["egyptian_friendly"])
    try:
        text = await backend.complete(
            [
                {"role": "system", "content": ACK_PROMPT.format(language=language, example=example)
                 + (BUTLER_PROMPT if persona == "butler" else "")},
                {"role": "user", "content": request},
            ],
            max_tokens=30,
        )
    except Exception:
        return canned_ack(request, tone, persona)
    text = text.strip().strip('"').splitlines()[0] if text.strip() else ""
    expected = "en" if lang == "en" else "ar"
    if not text or len(text.split()) > 16 or _CLAIMS.search(text) or detect_language(text) != expected:
        return canned_ack(request, tone, persona)
    return text
