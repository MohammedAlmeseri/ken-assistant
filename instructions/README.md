# Setup instructions

Step by step, from nothing to Ken reading your WhatsApp, Gmail and Google Calendar. Follow the files **in order**. Each step ends with a **✅ Check** so you know it worked before moving on.

| # | Step | Time | Needed for |
|---|---|---|---|
| 1 | [Install Ken](01-install-ken.md) | 5 min | everything |
| 2 | [Add your OpenAI key](02-openai-key.md), or go fully local | 3 min | the AI |
| 3 | [Build and pair the WhatsApp server](03-whatsapp-server.md) | 15 min | WhatsApp |
| 4 | [Create your Google Cloud project](04-google-cloud-project.md) | 10 min | Gmail + Calendar |
| 5 | [Connect Gmail](05-gmail.md) | 5 min | Gmail |
| 6 | [Connect Google Calendar](06-google-calendar.md) | 2 min | Calendar |
| 7 | [Tell Ken where the servers are (`mcp_servers.json`)](07-connect-mcp-servers.md) | 3 min | all three |
| 8 | [First start: voice, wake word, your writing style](08-first-start.md) | 10 min | the full experience |
| 9 | [Troubleshooting](09-troubleshooting.md) | | when something fails |

**You don't need all of it.**
- Ken works with any subset of WhatsApp, Gmail and Calendar. A missing server just shows a red dot in the app.
- To only look around, do step 1 and run `python -m assistant.ui --mock` (made-up chats). Step 2 is still needed for answers, unless you use the local model.

## Conventions used here

- **Windows 10/11 and PowerShell.** To open PowerShell: press **Start**, type `PowerShell`, press **Enter**.
- **`<ken>`** means the folder you cloned Ken into, for example `C:\Users\you\ken-assistant`.
- **Commands** are in grey boxes. Copy the whole box, paste it into PowerShell with **right-click**, and press **Enter**.
- **Never share** anything you put in `.env` or in `%USERPROFILE%\.gmail-mcp\`. Those are your keys.

## What goes where (the full picture)

```
<ken>\                                   ← this repository
├── .env                                 ← your OpenAI key (step 2)
├── mcp_servers.json                     ← which servers Ken connects to (step 7)
├── whatsapp-mcp\                        ← the WhatsApp server you build (step 3)
│   ├── bin\whatsapp-mcp.exe
│   └── store\                           ← your WhatsApp session + messages (private!)
└── data\                                ← Ken's memory, logs, wake-word model (created on first start)

C:\Users\<you>\.gmail-mcp\
├── gcp-oauth.keys.json                  ← the Google "client" file you download (step 4)
└── credentials.json                     ← created when you sign in to Gmail (step 5)

C:\Users\<you>\.calendar-mcp\
└── token.json                           ← created when you connect Calendar (step 6)
```

Everything in this picture except the repository's own files is **git-ignored**: it can't be pushed by accident.
