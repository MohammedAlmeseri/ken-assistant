# Examples

Runnable demos of Ken's core ideas.

- **Fully offline:** no API key, no WhatsApp, no Google account.
- **Made-up data:** they use the local hash embedder instead of OpenAI embeddings, so similarity scores differ from the real app.
- **Real code:** every call goes into the same modules the app uses.

Run them from the repository root:

```bash
python examples/01_style_memory_rag.py
```

| File | What you'll see |
|---|---|
| [01_style_memory_rag.py](01_style_memory_rag.py) | "Their message → your reply" pairs in a vector store; retrieval filtered to one chat vs. across chats; the style block the model receives |
| [02_docs_rag.py](02_docs_rag.py) | Ken's docs split per heading, indexed once (re-embedded only when the files change), and searched by the `about_ken` tool |
| [03_safety_gate.py](03_safety_gate.py) | A scripted model that **obeys** a prompt-injection email; the send gate still blocks it. Plus an unknown recipient, an accepted send and a declined one |
| [04_chat_finder.py](04_chat_finder.py) | Arabic normalization, Arabic ↔ Latin and Franco-Arabic ("3abdo") matching, the activity bonus that picks the live chat |
| [05_shortcuts_and_facts.py](05_shortcuts_and_facts.py) | "mom" → exactly her chat (also "ابعت لماما"), family suggestions, "remember…" with a yearly birthday reminder |
| [06_nudges.py](06_nudges.py) | Proactive nudges: rule candidates → limits → AI check (stubbed), one per check, never repeated |

[settings.example.json](settings.example.json) shows the common settings. Copy it to `settings.json` in the repository root to start from it. The app writes that file when you press Save in Settings.
