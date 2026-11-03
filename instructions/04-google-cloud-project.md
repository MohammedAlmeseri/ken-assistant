# 4. Create your Google Cloud project

Gmail and Google Calendar need **your own** Google "OAuth client": a small file that lets programs on your PC ask *you* for permission. It's free. You make one, and both Gmail and Calendar use it.

> Google's console changes its menus from time to time. If a name differs slightly, look for the closest match. Older screens call the "Google Auth Platform" pages **APIs & Services → OAuth consent screen**.

## 4.1 Create a project

1. Go to **<https://console.cloud.google.com/>** and sign in with the Google account whose Gmail you want Ken to read.
2. **Create the project:** click the **project picker** at the top left, next to "Google Cloud", then **New project**. Name it `Ken` and click **Create**.
3. **Select it:** when it's ready, click **Select project**, or pick **Ken** in the project picker. Make sure **Ken** is shown at the top before continuing.

## 4.2 Turn on the Gmail and Calendar APIs

1. Go to **<https://console.cloud.google.com/apis/library/gmail.googleapis.com>** and click **Enable**.
2. Go to **<https://console.cloud.google.com/apis/library/calendar-json.googleapis.com>** and click **Enable**.

## 4.3 Set up the consent screen (who may sign in)

1. Go to **<https://console.cloud.google.com/auth/overview>** (Google Auth Platform) and click **Get started**.
2. Fill in the form:
   1. **App information:** App name `Ken`, User support email = your email → **Next**.
   2. **Audience:** **External** → **Next**.
   3. **Contact information:** your email → **Next**.
   4. Tick that you agree to the policy → **Continue** → **Create**.
3. **Add yourself as a test user:**
   1. Go to **<https://console.cloud.google.com/auth/audience>**.
   2. Under **Test users**, click **+ Add users**.
   3. Type **your own Gmail address** and click **Save**.

> **Why a test user?** Your app stays in "Testing" mode: only the people you list can sign in, which is exactly what you want for a private assistant. One catch: Google expires sign-ins of Testing apps after about **7 days**. When Gmail or Calendar stops working, sign in again ([step 5.3](05-gmail.md#53-sign-in), [step 6](06-google-calendar.md)).

## 4.4 Create the OAuth client and download it

1. Go to **<https://console.cloud.google.com/auth/clients>** and click **+ Create client**.
2. **Application type:** **Desktop app**. Name: `Ken desktop`. Click **Create**.
3. **Download the file:** in the dialog that appears, click **Download JSON**. You get a file named like `client_secret_1234…apps.googleusercontent.com.json` in your **Downloads** folder.

## 4.5 Put the file where Ken and Gmail look for it

Rename the downloaded file to **`gcp-oauth.keys.json`** and move it to **`C:\Users\<you>\.gmail-mcp\`**. In PowerShell:

```powershell
mkdir $HOME\.gmail-mcp -Force
Move-Item $HOME\Downloads\client_secret_*.json $HOME\.gmail-mcp\gcp-oauth.keys.json
```

> Treat this file like a password: don't email it, and don't put it in a repository. Ken's `.gitignore` already blocks it.

## ✅ Check

```powershell
dir $HOME\.gmail-mcp
```

You should see **`gcp-oauth.keys.json`**.

Next: **[5. Connect Gmail](05-gmail.md)**
