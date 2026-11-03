# Setup

This is the short version. For click-by-click steps with a check at the end of each, see **[instructions/](../instructions/)**.

**You can skip all of this** to look around first: the tests and [examples/](../examples/) run offline, and `python -m assistant.ui --mock` shows the app with made-up chats. The mock still uses the OpenAI key for answers, or Ollama with `ASSISTANT_BACKEND=local`.

## 0. Requirements

- Windows 10/11. The desktop app uses Windows voices and window helpers; the core runs anywhere.
- Python 3.11+.
- Optional:
  - **Go 1.22+** to build the WhatsApp server;
  - **Node 18+** for the Gmail server;
  - **Ollama** for the private local model.

## 1. Python environment

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt          # core: agent, MCP, OpenAI, Google Calendar
.venv\Scripts\pip install -r requirements-ui.txt       # the desktop app
.venv\Scripts\pip install -r requirements-voice.txt    # voice: speech-to-text, voices, wake word (optional)
.venv\Scripts\python -m pytest -q                      # 151 offline tests
```

## 2. Keys

```bash
copy .env.example .env
```

1. Put your OpenAI key in `.env`. It's git-ignored; never commit it.
2. For a fully local setup, leave the key empty, set `ASSISTANT_BACKEND=local` and run `ollama pull qwen3:1.7b`.
3. Optionally, add a `YOUTUBE_API_KEY` so "play X on YouTube" opens the top video directly.

## 3. MCP servers

```bash
copy mcp_servers.example.json mcp_servers.json
```

`mcp_servers.json` lists the servers Ken starts or connects to. Paths that start with `./` are relative to that file.

### WhatsApp: [sealjay/mcp-whatsapp](https://github.com/Sealjay/mcp-whatsapp)

1. **Build the server** into a `whatsapp-mcp/` folder next to `assistant/`. That folder is git-ignored, because its `store/` holds your WhatsApp session:

   ```bash
   git clone https://github.com/Sealjay/mcp-whatsapp.git whatsapp-mcp
   cd whatsapp-mcp
   git apply ../patches/whatsapp-mcp-chat-index.patch   # optional: adds an index, ~20 s → instant chat lists
   go build -o bin/whatsapp-mcp.exe ./cmd/whatsapp-mcp
   ```

2. **Start it:** `start_whatsapp.ps1`, or let Ken start it ("Start the WhatsApp server with Ken" is on by default). It listens on `127.0.0.1:8765` only.
3. **Pair it once:** open <http://127.0.0.1:8765/pair> and scan the QR code in WhatsApp → *Linked devices*.

> This is an **unofficial** client. Ken never bulk-sends: there's a rate limit, and no multi-person shortcuts. Still, use it at your own risk.

### Gmail: [@gongrzhe/server-gmail-autoauth-mcp](https://github.com/GongRzhe/Gmail-MCP-Server)

1. **Create OAuth credentials:**
   1. In [Google Cloud Console](https://console.cloud.google.com/), create a project.
   2. Enable the **Gmail API** and the **Google Calendar API**.
   3. Create an **OAuth client ID** of type *Desktop app*.
   4. Download the JSON file and rename it `gcp-oauth.keys.json`.
2. **Authorize:**

   ```bash
   mkdir %USERPROFILE%\.gmail-mcp
   move gcp-oauth.keys.json %USERPROFILE%\.gmail-mcp\
   npx @gongrzhe/server-gmail-autoauth-mcp auth
   ```

3. **Optional:** `npx` takes a few seconds to start every time. For instant startup, install the package into `tools/gmail-mcp` and point `mcp_servers.json` at `node ./tools/gmail-mcp/node_modules/@gongrzhe/server-gmail-autoauth-mcp/dist/index.js`.

### Google Calendar: Ken's own server ([assistant/calendar_mcp.py](../assistant/calendar_mcp.py))

- It uses the same OAuth client as Gmail.
- In the app, open **Settings → Accounts → Connect Google Calendar**, allow access in the browser, then restart Ken. You can also run `python assistant/calendar_mcp.py --auth`.
- Ken reads events and free time. He adds an event only when you click **Add** on a card, and never edits or deletes.

## 4. Run

```bash
.venv\Scripts\pythonw -m assistant.ui          # the app (no console window); or double-click "Start Assistant.bat"
.venv\Scripts\python  -m assistant.ui --mock   # made-up data, with a console for logs
.venv\Scripts\python  -m assistant             # the command-line chat (type /help)
```

- **Talk to Ken:**
  - tap **Ctrl+Shift+Space** to open the panel; hold it to talk;
  - or turn on **Listen for "Ken"** in the tray menu.
- **Wake word:** the first time, Ken asks to learn your voice: 25 takes of "Ken", a short read-aloud text and some room noise. That's about 3 minutes, and it all stays on your machine.
- **Draft your replies like you:** **Settings → Memory → Learn from my WhatsApp history** imports reply pairs from your 15 most-messaged chats. It costs well under a cent in embeddings.

## 5. Where your data lives

All of this is git-ignored:

| Path | What |
|---|---|
| `.env`, `settings.json`, `mcp_servers.json` | your keys and settings |
| `data/memory.db` | vector store: style memory, facts, Ken's docs |
| `data/audit.jsonl`, `data/openai_requests.jsonl` | every send and every API request |
| `data/wake/` | your wake-word model and recordings |
| `whatsapp-mcp/store/` | your WhatsApp session and messages |
| `~/.gmail-mcp/`, `~/.calendar-mcp/` | Google OAuth client and tokens |
