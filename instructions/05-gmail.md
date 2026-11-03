# 5. Connect Gmail

Ken uses the open-source **[Gmail MCP server](https://github.com/GongRzhe/Gmail-MCP-Server)** (`@gongrzhe/server-gmail-autoauth-mcp`). It runs on Node.js.

Ken only lets the AI **read** and **send** (after your click). Delete, archive, label and draft tools are hidden from it.

## 5.1 Install Node.js

1. Go to **<https://nodejs.org/>** and download the **LTS** Windows installer.
2. Run it and keep the defaults.
3. **Close and reopen** PowerShell.

## 5.2 (Recommended) Install the Gmail server locally

`npx` downloads the server on every start, which takes ~18 s. A local copy starts instantly. In `<ken>`:

```powershell
mkdir tools\gmail-mcp -Force
cd tools\gmail-mcp
npm install @gongrzhe/server-gmail-autoauth-mcp@1.1.11
cd ..\..
```

This creates `<ken>\tools\gmail-mcp\node_modules\…`. The `tools\` folder is git-ignored.

## 5.3 Sign in

You need the file from [step 4](04-google-cloud-project.md) in `C:\Users\<you>\.gmail-mcp\gcp-oauth.keys.json`.

1. **Start the sign-in.** In `<ken>`:

   ```powershell
   node tools\gmail-mcp\node_modules\@gongrzhe\server-gmail-autoauth-mcp\dist\index.js auth
   ```

   If you skipped 5.2, use `npx @gongrzhe/server-gmail-autoauth-mcp auth` instead.
2. **Allow access in the browser.** It opens Google's sign-in:
   1. Choose **your Gmail account** (the test user from step 4.3).
   2. "Google hasn't verified this app" → click **Continue**. It's *your* app, so that's expected.
   3. Allow the requested access → **Continue**.
3. **Done:** the browser says authentication is complete, and the command finishes.

## ✅ Check

```powershell
dir $HOME\.gmail-mcp
```

You should now see **`credentials.json`** next to `gcp-oauth.keys.json`.

Next: **[6. Connect Google Calendar](06-google-calendar.md)**
