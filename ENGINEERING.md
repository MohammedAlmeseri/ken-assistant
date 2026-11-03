# Engineering notes: how Ken was built

This is the document for engineers: the design decisions, the problems I ran into, how I fixed them and **how well**, and what happens when each part fails. For the code map, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). For what Ken does, see [docs/PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md).

**How to read the numbers.** Every percentage below is labelled:
- **measured:** from a test, a profiler run or the evaluation harness ([evals/RESULTS.md](evals/RESULTS.md)), with the raw counts;
- **by construction:** guaranteed by code, and covered by a test;
- **not measured:** fixed and tested, but no before/after number exists. I don't make one up.

---

## 1. Design principles

1. **Safety lives in code, not in the prompt.**
   - The model is treated as fallible and as a possible target of prompt injection: anything it reads from a message could be an attack.
   - So every action that touches the outside world passes through checks it can't talk its way past: intent, recipient, rate limit, and a human click.
2. **Propose, then click.**
   - The model never *does* anything irreversible. It shows a draft card, an event card, a link card or a shortcut card.
   - Only the owner's click acts, through a code path the model can't reach. For example, `calendar__create_event` is hidden from the model and only the card's **Add** button calls it.
3. **Retrieve with code first, and use AI only where it helps.**
   - Finding a chat, counting unanswered messages, detecting nudge candidates, resolving shortcuts and building the briefing data are all plain SQL and Python over the local WhatsApp cache. They are fast, free and predictable.
   - The model is used for judgment and language.
4. **Measure everything that costs money or quality.**
   - Every OpenAI request is logged with a purpose tag.
   - Quality claims go through an evaluation harness on real data, with a blind judge.
5. **Everything has a fallback.** Section 4 lists each one.

## 2. Architecture in one minute

| Thread | Runs | Talks to others via |
|---|---|---|
| **UI thread** (Qt) | pill, panel, cards, dialogs, timers, the tray | calls `Controller` methods |
| **Backend thread** (asyncio) | the agent, MCP clients, RAG, nudges, speech-to-text calls | Qt **signals**, which Qt queues onto the UI thread automatically |
| **Wake-word thread** | microphone stream, openWakeWord features, VAD, recording | callbacks → controller → signals |
| **Speech thread** | the speech queue (one line at a time, stoppable) | a generation counter for barge-in |

Why not one Qt-integrated event loop? The Gmail and Calendar MCP servers are **stdio subprocesses**, and subprocess support in Qt/asyncio bridges on Windows was unreliable. A dedicated asyncio thread with `run_coroutine_threadsafe` is boring and solid.

**One request:**
1. **The prompt is built:** rules + tone + persona + (voice rules) + current time + language hint + related facts + the shortcut note.
2. **The tool loop runs** (at most 6 steps): the model calls tools, and `_handle` routes each call to a local tool or an MCP read/send tool. Anything else is refused.
3. **Results are cleaned:** they're trimmed, wrapped as untrusted, and their addresses are recorded for the recipient guard.
4. **A `TurnResult` is returned**, and the UI turns it into bubbles and cards.

## 3. The RAG system

### Storage

- **One table** in `data/memory.db`, with columns `kind, text, meta (JSON), created, embedder, vec (float32 blob)`.
- **Why SQLite and numpy instead of a vector DB:**
  - The scale is ~1–10k items.
  - A brute-force `matrix @ query` is exact and takes milliseconds.
  - It's one file to back up, and it has no service to run.
- **Why each row records its embedder:**
  - OpenAI (1536-dim) and the local hash embedder (1024-dim) can coexist after an outage.
  - Search compares like with like and skips vectors it can't compare. It never mixes spaces.

### Four uses, and why each looks the way it does

| Use | Retrieval | Why this design |
|---|---|---|
| **Style memory** (`whatsapp_pair`, `whatsapp_sent`) | filter by `kind` and **chat**, then top-3 by cosine | A pair is embedded on **their** message, so a new message finds similar *situations* and the stored reply shows how you handled them. The per-chat filter matters: a pair's 3 nearest neighbours come from the same chat only 40% of the time (12% by chance, *measured* on 1,001 pairs). Without the filter, your style with one person leaks into another chat. |
| **Docs** (`ken_docs`) | top-3 sections | One chunk per heading, titled with its path ("ARCHITECTURE.md › 8. Voice in detail"), so a hit carries its context. A SHA-1 of the docs means re-embedding only happens when they change. |
| **Facts** (`facts`) | top-3 above a similarity floor, **plus** facts naming a person in the request | Meaning alone misses "what should I get **Youssef**?" ↔ "Youssef loves football". The exact-name pass catches it. With no facts saved, no embedding request is made. |
| **Shortcuts** | **exact** word match in code (not vectors) | "Send to my mom" must never be a similarity decision. |

**Cost control.** A draft needs two lookups (their message and the topic). `VectorStore.prime()` embeds both in **one** request and caches the vectors: 2 requests → 1 per draft (*by construction*, tested in `tests/test_requests.py`).

### Prompt assembly for a draft (`Agent._recall`)

1. **The chat's last 24 h** with timestamps, wrapped as untrusted. This is *what* to answer.
2. **How you answered the 3 most similar messages in this chat.** This is *how* to answer.
3. **Up to 3 of your similar messages**, without duplicates.
4. **Targets:**
   - your median whole-reply length, as "stay under 1.3×";
   - how often you split a reply into two lines;
   - no full stop, no emoji unless you use them, no padding.
5. **Code-side tidying** that a prompt can't guarantee: no final full stop, and the Arabic "؟".

## 4. Fallbacks and failure handling

| Component | Primary | Falls back to | When / how it's detected | What you see |
|---|---|---|---|---|
| Brain (LLM) | OpenAI `gpt-6-luna` | the SDK retries transient errors (each retry is logged); local Ollama is a **manual** switch | an exception from the API | an error line in the panel. No automatic switch: a 30-second local answer would be a surprise. |
| A turn that loops | — | stops after `max_steps` (6) | step counter | "I stopped because this took too many steps." |
| Stop button | — | the turn is cancelled **and removed from history** | `CancelledError` | the next request starts clean. A half-finished tool call would make the next API call fail. |
| Embeddings | OpenAI `text-embedding-3-small` | local hash embedder (words + character 3-grams) | any exception in `_embed` | nothing. The row records which embedder made it. |
| Batched query embedding | `prime()`, one request | per-query embedding inside `search()` | exception in `prime` | nothing |
| Speech to text | OpenAI `gpt-transcribe` | local faster-whisper | any API error | a log line; the request still works (slower) |
| Voices | Edge neural voices / OpenAI | Windows OneCore, then SAPI | any error while speaking a sentence | the same sentence, in a Windows voice |
| MCP servers | all three | each one is optional; connecting is retried once | connect error | a red dot in the footer and a notice. Everything else keeps working. |
| WhatsApp server | already running | Ken starts it and waits up to 15 s | port check | "The WhatsApp server didn't start. See data/whatsapp-server.log." |
| WhatsApp pairing | paired | — | `get_status` (an app-only tool) | "Open http://127.0.0.1:8765/pair and scan the QR code." |
| Calendar | connected | not connected | missing token file | "Google Calendar isn't connected yet: Settings → Connect." |
| Tool errors | — | returned to the model **as text** | `Tool error: …` | Ken explains in plain words instead of crashing |
| YouTube | Data API (top video) | the YouTube results page | no key, or an API error | the results page opens |
| Voice notes | download → decode → speech-to-text, cached | the note is skipped with its reason | per-note exception; a path outside the store is refused | "(couldn't get this note: …)" |
| Nudges' AI check | one tiny request | skip this time, retry next check | exception | silence. Better silent than wrong. |
| Fact recall | top-3 facts | no notes | exception | the request proceeds normally |
| State files (`reminders.json`, `nudges.json`, `shortcuts.json`) | parsed | an empty state | JSON error | the app starts fine |
| Wake word | trained model | the training dialog | model file missing | the tray offers "Teach Ken my voice…" |
| Logs | console | `data/app.log` (rotated at ~1 MB) | `pythonw` has no console | warnings don't vanish |
| Tests | — | **any** real OpenAI request raises | transport guard in `conftest.py` | a failing test, never a bill |

## 5. Problems I hit, and how well they were fixed

| # | Problem | Root cause | Fix | Result |
|---|---|---|---|---|
| 1 | **The app felt like ~10 fps** | Every animation frame repainted the whole panel, including the shadowed, dot-grid background | Draw the background **once** into a cached pixmap; repaint only the area that changed; ~12 fps while resting | **measured** with a 10 ms "heartbeat" timer on the UI thread. Empty panel: 185 → 369 of 400 beats on time (**46% → 92%**). Busy chat: 150 → 294 (**38% → 73%**). Background painting 27% → 1–2% of the time. Average lateness 12.5 → 1.8 ms (**−86%**). |
| 2 | **The wake word never fired live** | Trained on features of whole clips; the live listener sees a *stream* of 80 ms chunks, so the features didn't match | Replay the training audio through the **same streaming feature pipeline** the listener uses | **measured:** before, 0 of 3 takes detected when fed as a stream, at every threshold tried. After, on takes it had never seen: **14/15 (93%)** |
| 3 | **False wakes on sniffs, "hey", short words** | Few short non-word sounds among the negatives; nothing checked that the sound was speech | Hard negatives: look-alike words in Windows voices, lone syllables cut from your own talk, synthetic sniffs/breaths/clicks. Plus a **Silero VAD gate**: fire only if speech probability ≥ 0.6 (sniffs score < 0.45, my "Ken" 0.75–0.97) | **measured:** lone-word false wakes **54/67 → 0/67**. Overall: 2 false alarms in 1,834 moments of other sound (**0.11%**). |
| 4 | **Drafts didn't sound like me** | Style examples were "messages I sent", searched by topic: no context, no length target | Reply pairs keyed on *their* message, a per-chat filter, the last 24 h of the chat, a length target from my own replies, a no-padding rule | **measured** (50 held-out real replies, blind judge): picked as closest **14% → 32%**. Length overshoot **+18% → +8%**. Commas 0.20 → 0.06 per message (mine: 0.14). |
| 5 | **Never splitting a reply into two lines** (I do ~22% of the time) | A length target per *message* pushed one short line | Whole-reply length + "two parts → two lines" + my per-chat split rate | **measured**, only partly fixed: 0% → 52%, now overshooting; 44% vs my 28% after the model change. A prompt flips between extremes. The real fix is deciding the split in code; that's on the list. |
| 6 | **The model was being retired** and drafts were still generic | gpt-4.1-mini's shutdown date | Re-ran the same 50-reply evaluation on gpt-6-luna before switching | **measured:** "closest to the real reply" **22% → 60%** (judged by the old model) and **18% → 68%** (judged by the new one). Meaning similarity 51.6% → 58.5%. **−76%** cost per run, +0.6 s per call. |
| 7 | **"يوسف" opened the wrong Youssef** (a one-message contact from a year ago) | The fuzzy match ignored how alive a chat is | Activity bonus in ranking, plus "last message today / 3 days ago" shown to the model with a "best match" mark | **measured:** the right chat **9/10 (90%)**. The one miss was ambiguous: several contacts share one name. |
| 8 | **Prompt injection / unrequested sends** | A model can obey text inside an email | Intent check, recipient guard, untrusted wrapping, a click-only dialog | **measured:** injection obeyed **0%**, sends attempted unasked **0%**, claims backed by what it read **96% (26/27)**. **By construction:** a send can't happen without a click (tests + [example 3](examples/03_safety_gate.py)). |
| 9 | **3.7k API requests in one day** | Mostly evaluation runs (2,187 model calls in all evaluations), plus a spoken "got it" acknowledgment on every voice request (about a third of voice requests) | A request log with purpose tags and a "requests today" counter in the footer; the acknowledgment off by default; one embedding request per draft instead of two | **by construction:** −1 request per spoken question, −50% embedding requests per draft. Normal use measured at ~300 requests over two days. |
| 10 | **Tests could spend money** | Tests built real OpenAI clients | `conftest.py` gives every client a transport that raises on any real request, and the log goes to a temp folder | **by construction:** 0 real requests possible from 151 tests |
| 11 | **English questions answered in Arabic** | The chats the model had just read were Arabic | A per-turn language hint from the user's own letters, placed last in the prompt (so the cached prefix stays) | not measured; regression-tested |
| 12 | **Draft buttons dead after an event card** | The panel disabled every card while busy, then re-enabled them; the event card lacked `set_busy`, the exception stopped the loop, and the drafts after it stayed disabled | A base `Card.set_busy`, and a try/except per card so one broken card can't freeze the others | **by construction**, with a regression test |
| 13 | **The "thinking" animation looked frozen** | Every particle was assigned to a network node, so nothing moved between them | Replaced with a gyroscope of three rings orbiting at different speeds and tilts | not measured (visual) |
| 14 | **Arabic and English words swapping places** in shortcut cards | Qt's bidi algorithm treats `ماما, أمي` as one right-to-left run | Each word as its own chip widget; Unicode isolates for numbers | **by construction:** order no longer depends on text direction |
| 15 | **The person card overflowed the 448 px panel** (found while preparing this repo) | Six buttons in one row | Dismiss moved to a small × in the card header | by construction |
| 16 | **The settings window outgrew the screen** (Save unreachable) | One long column | A sidebar of sections, one page of tiles each, plus search | by construction |

## 6. Testing and evaluation

- **151 offline tests** cover:
  - the allowlist and the send gate (intent, recipient guard, attachments, HTML, rate limit, decline);
  - trimming, untrusted wrapping and the audit log;
  - RAG recall and the one-request embedding;
  - docs indexing only on change;
  - facts, shortcuts, nudges, voice notes, the follow-up window and timers;
  - the speech queue and wake-word helpers;
  - the UI cards (headless Qt).

  A `FakeBackend` replays scripted model replies, so the agent loop is tested without a model.
- **[evals/run_eval.py](evals/run_eval.py)** runs on **real data**, so it costs money. It's tagged `eval` in the request log and kept apart from the tests. It covers:
  - style with and without RAG (50 held-out replies, a blind 3-way judge);
  - model comparison (each model also judges, to cancel self-preference);
  - groundedness and injection on fake data;
  - chat finding.

  All evaluations together: **2,187 calls, ≈ $0.64**.
- **Statistical honesty:** with 50 cases, differences under ~±10 points are noise. The unchanged baseline measured 42–50 characters across four runs.

## 7. Trade-offs and known limits

- **Unofficial WhatsApp client:** there's a ban risk, so Ken never bulk-sends and has no multi-person shortcuts.
- **Windows-first UI.** WinRT voices, the window helpers and the hotkey are Windows-specific. The core (agent, RAG, MCP, safety) is portable.
- **Brute-force vector search** is exact and fast up to ~100k items. Past that, an ANN index (FAISS, sqlite-vec) would replace one function.
- **The decision is the hard part.** Most of the gap between a draft and my real reply is *what I decided*, which no retrieval can recover. Meaning similarity rises from 31–36% to 46–50% when the draft is told my intent. RAG improves *how* a draft sounds, not *what* I decide.
- **Google OAuth in "testing" mode** expires tokens about weekly, and needs a reconnect.

## 8. What I'd do next

1. Decide the line split in code from the per-chat rate, instead of in the prompt (problem 5).
2. Run the safety evaluation again on gpt-6-luna. It was last run on gpt-4.1-mini.
3. Speaker verification, so only my voice can wake Ken.
4. A dark Frost theme; packaging as an installer.
