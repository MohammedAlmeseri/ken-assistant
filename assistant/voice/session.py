"""One spoken turn: run the main agent call and the ack call in parallel, speak in order."""

from __future__ import annotations

import asyncio

from ..agent import Agent, TurnResult
from ..config import Settings
from ..llm import LLMBackend, Usage
from .ack import make_ack
from .tts import SpeechQueue, detect_language


# ----------------------------------------------------------------------------
# What gets said
#   ONE short line per turn; a draft's text is never read aloud (it's on screen).
# ----------------------------------------------------------------------------

def spoken_reply(result: TurnResult, request: str = "", tone: str = "egyptian_friendly",
                 persona: str = "plain") -> str:
    """ONE short line per turn. Drafts are on screen, so their text is never read aloud,
    and after a send only "Sent." is said (the model's longer summary is skipped)."""
    arabic = detect_language(request or result.text) == "ar" and tone != "us_professional"
    butler = persona == "butler"
    if result.sent:
        if arabic:
            return ("تم الإرسال" if tone == "saudi_professional" else "اتبعت") + (" يا Sir." if butler else ".")
        return "Sent, Sir." if butler else "Sent."
    if result.text.strip():
        return result.text
    if result.drafts:
        who = result.drafts[0].recipient_name
        if arabic:
            return "Sir، الرد جاهز، أبعته؟" if butler else "الرد جاهز، أبعته؟"
        if butler:
            return f"Sir, the draft for {who} is ready. Shall I send it?" if who else "Sir, your draft is ready. Shall I send it?"
        return f"Draft for {who} is ready. Send it?" if who else "Your draft is ready. Send it?"
    return ""


# ----------------------------------------------------------------------------
# A spoken turn
#   Runs the agent with the voice rules and queues the reply for speech.
# ----------------------------------------------------------------------------

class VoiceSession:
    def __init__(self, agent: Agent, speech: SpeechQueue, settings: Settings, ack_backend: LLMBackend | None):
        self.agent = agent
        self.speech = speech
        self.settings = settings
        self.ack_backend = ack_backend if settings.ack_style == "model" else None

    async def handle(self, text: str, speak: bool | None = None) -> TurnResult:
        """speak=None follows settings.speak_replies; the app passes True for spoken requests."""
        speak = self.settings.speak_replies if speak is None else speak
        self.speech.stop()  # barge-in: a new request cuts off whatever is playing
        ack_before = Usage() + self.ack_backend.usage if self.ack_backend else Usage()
        main = asyncio.create_task(self.agent.run(text, voice=speak))

        if self.settings.instant_ack and speak:
            ack = asyncio.create_task(make_ack(text, self.ack_backend, self.settings.tone, self.settings.persona))
            # If the main answer is fast, an ack would just be noise.
            done, _ = await asyncio.wait({main}, timeout=self.settings.ack_skip_if_faster_than)
            if not done:
                await asyncio.wait({main, ack}, return_when=asyncio.FIRST_COMPLETED)
                if ack.done() and not main.done() and not ack.cancelled() and ack.result():
                    self.speech.say(ack.result())
            if not ack.done():
                ack.cancel()

        result = await main
        if self.ack_backend:
            result.ack_usage = self.ack_backend.usage - ack_before
        if speak:
            # The queue keeps order, so the reply plays after the ack finishes.
            self.speech.say(spoken_reply(result, text, self.settings.tone, self.settings.persona))
        return result
