# 3. Build and pair the WhatsApp server

Ken reads and sends WhatsApp messages through **[mcp-whatsapp](https://github.com/Sealjay/mcp-whatsapp)**. It's an open-source server that logs in as a **linked device**, the same way WhatsApp Web does. You build it once, scan a QR code once, and from then on Ken starts it by himself.

> ⚠️ **Read first.** This is an **unofficial** WhatsApp client. WhatsApp can ban numbers that automate bulk messages. Ken never bulk-sends: he has a rate limit, every message needs your click, and there are no group broadcasts. Still, use it at your own risk.

It has no ready-made download, so you build it. That needs **Go** and a **C compiler**: its database library uses C.

## 3.1 Install Go

1. Go to **<https://go.dev/dl/>** and download the **Windows installer (`.msi`, amd64)** of the latest version (1.26 or newer).
2. Run it and keep the defaults.

## 3.2 Install a C compiler (MSYS2)

1. **Install MSYS2:** go to **<https://www.msys2.org/>**, download the installer (`msys2-x86_64-….exe`), run it, and keep the default folder **`C:\msys64`**.
2. **Install the compiler:** when it finishes, an **MSYS2 UCRT64** terminal opens (or find it in the Start menu). Paste this, press Enter, then press **Enter** again to accept:

   ```bash
   pacman -S --needed mingw-w64-ucrt-x86_64-gcc
   ```

3. **Add the compiler to your PATH:**
   1. Press **Start**, type `environment`, and open **"Edit the system environment variables"**.
   2. Click **Environment Variables…**.
   3. Under *User variables*, select **Path** and click **Edit…**.
   4. Click **New**, type `C:\msys64\ucrt64\bin`, then **OK**, **OK**, **OK**.
4. **Close every PowerShell window**, so the new PATH takes effect.

## 3.3 Download and build the server

Open a **new** PowerShell window in `<ken>`:

```powershell
cd $HOME\ken-assistant
git clone https://github.com/Sealjay/mcp-whatsapp.git whatsapp-mcp
cd whatsapp-mcp
git apply ..\patches\whatsapp-mcp-chat-index.patch
go env -w CGO_ENABLED=1
go build -o bin\whatsapp-mcp.exe .\cmd\whatsapp-mcp
cd ..
```

- **The folder must be named `whatsapp-mcp`** and sit **inside** `<ken>`. Ken looks for `<ken>\whatsapp-mcp\bin\whatsapp-mcp.exe`.
- **The `git apply` line is optional.** It adds a database index, so the chat list loads instantly instead of in ~20 s. If it says the patch doesn't apply (a newer server version), skip it.

## 3.4 Pair your phone (once)

1. **Start the server** in `<ken>`:

   ```powershell
   .\whatsapp-mcp\bin\whatsapp-mcp.exe -store .\whatsapp-mcp\store serve
   ```

   Leave this window open. It listens only on your own PC (`127.0.0.1:8765`).
2. **Open the pairing page:** in your browser, go to **<http://127.0.0.1:8765/pair>**. A QR code appears.
3. **Link your phone:** on your phone, open WhatsApp and go to **Linked devices**:
   - **iPhone:** **Settings → Linked Devices**;
   - **Android:** **⋮ (top right) → Linked devices**.

   Tap **Link a device** and scan the QR code on your screen.
4. **Wait for the sync:** the page confirms the link, and the server window starts syncing your chats. The first sync can take a few minutes for a big account.
5. **Stop the server:** press **Ctrl+C** in that window. From now on Ken starts it for you. This is the setting **"Start the WhatsApp server with Ken"**, on by default.

> **No camera?** On the pairing page, use the phone-number form. Then on your phone choose **Link a device → Link with phone number instead** and type the code.

> **Every ~20 days** WhatsApp renews the link. If Ken shows "WhatsApp isn't linked", open <http://127.0.0.1:8765/pair> again and re-scan.

## ✅ Check

- **Your session exists:** `<ken>\whatsapp-mcp\store\` now contains `whatsapp.db` and `messages.db`. This is your private session; it's git-ignored.
- **Ken can read it.** You'll do this check properly in [step 7](07-connect-mcp-servers.md), once `mcp_servers.json` is set. If you want to try now:
  1. Start the server again in one PowerShell window (the command from step 3.4.1).
  2. In a second window in `<ken>`, run `.\.venv\Scripts\python -m assistant`.
  3. There should be **no** "MCP server 'whatsapp' failed" line. Type `/tools` and look for `read   whatsapp__list_chats`, then `/quit`.

  (The desktop app starts the server by itself; the command-line chat doesn't.)

Next: **[4. Create your Google Cloud project](04-google-cloud-project.md)** (or skip to [step 7](07-connect-mcp-servers.md) if you don't want Gmail/Calendar).
