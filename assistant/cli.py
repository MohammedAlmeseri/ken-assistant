"""Command-line front end for testing the backend before the desktop UI exists.

    python -m assistant --mock            # fake WhatsApp/Gmail data, real LLM
    python -m assistant                   # real MCP servers from mcp_servers.json
    python -m assistant --backend local   # use Ollama instead of OpenAI
    python -m assistant --voice --speak   # push-to-talk + read replies aloud
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys

from .agent import Agent, SendRequest, TurnResult
from .config import Settings, load_settings
from .llm import OpenAIBackend, Usage, make_backend
from .mcp_client import MCPManager
from .mock_tools import MockToolSource

# ----------------------------------------------------------------------------
# Command-line chat
#   The terminal version of Ken: same agent and safety rules, plus /commands for testing.
# ----------------------------------------------------------------------------

HELP = """Commands:
  /backend openai|local   switch model backend
  /voice on|off           push-to-talk input (empty Enter starts recording)
  /speak on|off           read replies aloud
  /ack on|off             instant acknowledgment
  /stt openai|local|<model>  speech-to-text: OpenAI (~1s, best Arabic) or local Whisper
  /whisper base|small     local Whisper model (base ~3s, small ~11s but better Arabic)
  /tts                    voices · /tts engine openai|windows · /tts openai marin
                          /tts en mark · /tts ar hoda · /tts rate 1.2
  /stop                   stop speaking
  /usage                  token usage and estimated cost so far
  /tools                  show the tools the model can use
  /drafts                 show drafts from this session
  /reset                  start a new conversation
  /quit"""


# ----------------------------------------------------------------------------
# Input and the confirmation prompt
#   A send in the terminal must be confirmed by typing 'yes'.
# ----------------------------------------------------------------------------

async def ask(prompt: str) -> str:
    return (await asyncio.to_thread(input, prompt)).strip()


async def confirm_send(req: SendRequest) -> bool:
    print("\n" + "=" * 60)
    print("  CONFIRM SEND")
    print(f"  Channel:   {req.channel}")
    print(f"  To:        {', '.join(req.recipients)}")
    if req.subject:
        print(f"  Subject:   {req.subject}")
    print(f"  Text:\n    {req.text.replace(chr(10), chr(10) + '    ')}")
    print(f"  Tool:      {req.tool}  args={json.dumps(req.arguments, ensure_ascii=False)}")
    print("=" * 60)
    answer = await ask("Type 'yes' to send, anything else cancels: ")
    return answer.lower() == "yes"


# ----------------------------------------------------------------------------
# Printing results and costs
# ----------------------------------------------------------------------------

def print_result(result: TurnResult) -> None:
    if result.text:
        print(f"\nAI: {result.text}")
    for d in result.drafts:
        print(f"\n  ┌─ Draft {d.id} · {d.channel} → {d.recipient_name or ''} {d.recipient}")
        if d.subject:
            print(f"  │ Subject: {d.subject}")
        for line in d.text.splitlines() or [""]:
            print(f"  │ {line}")
        print("  └─")
    for s in result.sent:
        print(f"\n  ✔ Sent via {s.channel} to {', '.join(s.recipients)}")
    print()


def format_usage(usage: Usage, model: str, settings: Settings, local: bool) -> str:
    if usage.calls == 0:
        return "0 calls"
    cached = f" ({usage.cached:,} cached)" if usage.cached else ""
    line = f"{usage.input:,} in{cached} · {usage.output:,} out · {usage.calls} call{'s' * (usage.calls != 1)}"
    if local:
        return line + " · local, free"
    cost = usage.cost(settings.prices.get(model))
    return line + (f" · ≈${cost:.4f}" if cost is not None else " · (no price set for this model)")


# ----------------------------------------------------------------------------
# Voice in the terminal
#   Push-to-talk with Enter, optional spoken replies.
# ----------------------------------------------------------------------------

def make_ack_backend(settings: Settings):
    if settings.backend != "openai" or settings.ack_style != "model":
        return None  # local backend uses canned phrases: a CPU model is too slow to help
    return OpenAIBackend(settings, model=settings.ack_model, purpose="ack")


async def voice_input(recorder, transcriber) -> str | None:
    await ask("🎙  Press Enter to start talking…")
    recorder.start()
    await ask("🔴 Recording. Press Enter to stop.")
    audio = recorder.stop()
    print("  · Transcribing…")
    text, lang = await transcriber.transcribe(audio)
    if not text:
        print("  (heard nothing)")
        return None
    print(f"  You said [{lang}]: {text}")
    edit = await ask("  Enter = send · type a correction · 'x' = cancel: ")
    if edit.lower() == "x":
        return None
    return edit or text


# ----------------------------------------------------------------------------
# The chat loop
#   Connects the MCP servers (or the mock), builds the agent, then reads lines until /quit.
# ----------------------------------------------------------------------------

async def run(settings: Settings, mock: bool) -> None:
    tools_cm = contextlib.nullcontext(MockToolSource()) if mock else MCPManager(settings.mcp_config_path)
    async with tools_cm as tools:
        if isinstance(tools, MCPManager):
            for name, err in tools.errors.items():
                print(f"⚠  MCP server '{name}' failed to start: {err}")

        from .config import DATA_DIR
        from .memory import make_memory

        memory = make_memory(DATA_DIR / ("memory-mock.db" if mock else "memory.db"), settings.backend)
        agent = Agent(make_backend(settings), tools, settings, confirm_send, on_status=lambda s: print(f"  · {s}"),
                      memory=memory)
        await agent.start()
        from .knowledge import ensure_index

        try:  # Ken's own docs, for the about_ken tool (re-embedded only when they change)
            await ensure_index(memory)
        except Exception as exc:
            print(f"  ⚠  couldn't index Ken's docs: {exc}")

        speech = recorder = transcriber = None
        session = None
        session_usage = Usage()  # LLM tokens only; speech-to-text is billed per audio minute, not counted here
        # Shared with the speech engine and read on every sentence, so /tts changes apply at once.
        tts_prefs = {"en": settings.tts_voice_en, "ar": settings.tts_voice_ar, "rate": settings.tts_rate,
                     "engine": settings.tts_engine, "openai_voice": settings.tts_openai_voice,
                     "openai_model": settings.tts_openai_model}

        def openai_tts_usage() -> str:
            engine = getattr(speech, "_engine", None)
            used = getattr(engine, "_openai", None)
            if not used or not used.chars:
                return ""
            return f"  OpenAI voice so far: {used.chars:,} characters → {used.audio_seconds:.0f}s of audio\n"

        def ensure_voice_output():
            nonlocal speech, session
            from .voice.session import VoiceSession
            from .voice.tts import SpeechQueue, default_engine

            if speech is None:
                speech = SpeechQueue(lambda: default_engine(tts_prefs))
                voices = ", ".join(f"{lang}: {name}" for lang, name in sorted(speech.current_voices().items()))
                if tts_prefs["engine"] == "openai":
                    print(f"  (voice: OpenAI {tts_prefs['openai_voice']}, reply text is sent to OpenAI; "
                          f"Windows fallback: {voices or 'none'} · change with /tts)")
                else:
                    print(f"  (voice: Windows, {voices or 'no voices'} · change with /tts)")
                if "ar" not in speech.languages:
                    print("  ⚠  No Arabic voice installed. Add one in Windows Settings > Time & language > Speech > Add voices.")
            session = VoiceSession(agent, speech, settings, make_ack_backend(settings))

        def ensure_voice_input(rebuild: bool = False):
            nonlocal recorder, transcriber
            from .voice.stt import OpenAITranscriber, Recorder, make_transcriber

            if recorder is None:
                recorder = Recorder()
            if transcriber is None or rebuild:
                transcriber = make_transcriber(settings)
                if isinstance(transcriber, OpenAITranscriber):
                    print(f"  · Speech-to-text: OpenAI {settings.openai_stt_model} (audio is sent to OpenAI)")
                else:
                    print(f"  · Speech-to-text: local Whisper '{settings.whisper_model}' (first time downloads it)…")
                    transcriber.warm_up()

        if settings.speak_replies:
            ensure_voice_output()
        if settings.voice_input:
            ensure_voice_input()

        print(f"Backend: {agent.backend.name} ({getattr(agent.backend, 'model', '')}) · tools: {len(agent.specs) - 1}"
              f"{' · MOCK DATA' if mock else ''}")
        print("Type /help for commands.\n")

        while True:
            try:
                if settings.voice_input:
                    line = await ask("You (Enter = talk, or type): ")
                    if not line:
                        line = await voice_input(recorder, transcriber)
                        if not line:
                            continue
                else:
                    line = await ask("You: ")
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue

            if line.startswith("/"):
                cmd, _, arg = line.partition(" ")
                arg = arg.strip().lower()
                try:
                    if cmd == "/quit":
                        break
                    elif cmd == "/help":
                        print(HELP)
                    elif cmd == "/backend":
                        settings.backend = arg
                        agent.backend = make_backend(settings)
                        if session:
                            ensure_voice_output()
                        if settings.voice_input and settings.stt_backend == "auto":
                            ensure_voice_input(rebuild=True)  # audio follows text: local LLM -> local STT
                        print(f"  Backend: {agent.backend.name}")
                    elif cmd == "/voice":
                        settings.voice_input = arg == "on"
                        if settings.voice_input:
                            ensure_voice_input()
                    elif cmd == "/speak":
                        settings.speak_replies = arg == "on"
                        if settings.speak_replies:
                            ensure_voice_output()
                    elif cmd == "/stt":
                        if arg in ("openai", "local", "auto"):
                            settings.stt_backend = arg
                        elif arg:
                            settings.openai_stt_model, settings.stt_backend = arg, "openai"
                        if settings.voice_input:
                            await asyncio.to_thread(ensure_voice_input, True)
                        else:
                            print(f"  Speech-to-text: {settings.stt_backend} (applies when /voice is on)")
                    elif cmd == "/whisper":
                        settings.whisper_model = arg or settings.whisper_model
                        settings.stt_backend = "local"
                        if settings.voice_input:
                            await asyncio.to_thread(ensure_voice_input, True)
                    elif cmd == "/ack":
                        settings.instant_ack = arg == "on"
                    elif cmd == "/stop":
                        if speech:
                            speech.stop()
                    elif cmd == "/tts":
                        if speech is None:
                            ensure_voice_output()
                        parts = arg.split()
                        if len(parts) == 2 and parts[0] == "engine" and parts[1] in ("openai", "windows"):
                            settings.tts_engine = tts_prefs["engine"] = parts[1]
                            if parts[1] == "openai":
                                print("  Replies will be spoken by OpenAI (paid; the reply text is sent to OpenAI).")
                        elif len(parts) == 2 and parts[0] == "openai":
                            settings.tts_openai_voice = tts_prefs["openai_voice"] = parts[1]
                            settings.tts_engine = tts_prefs["engine"] = "openai"
                        elif len(parts) == 2 and parts[0] == "rate":
                            settings.tts_rate = tts_prefs["rate"] = max(0.5, min(3.0, float(parts[1])))
                        elif len(parts) == 2 and parts[0] in ("en", "ar"):
                            names = [n for n, lang in speech.voice_list if lang.lower().startswith(parts[0])]
                            if not any(parts[1] in n.lower() for n in names):
                                print(f"  No {parts[0]} voice matching '{parts[1]}'. Installed: {', '.join(names) or 'none'}")
                                continue
                            tts_prefs[parts[0]] = parts[1]
                            setattr(settings, f"tts_voice_{parts[0]}", parts[1])
                        elif parts:
                            print("  Usage: /tts · /tts engine openai|windows · /tts openai marin · "
                                  "/tts en mark · /tts ar hoda · /tts rate 1.2")
                            continue
                        engine_on = tts_prefs["engine"]
                        print(f"  engine: {engine_on}" + (f" (voice {tts_prefs['openai_voice']}; options: marin cedar alloy ash ballad "
                                                          "coral echo fable nova onyx sage shimmer verse)" if engine_on == "openai" else ""))
                        current = speech.current_voices()
                        print("  Windows voices" + (" (fallback):" if engine_on == "openai" else ":"))
                        for name, lang in speech.voice_list:
                            mark = " ← in use" if name in current.values() else ""
                            print(f"    {name:22} {lang}{mark}")
                        print(f"  speed: {tts_prefs['rate']}  (to keep choices, put tts_engine / tts_openai_voice / "
                              "tts_voice_en / tts_voice_ar / tts_rate in settings.json)")
                        print(openai_tts_usage(), end="")
                        if parts and parts[0] in ("en", "ar", "rate", "engine", "openai"):
                            speech.say("هكذا يبدو صوتي الآن." if parts[0] == "ar" else "This is how I sound now.")
                    elif cmd == "/usage":
                        print(f"  session total: {format_usage(session_usage, agent.backend.model, settings, agent.backend.name == 'local')}")
                        print(openai_tts_usage(), end="")
                    elif cmd == "/tools":
                        for spec in agent.specs:
                            print(f"  {agent.kinds.get(spec.name, 'local'):6} {spec.name}")
                    elif cmd == "/drafts":
                        for d in agent.drafts:
                            print(f"  {d.id} {d.channel} → {d.recipient}: {d.text[:80]}")
                    elif cmd == "/reset":
                        agent.reset()
                        print("  New conversation.")
                    else:
                        print("  Unknown command. /help")
                except Exception as exc:
                    print(f"  Error: {exc}")
                continue

            try:
                if settings.speak_replies and session:
                    result = await session.handle(line)
                else:
                    result = await agent.run(line)
            except Exception as exc:
                print(f"\n  Error: {type(exc).__name__}: {exc}\n")
                continue
            print_result(result)
            local = agent.backend.name == "local"
            session_usage = session_usage + result.usage + result.ack_usage
            turn = result.usage + result.ack_usage
            print(f"  tokens this turn: {format_usage(turn, agent.backend.model, settings, local)}"
                  f"{'  (ack included)' if result.ack_usage.calls else ''}")
            print(f"  session total:    {format_usage(session_usage, agent.backend.model, settings, local)}")
            print(openai_tts_usage())


# ----------------------------------------------------------------------------
# Entry point
#   python -m assistant [--mock] [--backend local] [--voice --speak]
# ----------------------------------------------------------------------------

def main() -> None:
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="assistant")
    parser.add_argument("--mock", action="store_true", help="use fake WhatsApp/Gmail data")
    parser.add_argument("--backend", choices=["openai", "local"])
    parser.add_argument("--voice", action="store_true", help="push-to-talk input")
    parser.add_argument("--speak", action="store_true", help="read replies aloud")
    args = parser.parse_args()

    settings = load_settings()
    if args.backend:
        settings.backend = args.backend
    settings.voice_input = settings.voice_input or args.voice
    settings.speak_replies = settings.speak_replies or args.speak
    try:
        asyncio.run(run(settings, args.mock))
    except KeyboardInterrupt:
        pass
