# 1. Install Ken

## 1.1 Install Python

1. Go to **<https://www.python.org/downloads/windows/>** and download the latest **Python 3.12 or 3.13**. Choose the "Windows installer (64-bit)".
2. Run the installer. On the first screen, **tick "Add python.exe to PATH"** at the bottom, then click **Install Now**.

## 1.2 Install Git

1. Go to **<https://git-scm.com/download/win>** and download "64-bit Git for Windows Setup".
2. Run it and keep all the defaults (**Next** … **Install**).

## 1.3 Download Ken

Open a **new** PowerShell window (so it sees Python and Git) and run:

```powershell
cd $HOME
git clone https://github.com/MohammedAlmeseri/ken-assistant.git
cd ken-assistant
```

Ken is now in `C:\Users\<you>\ken-assistant`. That's the `<ken>` folder in these guides.

## 1.4 Create a virtual environment and install the packages

Still in `<ken>`:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install -r requirements.txt -r requirements-ui.txt -r requirements-voice.txt
```

This takes a few minutes: the voice packages are large.

> **Skipping voice?** You can leave out `-r requirements-voice.txt`: Ken then works by typing only. The test suite below expects all three files, though.

## 1.5 Create your private files from the templates

```powershell
copy .env.example .env
copy mcp_servers.example.json mcp_servers.json
```

- `.env` will hold your keys (step 2).
- `mcp_servers.json` tells Ken which servers to start (step 7).

Both files are git-ignored.

## ✅ Check

```powershell
.\.venv\Scripts\python -m pytest -q
```

You should see **`151 passed`**. The tests never use the internet or any key.

You can also run an example:

```powershell
.\.venv\Scripts\python examples\03_safety_gate.py
```

Next: **[2. Add your OpenAI key](02-openai-key.md)**
