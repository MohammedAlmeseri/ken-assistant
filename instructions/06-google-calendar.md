# 6. Connect Google Calendar

The Calendar server is part of Ken ([`assistant/calendar_mcp.py`](../assistant/calendar_mcp.py)). There's nothing to install: it reuses the Google client file from [step 4](04-google-cloud-project.md).

- **What it can do:** read your events and free time.
- **Adding events:** it adds an event **only** when you click **Add** on an event card.
- **What it never does:** edit or delete events.

## 6.1 Sign in

**Either** from PowerShell, in `<ken>`:

```powershell
.\.venv\Scripts\python assistant\calendar_mcp.py --auth
```

**or** later in the app: **Settings → Accounts → Connect Google Calendar**.

Then, in the browser:
1. Choose your account.
2. "Google hasn't verified this app" → **Continue**.
3. Allow both calendar permissions (seeing your calendars, and viewing and editing events) → **Continue**. Ken uses the "edit" part only to add the events you click **Add** on.

PowerShell then prints: `Google Calendar connected. Token saved to C:\Users\<you>\.calendar-mcp\token.json`.

> **Calendar stopped working after a week?** That's the Testing-mode expiry from step 4.3. Run the same command again.

## ✅ Check

```powershell
dir $HOME\.calendar-mcp
```

You should see **`token.json`**.

Next: **[7. Tell Ken where the servers are](07-connect-mcp-servers.md)**
