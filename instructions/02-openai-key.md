# 2. Add your OpenAI key (or go fully local)

Ken's "brain" is OpenAI by default (`gpt-6-luna`, very cheap), and so are speech-to-text and the embeddings for his memory. **Option B** runs everything on your PC instead.

## Option A: OpenAI (recommended)

1. **Create an account:** go to **<https://platform.openai.com/>** and sign up or log in.
2. **Add credit:** go to **<https://platform.openai.com/settings/organization/billing>**, click **Add payment details**, and add a small amount (for example $5). Normal use is a few cents a day.
3. **Create a key:** go to **<https://platform.openai.com/api-keys>** and click **+ Create new secret key**.
   - Name: `Ken`.
   - Permissions: **All**, or Restricted with *Model capabilities: Write*.
   - Click **Create secret key**.
4. **Copy the key now.** It starts with `sk-` and is shown **only once**.
5. **Put it in `.env`.** In PowerShell, in `<ken>`, run:

   ```powershell
   notepad .env
   ```

   Find the line `OPENAI_API_KEY=` and paste your key right after the `=`, with no spaces and no quotes:

   ```
   OPENAI_API_KEY=sk-proj-AbC123...
   ```

   Save (**Ctrl+S**) and close Notepad.

### Optional: YouTube key ("Ken, play … on YouTube")

Without it, Ken opens the YouTube results page instead of the video. To add one, after you've done [step 4](04-google-cloud-project.md):

1. **Enable the API:** go to **<https://console.cloud.google.com/apis/library/youtube.googleapis.com>** (your Ken project selected) and click **Enable**.
2. **Create the key:** go to **<https://console.cloud.google.com/apis/credentials>** and click **+ Create credentials → API key**. Copy the key.
3. **Restrict it (recommended):** click the new key. Under **API restrictions**, choose **Restrict key → YouTube Data API v3**, then **Save**.
4. **Save it:** in `notepad .env`, paste the key after `YOUTUBE_API_KEY=`.

## Option B: fully local (nothing leaves your PC)

Slower: about 30 s per answer on a laptop CPU, and weaker at Arabic.

1. Go to **<https://ollama.com/download>**, download Ollama for Windows, and install it.
2. In PowerShell:

   ```powershell
   ollama pull qwen3:1.7b
   ```

3. In `notepad .env`, remove the `#` in front of `ASSISTANT_BACKEND` and set it to `local`:

   ```
   ASSISTANT_BACKEND=local
   ```

## ✅ Check

```powershell
.\.venv\Scripts\python -m assistant --mock
```

Type `What's new on WhatsApp?` and press Enter. You should get a summary of the **made-up** chats, such as "Brother asked about Friday". Type `/quit` to leave.

> If you see `AuthenticationError`, the key is wrong or has spaces around it. If you see `insufficient_quota`, add credit (step A2).

Next: **[3. Build and pair the WhatsApp server](03-whatsapp-server.md)**
