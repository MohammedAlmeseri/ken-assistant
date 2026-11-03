# 9. Troubleshooting

Find the message you see, then do what the right column says. The app's log is `<ken>\data\app.log`; the WhatsApp server's log is `<ken>\data\whatsapp-server.log`.

## Installing

| You see | Do this |
|---|---|
| `'python' is not recognized` | Python isn't on PATH. Reinstall it and tick **Add python.exe to PATH** ([step 1.1](01-install-ken.md)), then open a new PowerShell window. |
| `pip install` fails on a voice package | Install without voice first: `pip install -r requirements.txt -r requirements-ui.txt`. Ken works by typing; add voice later. |
| Tests fail right after installing | Make sure you installed **all three** requirement files and run the tests with `.\.venv\Scripts\python -m pytest`, not a global Python. |

## OpenAI

| You see | Do this |
|---|---|
| `AuthenticationError` / `invalid_api_key` | The key in `.env` is wrong, or has spaces or quotes. Write it as `OPENAI_API_KEY=sk-…` and nothing else ([step 2](02-openai-key.md)). |
| `insufficient_quota` | Add credit at <https://platform.openai.com/settings/organization/billing>. |
| Answers take ~30 s | You're on the local model. Remove `ASSISTANT_BACKEND=local` from `.env`, or switch in **Settings → General**. |

## WhatsApp

| You see | Do this |
|---|---|
| `go-sqlite3 requires cgo to work` / `CGO_ENABLED=0` | Run `go env -w CGO_ENABLED=1` and check the C compiler: `gcc --version` must work in PowerShell. If it doesn't, redo [step 3.2](03-whatsapp-server.md) (PATH) and open a new window. |
| `gcc: executable file not found` | Same: `C:\msys64\ucrt64\bin` isn't on PATH, or PowerShell wasn't reopened. |
| "The WhatsApp server didn't start" | Check that `<ken>\whatsapp-mcp\bin\whatsapp-mcp.exe` exists (the folder name must be exactly `whatsapp-mcp`). Read `data\whatsapp-server.log`. |
| "WhatsApp isn't linked" | Open <http://127.0.0.1:8765/pair> and scan the QR again (WhatsApp renews the link about every 20 days). |
| Port 8765 already in use | Another copy is running: that's fine, Ken connects to it. To restart it, close it in **Task Manager → whatsapp-mcp.exe → End task**. |
| Chats load very slowly | Apply the index patch ([step 3.3](03-whatsapp-server.md)) and rebuild. |

## Gmail and Calendar

| You see | Do this |
|---|---|
| `OAuth keys file not found` | `gcp-oauth.keys.json` isn't in `C:\Users\<you>\.gmail-mcp\` (check the exact name; Windows may hide `.json`). Redo [step 4.5](04-google-cloud-project.md). |
| `Error 403: access_denied` in the browser | Your Google account isn't a **test user**. Add it in [step 4.3](04-google-cloud-project.md#43-set-up-the-consent-screen-who-may-sign-in). |
| `invalid_grant`, or Gmail/Calendar stop working after ~a week | The Testing-mode sign-in expired. **Gmail:** delete `%USERPROFILE%\.gmail-mcp\credentials.json` and redo [step 5.3](05-gmail.md#53-sign-in). **Calendar:** run `.\.venv\Scripts\python assistant\calendar_mcp.py --auth` again. |
| "Google Calendar isn't connected yet" | There's no `token.json` yet. Do [step 6](06-google-calendar.md). |
| "Gmail API has not been used in project … or it is disabled" | Enable the API ([step 4.2](04-google-cloud-project.md)) and wait a minute. |
| `MCP server 'gmail' failed to start: … not found` | Node isn't installed or isn't on PATH ([step 5.1](05-gmail.md)), or the path in `mcp_servers.json` doesn't match the local copy. Use the `npx` variant from [step 7](07-connect-mcp-servers.md). |

## `mcp_servers.json`

| You see | Do this |
|---|---|
| `mcp_servers.json not found` | Run `copy mcp_servers.example.json mcp_servers.json` in `<ken>`. |
| `JSONDecodeError` | A comma is missing or extra. Compare with [step 7](07-connect-mcp-servers.md); there's no comma after the last block. |

## Voice

| You see | Do this |
|---|---|
| Ken doesn't hear "Ken" | Tick **Listen for "Ken"** in the tray. Retrain in a quieter room, or with the microphone you actually use (tray → **Teach Ken my voice…**). |
| Ken wakes up by itself | Retrain, and say "Ken" in different ways during the 25 takes. |
| The voice sounds robotic | The free Edge voices need the internet; offline, Ken falls back to Windows voices. Check **Settings → Voice → engine: Edge**. |
| No Arabic voice offline | **Windows Settings → Time & language → Speech → Add voices** → add an Arabic voice. |
