# 8. First start: the app, voice, wake word and your writing style

## 8.1 Start the app

**Double-click `Start Assistant.bat`** in `<ken>`. Or, from PowerShell:

```powershell
.\.venv\Scripts\pythonw -m assistant.ui
```

Then:

- **The pill:** a small round **pill** appears at the bottom of the screen. Drag it anywhere.
- **The tray icon:** Ken also has an icon in the tray (bottom right, near the clock). Its menu has Settings, Timers, "Listen for Ken" and Quit.
- **Connections:** open the panel. The footer shows **● wa ● gmail ● cal**: green means connected, red means that server failed (see [Troubleshooting](09-troubleshooting.md)).
- **Logs:** if something goes wrong silently, read `<ken>\data\app.log`.

## 8.2 Talk to Ken

| Do this | What happens |
|---|---|
| **Tap Ctrl+Shift+Space** | open or close the chat panel |
| **Hold Ctrl+Shift+Space** and speak, then release | Ken transcribes and answers out loud |
| Click the pill | open the panel |
| Type and press **Enter** | ask by text |
| **Esc** | close the panel |

Try:
- "What's new on WhatsApp?"
- "Summarize my latest emails"
- "Reply to my brother: yes, I'll be there"
- "Remind me to call mom at 6"
- "Start a pomodoro"
- "☀" (or click ☀) for a briefing

**A send always opens a confirmation window.** Nothing is sent until you click **send**; pressing Enter there does nothing.

## 8.3 Teach Ken your writing style (recommended)

Open **Settings** (⚙ in the panel) → **Memory** → **Learn from my WhatsApp history**.

- **What it does:** Ken reads how you answered people in your 15 most-messaged chats (up to 150 replies each), so his drafts sound like you.
- **Cost:** well under a cent in embeddings.
- **When:** run it again any time; it skips what it already learned.

## 8.4 Wake word "Ken" (optional)

Open the tray menu → **Teach Ken my voice…**, and follow the window, about 3 minutes:

1. Say **"Ken"** 25 times. It tells you how: normally, louder, quieter…
2. Read a short text out loud. This teaches Ken what *isn't* his name.
3. Stay quiet for 8 seconds, so it hears your room.

Training runs on your PC. Then tick **Listen for "Ken"** in the tray menu.

- **Using it:** say "Ken" and he answers "At your service, Sir." Then say your request.
- **Follow-ups:** after he answers, you have ~8 s to follow up without saying "Ken" again. Say "thanks" to end.
- **Privacy:** the microphone is checked **only on your PC**. Audio leaves only after "Ken", the same as holding the hotkey.

## 8.5 Settings worth a look

| Section | What to set |
|---|---|
| **General** | model (OpenAI / local), writing tone (Egyptian / Saudi / US English), personality (Butler "Sir" / Plain) |
| **Voice** | engine (Edge is free and natural), the English and Arabic voice (▶ to preview), speed |
| **Shortcuts** | your words for people: "mom" → her chat, "boss" → an email address |
| **Nudges** | proactive reminders about messages waiting for your reply, quiet hours |
| **App** | a morning briefing at a set time |

Settings are saved to `<ken>\settings.json`, which is git-ignored. [`examples\settings.example.json`](../examples/settings.example.json) shows the common ones.

## ✅ You're done

Ken now:
- reads your WhatsApp, Gmail and Calendar;
- drafts in your style;
- waits for your click before anything leaves your PC.

To learn how it all works inside, see [ENGINEERING.md](../ENGINEERING.md) and [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md).
