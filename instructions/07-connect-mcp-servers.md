# 7. Tell Ken where the servers are (`mcp_servers.json`)

Ken talks to WhatsApp, Gmail and Calendar through **MCP servers** (Model Context Protocol). The file **`<ken>\mcp_servers.json`** lists them. You created it from the template in [step 1.5](01-install-ken.md).

## 7.1 Open the file

```powershell
notepad mcp_servers.json
```

## 7.2 Make it look like this

This is the recommended setup, with the local Gmail copy from [step 5.2](05-gmail.md). Replace the whole content and save:

```json
{
  "mcpServers": {
    "whatsapp": {
      "url": "http://127.0.0.1:8765/mcp"
    },
    "gmail": {
      "command": "node",
      "args": ["./tools/gmail-mcp/node_modules/@gongrzhe/server-gmail-autoauth-mcp/dist/index.js"]
    },
    "calendar": {
      "command": "./.venv/Scripts/python.exe",
      "args": ["./assistant/calendar_mcp.py"]
    }
  }
}
```

**If you skipped step 5.2** (no local copy), use this `gmail` block instead. It works, but starts ~18 s slower:

```json
    "gmail": {
      "command": "npx",
      "args": ["-y", "@gongrzhe/server-gmail-autoauth-mcp@1.1.11"]
    },
```

**If you don't use one of the services,** delete its whole block, and mind the commas: there's no comma after the last block.

## What each line means

| Entry | Kind | What Ken does with it |
|---|---|---|
| `whatsapp` → `url` | **HTTP** server | Connects to the WhatsApp server at that address. If nothing is listening there, Ken first starts `<ken>\whatsapp-mcp\bin\whatsapp-mcp.exe` himself, waits up to 15 s, and logs to `data\whatsapp-server.log`. |
| `gmail` → `command` + `args` | **stdio** server | Starts `node …index.js` as a child process and talks to it through stdin/stdout. It reads your sign-in from `%USERPROFILE%\.gmail-mcp\`. |
| `calendar` → `command` + `args` | **stdio** server | Starts Ken's own Python calendar server with the virtual environment's Python. It reads `%USERPROFILE%\.calendar-mcp\token.json`. |

- **Paths that start with `./`** are relative to the folder of `mcp_servers.json`, which is `<ken>`.
- **Tool names get the server's name in front,** for example `whatsapp__send_message` or `gmail__search_emails`. Ken's safety allowlist reads those names and hides every tool that deletes, archives, labels, drafts or creates. The model never sees them.

## ✅ Check

1. **Start the command-line chat.** If you set up WhatsApp, first start its server in another window (the command from [step 3.4](03-whatsapp-server.md#34-pair-your-phone-once)); the desktop app does this by itself, the command-line chat doesn't. Then, in `<ken>`:

   ```powershell
   .\.venv\Scripts\python -m assistant
   ```

   - **Good:** no lines like `⚠ MCP server 'gmail' failed to start: …`.
   - **If you do see one,** the message says what's wrong. See [Troubleshooting](09-troubleshooting.md).
2. **List the tools.** Type `/tools`. You should see tools from each server you set up, such as:

   ```
   read   whatsapp__list_chats
   send   whatsapp__send_message
   read   gmail__search_emails
   send   gmail__send_email
   read   calendar__list_events
   ```

   (`calendar__create_event` is deliberately missing: only the event card's **Add** button can call it.)
3. **Try it:** ask `What's on my calendar tomorrow?` and `Summarize my latest emails`, then type `/quit`.

Next: **[8. First start](08-first-start.md)**
