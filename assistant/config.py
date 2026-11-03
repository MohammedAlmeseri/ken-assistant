"""Settings. Defaults live here; settings.json and environment variables override them."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

from dotenv import load_dotenv

# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


# ----------------------------------------------------------------------------
# Settings
#   Every default lives here. settings.json (written by the Settings window) and environment
#   variables override them; unknown keys are ignored.
# ----------------------------------------------------------------------------

@dataclass
class Settings:
    # Model backend
    backend: str = "openai"  # "openai" | "local"
    # gpt-6-luna replaces gpt-4.1-mini (shuts down 14 Jan 2027): closer to the user's real replies
    # in evals/RESULTS.md at ~1/4 the price, ~0.5 s slower per call.
    openai_model: str = "gpt-6-luna"
    ack_model: str = "gpt-6-luna"
    # qwen3:1.7b: ~30 s per answer on this laptop (it reuses the repeated prompt; qwen3.5's hybrid
    # design can't, so qwen3.5:4b took minutes per call). Weaker at tools and Arabic than OpenAI.
    local_model: str = "qwen3:1.7b"
    ollama_host: str = "http://127.0.0.1:11434"
    # Measured on this laptop: the MX130 (4 GB) makes Ollama spill the model into shared memory,
    # which is 3x slower than the CPU and crashes long prompts ("CUDA error: the launch timed
    # out"). 0 = CPU only. Raise it only on a real GPU.
    local_gpu_layers: int = 0
    num_ctx: int = 8192  # local only; OpenAI ignores it. 8k fits our prompt plus a few tool results
    temperature: float = 0.5  # ignored by reasoning models (gpt-5/gpt-6), which only take the default
    # Reasoning models only: "none" answers fastest (voice mode); "low"/"medium" think first and cost more.
    reasoning_effort: str = "none"
    max_steps: int = 6

    # Tool output trimming (keeps the context window small)
    max_item_chars: int = 2000
    max_list_items: int = 10
    max_tool_result_chars: int = 12000

    # How the assistant writes replies and drafts: "egyptian_friendly", "saudi_professional", "us_professional"
    tone: str = "egyptian_friendly"
    # How Ken talks to YOU (never how drafts are written): "butler" (a refined English butler who
    # starts with "Sir") or "plain".
    persona: str = "butler"

    # Safety
    require_send_intent: bool = True
    whatsapp_sends_per_minute: int = 3
    email_sends_per_minute: int = 5

    # Voice
    voice_input: bool = False
    speak_replies: bool = False  # read every reply aloud, typed or spoken
    speak_after_voice: bool = True  # answer out loud when the request was spoken
    voice_language: str = "auto"  # "auto" | "ar" | "en"
    # Speech-to-text: "auto" (OpenAI when backend is openai, else local), "openai" or "local".
    stt_backend: str = "auto"
    # $0.0045/min; replaces gpt-4o-transcribe (shuts down 26 Feb 2027). Takes multiple language hints.
    openai_stt_model: str = "gpt-transcribe"
    # Local Whisper on the i7-10510U: base ~3s per short command, small ~11s (better Arabic).
    whisper_model: str = "base"
    whisper_initial_prompt: str = "واتساب، جيميل، WhatsApp, Gmail"
    # Text-to-speech voice per language: part of a Windows voice name ("Mark", "Zira", "Hoda").
    # Empty = the Windows default voice if it speaks that language. Change live with /tts.
    tts_voice_en: str = ""
    tts_voice_ar: str = ""
    tts_rate: float = 1.0  # 0.5 (slow) to 2.0 (fast) is the useful range
    # "windows" (free, local, robotic) or "openai" (natural; the reply text goes to OpenAI and
    # costs ~$0.60/1M text tokens in + $12/1M audio tokens out). Falls back to Windows on errors.
    # "edge" (default): Microsoft Edge's free neural voices (natural; unofficial service, the text goes
    # to Microsoft). Falls back to Windows on errors. gpt-4o-mini-tts ("openai") shuts down 6 Jan 2027.
    tts_engine: str = "edge"
    edge_voice_en: str = "en-GB-RyanNeural"  # British: the butler
    edge_voice_ar: str = "ar-EG-ShakirNeural"  # native Egyptian Arabic
    tts_openai_model: str = "gpt-4o-mini-tts"
    tts_openai_voice: str = "marin"  # marin and cedar are the most natural; the others sound older
    # A spoken "Right away, Sir" while the answer is being made. Off: the user found the extra talk
    # annoying (and it costs one request per spoken question). Settings can turn it back on.
    instant_ack: bool = False
    # Say "Ken" instead of holding the hotkey. Needs a voice model trained once (Settings or tray menu);
    # detection runs on this computer, audio only leaves after the word, like the hotkey.
    wake_word: bool = False
    ack_style: str = "model"  # "model" | "canned"
    ack_skip_if_faster_than: float = 1.5

    # Desktop app
    hotkey: str = "ctrl+shift+space"  # tap: open/close the panel · hold: talk
    hold_to_talk_ms: int = 350
    show_pill: bool = True
    pill_x: int = -1  # where you last dragged the pill (-1 = bottom centre)
    pill_y: int = -1
    autostart_whatsapp: bool = True
    # ☀ briefing by itself once a morning (the first check after this time, before noon), with a
    # short spoken opening line ("Good morning, Sir. Two messages need your reply.").
    morning_brief: bool = False
    morning_brief_time: str = "08:00"  # start whatsapp-mcp serve if it isn't running

    # ------------------------------------------------------------------------
    # Memory, people, follow-up and nudges
    # ------------------------------------------------------------------------
    # "Remember Youssef's birthday is 3 May": the yearly reminder fires at this time on the day
    # (the briefing mentions it too).
    birthday_time: str = "10:00"
    # After Ken answers a spoken request out loud, keep listening this many seconds for a follow-up
    # without "Ken" (0 = off). Needs "Listen for Ken" on, which keeps the microphone open.
    follow_up_seconds: int = 8
    # Proactive nudges ("Sir, Youssef asked twice about class"). Rules in code, then a tiny AI check
    # per new candidate (a few requests a day). Never sends anything.
    nudges: bool = True
    nudge_max_per_day: int = 4
    nudge_quiet: str = "23:00-09:00"  # no nudges in this window (may wrap past midnight)
    nudge_hours_close: float = 3  # unanswered this long, for the ~15 people you message most
    nudge_hours_other: float = 24  # for everyone else
    # Chat JIDs starred ★ on a person card: their nudges are spoken aloud, others are a card only.
    important_people: list[str] = field(default_factory=list)

    # USD per 1M tokens: (input, cached input, output). Used only for the CLI's cost estimate;
    # check current prices at https://openai.com/api/pricing and edit if they change.
    prices: dict[str, list[float]] = field(default_factory=lambda: {
        "gpt-4.1-mini": [0.40, 0.10, 1.60],  # shuts down 14 Jan 2027
        "gpt-6-luna": [0.10, 0.01, 0.50],
        "gpt-6.1-terra": [1.00, 0.05, 5.00],
    })
    # USD per minute of audio, for the voice part of the same estimate.
    audio_prices: dict[str, float] = field(default_factory=lambda: {
        "gpt-transcribe": 0.0045,
        "gpt-4o-transcribe": 0.006,
        "gpt-4o-mini-tts": 0.015,  # $0.60/1M text in + $12/1M audio out ≈ 1.5 cents a minute
    })

    # Files
    mcp_config_path: str = str(PROJECT_ROOT / "mcp_servers.json")
    audit_log_path: str = str(DATA_DIR / "audit.jsonl")

    # Optional explicit tool lists (exact prefixed names, e.g. "gmail__send_email").
    # When set they override the keyword heuristics in tools.py.
    read_tools: list[str] = field(default_factory=list)
    send_tools: list[str] = field(default_factory=list)
    # Exact tool names that are always hidden, whatever the rules above say.
    hidden_tools: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------
# Saving and loading
# ----------------------------------------------------------------------------

def save_settings(changes: dict, path: Path | None = None) -> None:
    """Merge changes into settings.json (only known fields), keeping what's already there."""
    path = path or PROJECT_ROOT / "settings.json"
    known = {f.name for f in fields(Settings)}
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data.update({k: v for k, v in changes.items() if k in known})
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_settings(path: Path | None = None) -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    settings = Settings()
    path = path or PROJECT_ROOT / "settings.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(Settings)}
        for key, value in data.items():
            if key in known:
                setattr(settings, key, value)
    if os.getenv("ASSISTANT_BACKEND"):
        settings.backend = os.environ["ASSISTANT_BACKEND"]
    if os.getenv("OLLAMA_HOST"):
        settings.ollama_host = os.environ["OLLAMA_HOST"]
    return settings
