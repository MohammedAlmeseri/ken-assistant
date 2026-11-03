# Ken: how everything works

This is the map of the code: which process does what, how a request flows through Ken, and where each feature lives. For goals, decisions and status, see [PROJECT_BRIEF.md](PROJECT_BRIEF.md).

## 1. The big picture

```
┌──────────────────────────── Ken desktop app (one Python process) ────────────────────────────┐
│                                                                                              │
│  UI thread (Qt / PySide6)                          Backend thread (asyncio loop)             │
│  ─────────────────────────                         ──────────────────────────────            │
│  App ─ wires everything                            Agent ─ the LLM tool loop                 │
│  Pill ─ floating plexus capsule        signals     ├─ LLM backend (OpenAI / Ollama)          │
│  Panel ─ chat, cards, input      ◄──────────────►  ├─ MCPManager (WhatsApp, Gmail, Calendar)  │
│  Dialogs ─ send confirm, inbox,      submit()      ├─ VectorStore (style memory)             │
│            settings, wake training                 ├─ ChatDirectory (WhatsApp SQLite)        │
│  TimerBoard ─ floating timer cards                 ├─ TimerManager, Reminders                │
│  Hotkey (pynput)                                   └─ VoiceSession / transcription           │
│                                                                                              │
│  Helper threads: speech queue (TTS), wake-word listener (mic), chimes, greeting audio        │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
        │ HTTP 127.0.0.1:8765            │ stdio                       │ stdio
        ▼                                ▼                             ▼
  whatsapp-mcp (Go, whatsmeow)   Gmail MCP (Node, local copy)   calendar_mcp.py (Python)
  SQLite: whatsapp-mcp/store/    OAuth: ~/.gmail-mcp/           OAuth token: ~/.calendar-mcp/

  Cloud: OpenAI (chat, embeddings, speech to text, optional voice) · Microsoft Edge voices ·
         Google (Gmail, Calendar, YouTube Data API)
```

**Starting Ken:** `Start Assistant.bat` runs `pythonw -m assistant.ui` with no console window. Warnings go to `data/app.log`. A second launch only opens the panel of the running one (`QLocalServer`). There is also a terminal version, `python -m assistant` (`cli.py`), with the same agent.

**Threads:** Qt widgets must stay on the UI thread, and the agent awaits network calls, so the `Controller` runs an asyncio loop on its own thread.
- The UI hands work to the backend with `controller.submit(coroutine)`.
- Results come back as Qt signals (`reply`, `status`, `busy`, `notice`, …), which Qt delivers on the UI thread.
- Stopping a reply cancels its asyncio task, and the agent rolls back that half-finished turn.

## 2. Code map

| Area | Files | What they hold |
|---|---|---|
| Agent | `agent.py` | System prompt, tones and personality, local tools, the tool loop, the send gate, RAG recall |
| Models | `llm.py`, `openai_client.py` | OpenAI and Ollama backends behind one interface; usage counting; the logged OpenAI client factory |
| Tools | `mcp_client.py`, `tools.py`, `mock_tools.py` | MCP connections; the read/send/hidden allowlist; schema trimming; fake tools for `--mock` |
| Safety | `safety.py` | Send-intent check, recipient guard, rate limiter, untrusted wrapping, output trimming, audit log |
| Memory | `memory.py`, `contacts.py`, `facts.py`, `people.py`, `voicenotes.py`, `shortcuts.py` | Vector store for style memory and history learning; WhatsApp chat directory, fuzzy name finder and chat statistics; saved facts ("remember that…"); person and group summaries; voice notes transcribed on request; shortcuts ("mom" → her chat) |
| Features | `calendar_mcp.py`, `timers.py`, `reminders.py`, `briefing.py`, `web.py`, `inbox.py`, `nudges.py` | Calendar server; timers; reminders (one-off and yearly) and chat watches; briefing prompt; browser and YouTube; new-message polling; proactive nudges |
| Voice | `voice/stt.py`, `voice/tts.py`, `voice/session.py`, `voice/ack.py`, `voice/wake.py`, `voice/greeting.py` | Recording and transcription; voices and the speech queue; spoken turns; quick acknowledgments; wake word; "At your service, Sir." |
| UI | `ui/app.py`, `ui/controller.py`, `ui/panel.py`, `ui/pill.py`, `ui/plexus.py`, `ui/timer_widget.py`, `ui/dialogs.py`, `ui/wake_dialog.py`, `ui/nudge_toast.py`, `ui/theme.py`, `ui/hotkey.py`, `ui/winutil.py` | Everything visible |
| Settings | `config.py`, `settings.json` (yours, not in git), `.env` (keys, not in git), `mcp_servers.json` | Defaults, your overrides, secrets, which MCP servers to start |
| Quality | `tests/`, `evals/` | 151 offline tests; evaluations against real data (`evals/RESULTS.md`) |

## 3. Startup

`Controller._startup` runs on the backend thread:

1. **WhatsApp server:** if nothing listens on 127.0.0.1:8765, start `whatsapp-mcp serve` (log in `data/whatsapp-server.log`).
2. **MCP servers:** connect to every server in `mcp_servers.json` (`./` paths are relative to that file). One failed server doesn't stop the others; the footer shows which are up. Calendar counts as connected only when its OAuth token exists.
3. **Memory, directory and agent:** open the style memory (`data/memory.db`) and the WhatsApp chat directory, make `Facts` (same database), `VoiceNotes` (only when WhatsApp is connected) and the `NudgeEngine`, build the `Agent` (with the `Shortcuts` store), and discover tools (section 5). Then, once ever per contact, family-looking contacts are offered as shortcut cards (`_suggest_shortcuts`).
4. **Quick chats:** the quick-chat chips (the people you message most), a check that WhatsApp is paired, and the voice engine if replies are read aloud.

The UI thread meanwhile shows the pill, registers the hotkey, starts the wake-word listener if it's on, and starts three timers:
- the inbox poll, every 60 s;
- proactive nudges, every 10 minutes (first two minutes after start);
- reminders and the morning briefing, every 15 s;
- the timer board, every 33 ms (only while timers exist).

## 4. One request, end to end

**Typed:** Panel input → `Panel._submit` → `ask(text, spoken=False)` → `Controller._ask` → `Agent.run(text)`.

**Spoken:**
1. **The request is captured:**
   - **Hotkey or mic:** `Recorder` (sounddevice) records until you release.
   - **Wake word:** `WakeListener` hears "Ken" → `Greeter` says "At your service, Sir." → it records until a 0.9 s pause (Silero VAD).
2. **It's transcribed:** `OpenAITranscriber` (or local Whisper) turns the audio into text, and "Ken," at the start is removed. Stop words like "stop" or "خلاص" end here with nothing sent.
3. **It's sent:** if the panel is open, the text shows in the input for 1.8 s so you can correct it, then sends. If the panel is closed, it sends at once and the panel stays closed.
4. **Ken thinks and answers:** `VoiceSession.handle` runs `Agent.run(text, voice=True)`. Voice mode asks for one short sentence. `spoken_reply` then picks the line to say (never a draft's text), and the `SpeechQueue` speaks it.

**Inside `Agent.run`:**
1. **The system prompt** is built from `SYSTEM_PROMPT` + tone + personality + voice rules, plus two lines at the end:
   - **the current date and time;**
   - **which language you wrote in** (`language_hint`), so English questions get English answers.

   These two change every turn, so they go last and the start of the prompt stays cacheable. After them come up to 3–5 **saved facts** related to the request (`Agent._saved_notes`: closest by meaning, plus facts about a person the request names). With no facts saved this step makes no request at all. Last comes a **Shortcuts** line for any shortcut word in the message (`Agent._shortcut_note`): "mom" = ماما (WhatsApp +20…, chat JID …). It is looked up in code, costs nothing, and adds that chat or address to the recipient guard.
2. **The loop** (at most `max_steps` = 6): call the model with the history and the tool list.
   - If it answers with text, the turn is done.
   - If it calls tools, each call goes to `_handle` (section 5) and the result is appended as a tool message.
3. **Old tool output** from earlier turns is shortened (`_compact_old_tool_output`) so long sessions stay cheap.
4. **The result:** a `TurnResult`: text, drafts, events, links, person cards, shortcut cards, sends, and token usage. The UI turns it into a reply bubble and cards.

**Back on the UI thread:**
- the reply arrives as particles from Ken's avatar and types itself out;
- cards slide in;
- the pill flashes "done".

If the panel is closed and a card waits for a click, the pill glows instead of opening the panel.

## 5. Tools: what the model can call

`Agent.start` lists every MCP tool and sorts it with `tools.classify`:
- **hidden:** names containing delete, archive, label, draft, create, modify, file, media and similar (`_DENY`);
- **send:** names containing send or reply;
- **read:** list, get, read, search or find.

Hidden tools are never shown to the model. Read and send tools are shown in a trimmed form (`compact_spec`, short descriptions and no noisy parameters), because every model call carries every tool definition.

Ken's own tools are added next to them:

| Tool | What happens |
|---|---|
| `find_chat` | Fuzzy search over WhatsApp chats (Arabic normalisation, Latin↔Arabic transliteration, Franco-Arabic digits). Results are ranked, the best is marked, and each shows "last message today / Dec 2025". |
| `my_past_messages` | Style memory recall for a chat (section 6). Required before a WhatsApp draft: `show_draft` refuses until it ran. |
| `show_draft` | Puts a draft card in the panel (WhatsApp text tidied: no final full stop, Arabic ؟). Never sends. |
| `propose_event` | An event card. Only the owner's **Add** click calls `calendar__create_event`, through `Agent.add_event`, which audit-logs it. |
| `start_timer`, `control_timer` | Pomodoro, stopwatch and countdown in the shared `TimerManager`. |
| `set_reminder`, `watch_chat`, `reminders` | Reminders and "tell me when X replies", stored in `data/reminders.json`. |
| `about_ken` | Questions about Ken himself: searches this file and the project brief (section 6) and answers from them. |
| `open_website`, `web_search`, `play_youtube` | The browser, with the link rule (section 7). `play_youtube` asks the YouTube Data API for the top match. |
| `remember_fact`, `forget_fact` | Saved facts (`facts.py`), kept in `memory.db` under the kind `facts`. Work only when the owner's latest message says remember / forget (section 7). A `yearly_date` (MM-DD) also adds a yearly reminder at `birthday_time`; forgetting the fact cancels it. |
| `person_summary` | `people.build_profile` gathers, with no AI: the best-matching chat, `ChatDirectory.stats` (messages each way in 30 days, last message and who sent it, unanswered messages, median reply time; top speakers and open questions for groups), the last 7 days of chat, saved facts about them, and calendar events with their name in the next 14 days. The model writes the summary from it; the app shows a person card. No email. |
| `propose_shortcut` | "mom is ماما", "boss is sara@x.com": `shortcuts.resolve` turns *who* into exactly one chat (no close second match) or an email address and shows a shortcut card. Saved only on the card's **Save** click. `find_chat`, `person_summary` and `voice_notes` check shortcuts before fuzzy matching. |
| `voice_notes` | Transcribes the newest voice notes of a chat (max 5 notes, 5 minutes of audio per request), only when the owner's message asks about a voice note. See section 9. |

**Read tools** (`_read`): the result is cut to size (`trim_tool_output`: at most 10 items and 2,000 characters each), wrapped as `<<<UNTRUSTED_DATA source=…>>>`, and the recipients in it are remembered by the `RecipientGuard`.

## 6. Writing in the owner's style (RAG)

**Data:** `data/memory.db`, a SQLite table of items with embeddings (OpenAI `text-embedding-3-small`, or a local hash embedder offline). It holds two kinds of item:
- `whatsapp_sent`: messages sent through Ken. The embedding is made from your text.
- `whatsapp_pair`: their message → your reply. The embedding is made from **their** message, so a new incoming message finds how you answered similar ones.

**Filling it:**
- Every confirmed WhatsApp send adds both items: what you answered is read **before** sending.
- **Settings → Memory → Learn from my WhatsApp history** imports up to 150 reply pairs from each of your 15 most-messaged chats (`learn_history`), in batches, skipping duplicates.

**Recall** (`Agent._recall`) for a WhatsApp draft:
1. **What's being answered:** from the chat directory (WhatsApp SQLite) it reads their latest messages, the last 24 h of conversation with timestamps, your usual reply length and how often you split replies into two lines.
2. **Similar past exchanges:** both lookups (their message and the topic) are embedded in **one** request (`VectorStore.prime`), and each search reuses the cached vectors. The 3 most similar exchanges in this chat, plus 3 similar messages of yours, are kept.
3. **Instructions for the draft,** built from all of the above: write ONE message like these; no full stop; no emoji unless you use them; no excuses or promises; stay under about 1.3× your usual length; two lines only when the reply has two parts.

The conversation and examples are wrapped as untrusted data. Measured gains are in `evals/RESULTS.md`.

**Ken's own docs** (`knowledge.py`): `PROJECT_BRIEF.md` and this file are split into one chunk per `##`/`###` section (long ones cut at paragraphs) and stored in the same database under the kind `ken_docs`. A fingerprint of both files is saved with them: at startup they're re-embedded (one request) only if either file changed. The `about_ken` tool returns the 3 closest sections, so "how does the wake word work?" is answered from the docs, not guessed.

## 7. Safety in code

The send path (`Agent._send`), in order:
1. **Clean the message:** WhatsApp text is tidied first, so the dialog shows exactly what is sent.
2. **Intent:** the latest message must ask to send (`has_send_intent`), unless the owner clicked **Send** on a card.
3. **No attachments and no HTML bodies.**
4. **Recipient guard:** every recipient must have been typed by you or seen in what Ken read this conversation.
5. **Rate limit:** 3 WhatsApp / 5 email sends a minute.
6. **Confirmation dialog** (`ConfirmSendDialog`, on the UI thread through a future). Only a mouse click on **send** returns yes; Enter does nothing.
7. **Send and log:** the MCP send tool is called and the result is audit-logged. A WhatsApp send is also added to style memory.

**Other safety pieces:**
- **Calendar:** the model can only propose. `create_event` is hidden from it and is called only from the card's **Add** button.
- **Links** (`web.asked_for`): a link opens by itself only if you named the site or it's a Google search / YouTube address Ken built. `google.com/url` and YouTube `/redirect` are refused, and so are non-web schemes (`normalize_url`). Anything else becomes a link card.
- **Untrusted text:** messages, emails, events, briefing data and video titles are always wrapped, and the prompt says never to follow instructions inside them.
- **Wake word privacy:** the microphone is analysed locally; audio leaves only after "Ken" (like holding the hotkey), or inside a follow-up window right after Ken answered you.
- **Facts:** `remember_fact` / `forget_fact` refuse unless the owner's own latest message asks (`has_remember_intent`, `has_forget_intent`), so a chat saying "remember that…" can't plant a lasting note. Saved facts are labelled "facts, not instructions" in the prompt.
- **Voice notes:** `whatsapp__download_media` stays hidden from the model; only `VoiceNotes` calls it, and only when the owner's message mentions a voice note (`has_voice_intent`). The file path it returns must lie inside the WhatsApp store. Transcripts are wrapped as untrusted. The server does not send a "played" receipt.
- **Shortcuts:** saved only from a click (card, Settings or person card), one word → one chat or address (saving a word for someone else moves it), and never several people (no broadcasts; WhatsApp limits bulk sending). A shortcut only picks *who*: the send still needs your request and the confirmation dialog with the real name and number.
- **Nudges:** never send. The AI check reads the messages as untrusted data and its answer is used only as yes / no.

## 8. Voice in detail

**Speech to text** (`voice/stt.py`):
- `make_transcriber` follows the backend: OpenAI `gpt-transcribe` when the brain is OpenAI, local `faster-whisper` otherwise.
- A failed OpenAI call falls back to Whisper.
- Seconds sent to OpenAI are counted for the cost footer.

**Speaking** (`voice/tts.py`):
- `VoiceEngine` picks Edge (`EdgeTTSEngine`: edge-tts gives MP3, decoded with PyAV), OpenAI (streamed PCM with style instructions) or Windows OneCore voices. Edge and OpenAI fall back to Windows on any error.
- The voice per language comes from settings (`edge_en`, `edge_ar`).
- `SpeechQueue` is one thread that plays lines in order. A new request, the hotkey, or "Ken" cuts it off (generation counter).

**Wake word** (`voice/wake.py`):
- **Features:** openWakeWord's melspectrogram and embedding models turn each 80 ms chunk into a 96-number frame. A window is the last 16 frames.
- **Training** (`train`, run by the training dialog in a worker thread):
  - **Positives:** your 25 takes, word-trimmed, replayed into room noise at random gains.
  - **Negatives:** your read-aloud text; lone short sounds (syllables cut from your talk, synthetic sniffs, breaths, clicks, knocks and hums); Windows voices saying look-alike words ("can", "then", "لكن", "كنت", "hey"…).
  - Everything is **streamed through the same features the live listener uses**.
  - A logistic regression learns the difference. The threshold sits between held-out talk and held-out takes.
  - Saved to `data/wake/ken.npz`; recordings to `data/wake/samples.npz`, so Ken can be retrained without recording again.
- **Listening** (`WakeListener`):
  - **The mic:** one stream at 16 kHz, read in chunks by a worker thread.
  - **When it fires:** 3 frames in a row above the threshold **and** Silero VAD saying it was speech, with a 2 s cooldown.
  - **When it stays quiet:** while Ken is busy or recording.
  - **"Ken, stop":** while Ken is talking, his name cuts him off with no greeting.
- **Greeting** (`voice/greeting.py`): "At your service, Sir." is synthesized once per voice and personality and cached in `data/voice/`. It plays before recording starts, and its echo is dropped.

**Follow-up window** (`WakeListener.follow_up`, `Controller._follow_up_after_speech`):
- After Ken speaks an answer to a **spoken** request, the controller waits (in a background task) until the speech queue is quiet, then asks the wake listener to listen for `follow_up_seconds` (default 8).
- The listener drops the end of Ken's own voice, records like after "Ken" but without the greeting, and keeps the audio only if there was at least 0.4 s of real speech (`_capture(min_speech=0.4)`), so coughs cost nothing.
- "Thanks", "that's all" or "شكرا" (`CLOSE_WORDS`) or nothing understood ends it with no AI call. Anything else is sent as the next request in the same conversation, which can open another window.
- The request expires if it can't start within 5 s. The pill shows "still listening_" with a bar that shrinks as the seconds run out. Needs "Listen for Ken" on.

**Acknowledgment** (`voice/ack.py`): the optional spoken "Right away, Sir" while the answer is made. Off by default; canned butler phrases or a small model call.

## 9. Features in brief

- **Briefing** (`briefing.py`, `Controller._brief`):
  - Gathers WhatsApp chats with new messages in the last 24 h (each chat's conversation), today's emails without promotions, and today's and tomorrow's events, all in code.
  - One model call returns a speakable first line, sections, and "Needs your reply".
  - The morning version runs once a day after the set time (before noon) and speaks the first line.
- **Inbox** (`inbox.py`): no AI.
  - WhatsApp: read straight from the SQLite store.
  - Gmail: searched for unread mail every third poll.
  - New items light the red dot. The popup lists them, and **reply** drafts at once.
- **Timers** (`timers.py` + `ui/timer_widget.py`): the model and the menu both use the shared `TimerManager`.
  - Phases are computed from elapsed time, with pause support.
  - `TimerBoard` polls it every 33 ms, keeps one card per timer, chimes and notifies on phase changes.
- **Reminders** (`reminders.py`):
  - Due reminders fire from the 15 s timer: chime, Windows notification, panel notice and a spoken line.
  - Chat watches are checked in the 60 s poll (`incoming_since` on the WhatsApp store) and expire after their hours.
- **Saved facts and birthdays** (`facts.py`): one sentence each, tied to a chat when the name matches clearly (score ≥ 0.85, no close second). Yearly dates become `Reminder(repeat="yearly:MM-DD")`: `due()` fires them and moves them on a year (29 Feb falls on 28 Feb). The briefing lists today's yearly facts after its opening line. Settings → Memory lists them with a delete button.
- **Voice notes** (`voicenotes.py`): find the newest `media_type='audio'` rows of the chat → `download_media` (decrypted locally by the WhatsApp server) → decode Ogg/Opus to 16 kHz with PyAV → the same speech-to-text as the owner's voice (`Controller._stt`, so it's in the cost footer) → cache in `data/voice_notes.db` by message ID. `recent_conversation` then shows cached notes as `[voice 0:42] «…»` everywhere (summaries, drafts, briefing). Nothing is transcribed in the background and old notes are never backfilled.
- **Shortcuts** (`shortcuts.py`, `data/shortcuts.json`): each has spellings (`words`), a channel (WhatsApp chat — person or group — or Gmail address), the target, a label and the number. `matches(text)` normalises like chat names and also strips Arabic prefixes glued to a word ("لماما" = to mama), longest phrase first. `suggestions()` finds contacts saved under family words (Mom/ماما/أمي, Dad/بابا, wife, brother, sister, grandma…), the most-messaged one per word group, offered once at startup; Settings lists them again.
- **Proactive nudges** (`nudges.py`, `Controller._check_nudges`), every 10 minutes:
  1. **Rules, no AI:** for one-to-one chats active in the last 3 days, `stats()` says whether they wrote last. Unanswered for 3 h (your ~15 most-messaged people) or 24 h (others), and not just "ok / 👍 / تمام" → a candidate. Your own question unanswered for 2–6 days in a close chat → "owed". Calendar events starting within 20 minutes → an alert.
  2. **Limits:** quiet hours (`nudge_quiet`), at most `nudge_max_per_day` message nudges, never the same key twice, a chat at most every 6 h, snoozed / muted chats skipped, nothing during a Pomodoro focus phase or a meeting (event alerts still come). State in `data/nudges.json`.
  3. **The AI check:** each new candidate gets one tiny request ("does this need a reply? yes/no", tagged `nudge`); the answer is cached by key.
  4. **One at a time:** at most one message nudge per check, shown as `NudgeToast` above the pill (draft reply / snooze 2 h / mute chat). ★ people (`important_people`) are also spoken aloud.
- **Calendar server** (`calendar_mcp.py`): an MCP server built with `mcp.server.mcpserver.MCPServer`.
  - Tools: `list_events` (all selected calendars, declined events dropped, "[you haven't accepted]" marked), `find_free_time` (free/busy gaps) and `create_event` (primary calendar, `sendUpdates="none"`).
  - `--auth` runs Google's consent once, started from Settings.

## 10. The look (UI)

- **Theme** (`ui/theme.py`):
  - Frost console colours, fonts (Segoe UI Variable, Cascadia Mono for small labels, Segoe Fluent Icons) and one app-wide stylesheet.
  - `paint_paper` draws the dot-grid background; `paint_glyph` draws Ken's mark.
- **Plexus** (`ui/plexus.py`): one engine for every avatar.
  - Particles have spring physics (numpy) toward a target shape per state: sphere, ear, gyroscope, ribbon, check mark, sleep.
  - Links go to the 2–3 nearest neighbours, computed with numpy. Drawing is batched (one `drawLines` and one path fill per brightness level), so a frame costs a few milliseconds.
- **Pill** (`ui/pill.py`): the window stays one size, and the capsule inside it animates between sizes.
  - It runs at 25 fps when active and about 12 when resting.
  - It sleeps after 10 idle minutes, glows when a card waits, and shows a red dot for new messages.
- **Panel** (`ui/panel.py`):
  - **Fixed parts:** header (live avatar, status, icon buttons), scrolling feed, status line with a small gyroscope, quick-chat chips, input with a `›` prompt, and a footer with connections and cost.
  - **Fast drawing:** the background (shadow and paper) is drawn **once** into a pixmap. Every repaint copies it, because each animated child repaints what's behind it.
  - **Animations:** the flying particles repaint only the area they cross; feed rows fade in with an opacity effect that is removed afterwards; replies type out in 40 ms steps.
- **Person card** (`PersonCard` in `ui/panel.py`): initials, the numbers from `stats()`, what's waiting, and reply / watch / remind me / ★. Only **reply** uses the model.
- **Shortcut card** (`ShortcutCard`): the words as separate chips (`word_chips`, so mixed Arabic and English keep their order), → the chat, and **save**. Settings → Shortcuts lists them (× removes), has an Add row (words + chat name or email) and the suggestions. The person card has **＋ shortcut**.
- **Nudge pop-up** (`ui/nudge_toast.py`): a frameless card above the pill that never takes focus and hides itself after 45 s (not while the mouse is on it).
- **Dialogs** (`ui/dialogs.py`):
  - Frameless paper dialogs for send confirmation and the inbox.
  - Settings is a normal window: a sidebar, one page of tiles per section, chips (`Chips`, with the same `currentData()` interface as a combo box), search and a privacy line.

## 11. Data on disk (all ignored by git)

| Path | Contents |
|---|---|
| `.env` | `OPENAI_API_KEY`, optional `YOUTUBE_API_KEY` |
| `settings.json` | Your settings (merged over `config.py` defaults) |
| `data/memory.db` | Style memory (sent messages and reply pairs), saved facts, and Ken's docs, with embeddings |
| `data/voice_notes.db` | Voice-note transcripts made on request |
| `data/shortcuts.json` | Your shortcuts, and which contacts were already suggested |
| `data/nudges.json` | Which nudges were shown, AI-check answers, snoozed and muted chats |
| `data/audit.jsonl` | Every send and calendar add |
| `data/openai_requests.jsonl` | Every OpenAI request: time, tag (app/eval), purpose, endpoint, model, status |
| `data/reminders.json` | Reminders (one-off and yearly) and chat watches |
| `data/inbox_state.json` | What the inbox already showed |
| `data/wake/` | Your wake-word model and recordings |
| `data/voice/` | Cached greetings and voice previews |
| `data/app.log`, `data/whatsapp-server.log` | Logs |
| `data/eval/` | Evaluation results |
| `whatsapp-mcp/store/` | WhatsApp session and message SQLite |
| `~/.gmail-mcp/`, `~/.calendar-mcp/` | Google OAuth client and tokens |

## 12. Cost and request accounting

- **One client factory:** every OpenAI client is created by `openai_client.async_client` / `sync_client`. They attach an HTTP response hook that writes one line per request, retries included, to `data/openai_requests.jsonl`.
- **The panel footer** shows tokens, voice minutes, the session's estimated cost (`usage_line`, using `prices` and `audio_prices` from `config.py`) and today's request count from the log.
- **Evals** tag their requests `eval`.

## 13. Tests and evaluations

- **`tests/`:** 151 tests, all offline.
  - **The guard:** `conftest.py` gives every OpenAI client a transport that raises on any real request, uses a temporary request log, and fakes the browser and the YouTube API. A test can never spend money.
  - **Fakes:** `FakeBackend` replays scripted model replies. Qt tests run headless (`QT_QPA_PLATFORM=offscreen`).
- **`evals/run_eval.py`:** measurements against real data. These **cost money**, so ask before running.
  - `style`, `intent`, `before_after`: drafts vs your real replies;
  - `models <name>`: compare models;
  - `grounded`: facts, prompt injection, unrequested sends;
  - `finding`: chat finding, including the "يوسف" case.

  Results go to `data/eval/`, and the summary to `evals/RESULTS.md`.

## 14. Extending Ken

- **A new MCP server:** add it to `mcp_servers.json`. Its tools are discovered at startup and sorted by the allowlist; check that anything that writes or deletes is hidden (`tools._DENY`, or `hidden_tools` in settings).
- **A new local tool:**
  1. Define a `ToolSpec` in `agent.py`.
  2. Add it in `Agent.start`.
  3. Handle it in `Agent._handle`.
  4. Add a line to `SYSTEM_PROMPT`.
  5. If it acts on the world, make it propose a card and act only on a click (like `propose_event`).
- **A new avatar state:** add a target shape in `Plexus._targets` and a colour in `COLORS`, then map a pill state to it.
