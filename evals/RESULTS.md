# Evaluation results: drafting before vs after reply pairs + history

*Measured 28 October 2026 with `evals/run_eval.py` and gpt-4.1-mini, on the author's own WhatsApp history. Raw results are in `data/eval/` (private, never published); only the numbers are shown here. Run it on your own chats to get yours.*

## What was compared

The same **50 real WhatsApp replies** were used for every version: the newest 5 replies from each of your 10 most-messaged chats. Those replies were removed from the memory first, so no version could copy them. Each version was told **what you wanted to say** (a short reworded summary of your real reply), and its draft was compared with **what you actually sent**.

| Version | What it uses |
|---|---|
| **No RAG** | Instructions and tone only |
| **Before** | Style examples from the ~23 messages sent through the app, searched by topic. No reply pairs, no history, no 24 h context, no length target |
| **Now** | 985 "them → you" reply pairs from your history (same chat first), the chat's last 24 h with timestamps, a length target from your own messages, and the no-padding rule |

## Before → now (same 50 replies, same run, latest version)

| Aspect | Your real replies | No RAG | Before | **Now** | Change, before → now |
|---|---|---|---|---|---|
| Blind judge: "closest to the real reply" | — | 24% | 14% | **32%** | **+18 points** (best of the three) |
| Blind judge: none of the three close | | | | | 30% of cases (was 48% in the first run) |
| Length vs yours (how much longer) | 36 chars | +45% | +18% | **+8%** | Overshoot cut by **55%** |
| Commas per message | 0.14 | 0.44 (+214%) | 0.20 (+43%) | **0.06 (−57%)** | Now *fewer* than you use |
| Messages with emoji | 0% | 8% | 2% | 4% | +2 points (noise) |
| Replies split over 2+ lines | 22% | 20% | 0% | **52%** | From never splitting to splitting too often (see below) |
| Meaning similarity to your reply | — | 47.1% | 47.4% | 47.1% | No change (content comes from your intent) |

### Line-break fix: how it was calibrated

The first "now" version never split a reply over several lines (0%). You split about 22% of replies in the test set, and 24–45% of your replies in your top chats, usually as several short messages sent within a few minutes. Getting there took four tries:

| Try | Instruction to the model | Split | Length vs yours | Judge best |
|---|---|---|---|---|
| 1 | Length target per **message** (~13 chars, one piece) | 0% | +11% | 18% |
| 2 | Length per **line** + "put each thought on its own line" | 78% | +40% | 18% |
| 3 | Length for the **whole reply** + "only when clearly two parts" | 4% | +8% | 24% |
| 4 (kept) | Whole-reply length + "two parts (answer + question, reaction + answer) → two lines, otherwise one" | 52% | **+8%** | **32%** |

A prompt alone can't hit an exact rate; the wording flips the model between extremes. Try 4 is kept because it scores best overall. The way to hit ~22% exactly would be to decide the split in code instead of in the prompt.

## How the "now" version got there (intent-given runs, RAG + 24 h column)

| Step | Length vs yours | Commas | Emoji | Meaning similarity |
|---|---|---|---|---|
| Reply pairs + history, no 24 h context | +45% (52 chars) | 0.20 | 2% | 48.1% |
| + 24 h conversation context | +54% (56 chars) | 0.12 | 0% | 49.0% |
| + length target and no-padding rule | **−10% (33 chars)** | **0.10** | **0%** | **49.9%** |
| Same setup, second run | +11% (40 chars) | 0.20 | 6% | 46.7% |
| + per-chat line-split habit and whole-reply length (current) | +8% (39 chars) | 0.06 | 4% | 47.1% |

## gpt-4.1-mini vs gpt-6-luna (same 50 replies, "now" setup)

gpt-4.1-mini shuts down on 14 Jan 2027 and OpenAI names gpt-6-luna as its replacement. Both models got the same prompt, the same recall (pairs + 24 h + length target) and the same intent. Only the model writing the draft changed. Luna ran with reasoning off (`none`). **Each model judged blind**, because a judge tends to prefer its own writing: where the two judges agree, the result is solid.

| Aspect | Your real replies | gpt-4.1-mini | **gpt-6-luna** |
|---|---|---|---|
| "Closest to the real reply", judged by 4.1-mini | — | 22% | **60%** (none: 18%) |
| "Closest to the real reply", judged by Luna | — | 18% | **68%** (none: 12%) |
| "Reads more like a native texter", judged by 4.1-mini | — | **56%** | 32% |
| "Reads more like a native texter", judged by Luna | — | 22% | **62%** |
| Meaning similarity to your reply | — | 51.6% | **58.5%** (+7 points) |
| Length | 37 chars | 39 (+5%) | 30 (−18%) |
| Commas per message | 0.14 | 0.02 | 0.04 |
| Split over 2+ lines | 28% | 56% | **44%** (closer) |
| Messages with emoji | 0% | **0%** | 10% |
| Median seconds per call | | **1.0 s** | 1.6 s |
| Cost of this run (drafts + judging) | | $0.048 | **$0.011** (−76%) |

- **Closeness to your real replies:** Luna wins clearly. Both judges, including gpt-4.1-mini judging against itself, pick Luna 60–68% of the time. It reuses your actual words more often and invents fewer extra sentences.
- **Native-sounding Arabic:** a tie. Each judge prefers its own model, so no winner can be called.
- **Where Luna is worse:**
  - It adds emoji (10%, you 0%).
  - It runs a bit short.
  - It sometimes uses a Latin "?" instead of "؟".
  - It slips Gulf words into some chats ("مافي", "وين").
  - Once it wrote a bare `https://meet.google.com` link, where your real reply had a full meeting link.
  - It's ~0.5 s slower per call.

## Knowing your intent vs guessing it (blind runs)

| | Draft guesses your decision | Draft is told your decision |
|---|---|---|
| Meaning similarity to your reply | 31–36% | 46–50% |
| Judge: none of the drafts close | **70%** | **42–48%** |

Most of the gap between a draft and your real reply is **your decision** (yes/no, can't come, send a link). That isn't in the chat, so no RAG can recover it. RAG and history improve **how** a draft sounds, not **what** you decide.

## Other aspects (not affected by reply pairs or history)

| Aspect | Result | Data |
|---|---|---|
| Factual claims in summaries backed by what it read | **96%** (26 of 27) | 10 questions on fake data |
| Answers containing an unsupported claim | 10% (1 of 10). It was advice ("ignore this email"), not an invented fact | same |
| Prompt-injection email obeyed | **0%** | same |
| Sends attempted without being asked | **0%** | same |
| Found the right chat from a misspelled name | **90%** (9 of 10). The miss was ambiguous: several contacts are saved under the same name | 10 real chats |
| Made a draft card instead of plain text | **100%** | same |
| Looked up your past messages before drafting | **100%** | same |

## How to read these numbers

- **The style wins are real and consistent:** shorter drafts, fewer commas, no excuses or promises. They point the same way in every run.
- **The latest version is the first one the judge clearly prefers:** "now" 32% vs "before" 14%. With 50 cases a difference under about ±10 points is noise, so this +18 is a moderate, not certain, win.
- **Run-to-run noise is large.** The unchanged "before" version measured 42–50 characters across four runs. Treat single-run differences of a few points as noise.
- **Line breaks are fixed but overshoot:** from never splitting (0%) to 52%, against your 22%.
- **Meaning similarity doesn't move (~47%) for any version.** What a reply says comes from your intent, not from RAG.

## Cost

| Run | Model calls | Tokens | Cost |
|---|---|---|---|
| First full evaluation (style, groundedness, chat finding) | 236 | 189k | $0.078 |
| Blind 3-way style | 200 | 215k | $0.091 |
| Intent-given, before the length fix | 250 | 224k | $0.095 |
| Intent-given, after the length fix | 250 | 231k | $0.097 |
| Before vs now, first run | 250 | 125k | $0.055 |
| Line-break calibration, 3 runs | 750 | 379k | $0.163 |
| gpt-4.1-mini vs gpt-6-luna | 251 | 220k | $0.059 |
| **Total** | **2,187** | **1.58M** | **≈ $0.64** |
