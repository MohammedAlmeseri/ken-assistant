# Ken: Personal Assistant for WhatsApp, Gmail and Calendar (Brief v2)

*This brief describes what is built. For how the code works, see [ARCHITECTURE.md](ARCHITECTURE.md); for the engineering story (problems, fixes and measurements), see [ENGINEERING.md](../ENGINEERING.md).*

## 1. Overview

**Ken** is a private desktop assistant with a butler's manners ("Sir"). He reads the owner's WhatsApp chats, Gmail and Google Calendar, writes replies in the owner's own style, and sends only when the owner explicitly asks **and** confirms in a dialog. He can also start timers, set reminders, watch a chat for a reply, give a briefing, and open websites and YouTube videos.

- **Talk to him** by typing, holding the hotkey (`Ctrl+Shift+Space`), or saying **"Ken"** (a wake word trained on the owner's voice).
- **The brain** is OpenAI `gpt-6-luna` (switchable to a local Ollama model).
- **Services** are reached through MCP servers: WhatsApp, Gmail, and Google Calendar (Ken's own server).
- **The app** is a Python/PySide6 desktop app: a floating pill and a chat panel in the "Frost console" design.

AI assistants receiving this document: treat it as the source of truth. Ask the owner before changing decisions in sections 5 (safety) and 9 (privacy).

## 2. What Ken does

| Area | What it does |
|---|---|
| WhatsApp | Read and summarize chats; find a chat by any spelling ("youssef", "يوسف", "3abdo"); draft replies in the owner's style; send after confirmation |
| Gmail | Search, read, summarize; draft and send replies after confirmation |
| Calendar | Read events and free time; propose an event as a card the owner adds with one click (never edits or deletes) |
| Briefing | ☀ button or a daily morning briefing: last 24 h of WhatsApp and email plus today and tomorrow, and a "needs your reply" list |
| Timers | Named pomodoro, stopwatch and countdown timers as floating cards; Ken can start, pause, reset and stop them |
| Reminders | "Remind me to answer Sara at 6"; "tell me when Youssef replies" (checked every minute) |
| Web | Open sites, Google/YouTube searches, play a YouTube video (YouTube Data API) |
| Voice | Wake word "Ken", "At your service, Sir.", speech to text, natural voices, "Ken, stop" |
| Inbox | Red dot and a "new messages" popup without AI; Reply drafts at once |
| About himself | Answers questions about how he works, settings, privacy and costs from this brief and ARCHITECTURE.md (RAG, `about_ken`) |
| Memory | "Remember Youssef's birthday is 3 May", "I take my coffee black": saved locally, used when relevant; yearly dates remind you on the day (10:00) and appear in the briefing; "forget…" or Settings → Memory removes them |
| People | "Who is Ali?", "what's going on with Sara?", "catch me up on uni-friends": a short summary plus a person card (messages each way, what's waiting, reply time) with reply / watch / remind me / ★ |
| Voice notes | "What did Ahmed say in his voice note?": transcribed only when you ask (Arabic too), cached, and shown as text in summaries afterwards; the sender sees no "played" mark |
| Shortcuts | Your own words for a person, group or email: "send a message to my mom" (or "ابعت لماما") goes to exactly her chat, "what's new in uni", "email my boss". Add by saying "mom is ماما" (a card, saved on click), in Settings → Shortcuts, or with ＋ shortcut on a person card; contacts saved as Mom / ماما / Dad… are suggested once. One shortcut = one person, group or address, never several people |
| Follow-up | After Ken answers something you said, he keeps listening ~8 s, so "and reply yes to him" works without "Ken"; "thanks" ends it |
| Nudges | Ken speaks up on his own, at most 4 times a day: "Sir, Youssef asked twice about class (3 h ago)", unanswered questions you asked, and "class starts in 20 minutes"; quiet 23:00–09:00 and during Pomodoro focus |

Ken does **not**: reply automatically, create drafts inside Gmail/WhatsApp, delete/archive/label anything, manage contacts or groups, or edit/delete calendar events.

## 3. Hardware (corrected)

| Part | Actual |
|---|---|
| CPU | Intel Core i7-10510U (4 cores / 8 threads, 15 W laptop chip) |
| RAM | 16 GB |
| GPU | NVIDIA MX130, 4 GB VRAM: too weak and unstable for local models ("CUDA error: the launch timed out") |

Treat the machine as **CPU-only**. Local models: `qwen3:1.7b` (~30 s per answer, weaker at tools and Arabic) as a private fallback only.

## 4. Models and services

| Job | Choice | Notes |
|---|---|---|
| Thinking, tools, drafts | OpenAI **`gpt-6-luna`**, reasoning effort `none` | $0.10 / $0.50 per 1M tokens. Replaced `gpt-4.1-mini` (shuts down 14 Jan 2027); closer to the owner's real replies in the evals |
| Local fallback | Ollama `qwen3:1.7b`, CPU only | Private, slow |
| Speech to text | OpenAI **`gpt-transcribe`** ($0.0045/min) | Falls back to local `faster-whisper` (base) |
| Voice | **Microsoft Edge neural voices** (free): `en-GB-RyanNeural` (British butler), `ar-EG-ShakirNeural` (Egyptian) | Windows voices as fallback; OpenAI `gpt-4o-mini-tts` still selectable until it shuts down on 6 Jan 2027 |
| Embeddings (style memory) | `text-embedding-3-small` | Local hash embedder as fallback |
| Wake word | openWakeWord feature models + a classifier trained on the owner's voice | Fully offline |
| WhatsApp | `sealjay/mcp-whatsapp` (Go, HTTP 127.0.0.1:8765) | Unofficial client: ban risk; avoid bulk sends |
| Gmail | `@gongrzhe/server-gmail-autoauth-mcp@1.1.11` from a local copy (`tools/gmail-mcp`) | npx was too slow to start |
| Calendar | `assistant/calendar_mcp.py` (Ken's own MCP server) | Same Google OAuth client as Gmail |
| YouTube | YouTube Data API v3 (`YOUTUBE_API_KEY` in `.env`) | Without a key, opens the results page |

## 5. Safety rules (enforced in code, not only in the prompt)

1. **Send gate.** Every send shows a confirmation dialog with the exact channel, recipient and text, and runs only after a click. Enter never sends.
2. **Explicit intent.** A send is only allowed when the owner's latest message asks for it (`has_send_intent`).
3. **Untrusted content.** All message, email, calendar and video-title text is wrapped as untrusted data and never followed as instructions.
4. **Tool allowlist.** The model sees only read and send tools; delete/archive/label/draft/create/modify tools are hidden by keyword rules. `calendar__create_event` is hidden too: only the app calls it, after the owner clicks **Add** on an event card.
5. **No new recipients.** A send can only go to someone the owner typed or who appeared in what Ken read (`RecipientGuard`).
6. **Links.** Ken opens a site only if the owner named it, or it is a Google/YouTube address Ken built; redirects (`google.com/url`) and non-web links are refused. Links from messages become a card with **Open**.
7. **Rate limits.** At most 3 WhatsApp and 5 email sends a minute.
8. **Audit log.** Every send and every calendar add is logged locally (`data/audit.jsonl`).
9. **Secrets stay out of git.** `.env`, OAuth keys and tokens, the WhatsApp session, `settings.json` and `data/` are ignored.
10. **No surprise spending.** Tests can never reach the OpenAI API (a guard fails any real request). Every real request is logged in `data/openai_requests.jsonl`.
11. **Owner-only memory.** Facts are saved or deleted only when the owner's own latest message says remember / forget; chat text can never add one.
12. **Voice notes on request only.** The download tool is hidden from the model; Ken transcribes a voice note only when the owner's message asks about one.
13. **Nudges never send.** They only show a card (and speak for ★ people); "Draft reply" still ends in a normal draft and confirmation.
14. **Shortcuts pick who, never send.** Saved only with a click; one word means one chat or address; no multi-person shortcuts (WhatsApp limits bulk sending, and Ken's own limit is 3 WhatsApp sends a minute). Sends still need your request and the confirmation dialog.

## 6. How Ken writes like the owner

- **Style memory (RAG):** 985 "their message → owner's reply" pairs learned from the 15 most-messaged chats, plus every message sent through Ken.
- **Each WhatsApp draft gets:**
  - the 3 most similar past exchanges in that chat;
  - the last 24 h of the conversation with timestamps;
  - a length target and the chat's line-split habit.
- **Rules:** no full stop at the end, no emoji unless the owner uses them, no excuses or promises the owner didn't give, and the Arabic question mark (؟).
- **Measured** in `evals/RESULTS.md`: the judge picks the new drafts 32% vs 14% for the old ones, and length overshoot dropped from +45% to +8%.

## 7. Voice

- **Three ways in:** hold the hotkey, click 🎙, or say **"Ken"**.
- **After "Ken":**
  - Ken says "At your service, Sir." (made once per voice and cached);
  - he records until a short silence, transcribes, and answers out loud;
  - the panel stays closed.
- **Wake word:**
  - Trained in ~3 minutes on 25 takes of "Ken", a short read-aloud text with look-alike words, and room noise. Windows voices add more "not Ken" examples.
  - It fires only when the speech detector agrees (no sniffs or clicks).
- **"Ken, stop":** saying his name while he talks cuts him off. "stop", "خلاص" or "اسكت" right after it just stop him.
- **Answers:** one short spoken line; drafts are never read aloud; a send is confirmed only by a click.
- **Language:** Ken answers in the language of the owner's message, even when the chats he read are Arabic.

## 8. Personality and design

- **Personality:**
  - *Butler* (default): calm, British, starts with "Sir".
  - *Plain*: no butler manner.
  - Either way it never touches drafts.
- **Writing tone** for drafts and Arabic replies: Egyptian friendly (default), polite Saudi, or US English.
- **Frost console design:**
  - Light frosted glass on "console paper" (a faint dot grid) with a mint accent and monospace breadcrumbs.
  - A colour per channel (WhatsApp green, email coral, calendar blue, links amber).
  - A terminal-style status line and keyboard hints.
- **Ken's avatar** is a plexus of linked particles:
  - a turning sphere when idle;
  - an ear when listening;
  - a **gyroscope** of three orbiting rings when thinking;
  - a sound ribbon when speaking;
  - a check mark when done;
  - a dozing state after 10 idle minutes.

## 9. Privacy

- With OpenAI as the brain, the text of messages and emails Ken reads goes to OpenAI.
- With OpenAI speech to text, recorded requests (including follow-ups) and voice notes you ask about go to OpenAI. The microphone is otherwise only checked **locally** for "Ken".
- Saved facts stay in `data/memory.db`; the few related to a request are sent with it. Nudges send the waiting messages for a one-word check (a few times a day).
- With Edge voices, the text of spoken replies goes to Microsoft.
- Style memory, the wake-word model, recordings, reminders, logs and all settings stay on the machine.
- The local backend keeps everything on the machine (slow).

## 10. Costs (as measured)

- **Normal use:** about 300 requests over two days; the old instant "got it" was a third of voice requests and is now off by default.
- **Cost per million tokens:** `gpt-6-luna` costs a quarter of what `gpt-4.1-mini` did.
- **Edge voices** are free.
- **The panel's footer** shows tokens, voice minutes, the session's cost and today's request count.

## 11. Settings (sidebar sections)

- **General:**
  - model (OpenAI / local);
  - speech to text (same as model / OpenAI / local Whisper);
  - writing tone;
  - personality.
- **Voice:**
  - engine (Edge / OpenAI / Windows), with an English and an Arabic voice and ▶ previews;
  - answer out loud;
  - read typed replies;
  - quick "got it";
  - speed.
- **Wake word:** listen for "Ken"; teach Ken my voice; keep listening after answers (off / 5 / 8 / 12 s).
- **Accounts:** connect Google Calendar; start the WhatsApp server with Ken.
- **Memory:** learn from WhatsApp history; things Ken remembers (with delete); birthday reminder time.
- **Shortcuts:** your shortcuts (remove), add one (words + chat name or email), suggestions from your contacts.
- **Nudges:** on/off; at most 2 / 4 / 6 a day; quiet hours.
- **App:** show the pill; morning briefing time; hotkey.

## 12. Status and next steps

**Done:**
- everything in section 2;
- the Frost redesign;
- the move off deprecated models (Luna, gpt-transcribe, Edge voices);
- the OpenAI request log and the offline test guard;
- "remember that…" with yearly reminders, person and group summaries, voice notes on request, the follow-up listening window, and proactive nudges, and shortcuts (151 offline tests).

**Next:**
1. **Safety check on `gpt-6-luna`.** Prompt-injection, unrequested sends, groundedness and chat finding were last measured on `gpt-4.1-mini`. Ready to run: `python evals/run_eval.py grounded finding` (~100 requests, ~$0.02); ask the owner first.
2. **Replace `gpt-4o-mini-tts`** for anyone still on OpenAI voices before 6 Jan 2027: `gpt-realtime-2.1-mini` is the official successor.
3. **Line breaks:** drafts split into two lines more often than the owner (44% vs 28%); deciding the split in code would match it.
4. **Optional:** a gpt-live-1 "live conversation" mode; a light/dark switch for Frost.

## 13. Risks

- **WhatsApp ban** (unofficial client).
- **Privacy** of text sent to OpenAI and Microsoft.
- **Google tokens** may expire in Testing mode.
- **The Edge voice service** is unofficial and could stop working (Windows voices take over).
- **Wake word:** "Ken" is short, so a rare false start is possible (retrain from the saved recordings).
- **Model shutdowns** (the dates above).
- **Tool names** can change when MCP servers update.

## 14. References

- Model Context Protocol: https://modelcontextprotocol.io
- WhatsApp MCP: https://github.com/sealjay/mcp-whatsapp
- Gmail MCP: https://github.com/GongRzhe/Gmail-MCP-Server
- openWakeWord: https://github.com/dscripka/openWakeWord
- edge-tts: https://github.com/rany2/edge-tts
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- OpenAI pricing and deprecations: https://developers.openai.com/api/docs/pricing, https://developers.openai.com/api/docs/deprecations
