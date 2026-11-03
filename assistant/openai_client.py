"""The one place OpenAI clients are made, so every HTTP request is counted and logged.

Each request (retries included: the SDK retries failed calls up to twice) adds a line to
data/openai_requests.jsonl: time, tag ("app" or "eval"), purpose, endpoint, model, status.
The panel shows today's count from it, so the number can be compared with OpenAI's dashboard.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime

from .config import DATA_DIR

# ----------------------------------------------------------------------------
# The request log
#   Every OpenAI request (retries included) is one line in data/openai_requests.jsonl.
# ----------------------------------------------------------------------------

LOG = DATA_DIR / "openai_requests.jsonl"
TAG = "app"  # evals/run_eval.py sets "eval"
_lock = threading.Lock()
_today: dict[str, int] = {}  # date -> requests, loaded from the log once, then counted live


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _api_key() -> str:
    return (os.getenv("OPENAI_API_KEY") or "").strip()  # a stray newline breaks the HTTP headers


def _model_of(request) -> str:
    try:
        return str(json.loads(request.content).get("model", ""))
    except Exception:  # multipart (audio upload) or a streamed body
        return ""


# ----------------------------------------------------------------------------
# Logging and counting requests
# ----------------------------------------------------------------------------

def record(request, status: int, purpose: str) -> None:
    now = datetime.now().astimezone()
    entry = {"time": now.isoformat(timespec="seconds"), "tag": TAG, "purpose": purpose,
             "endpoint": request.url.path.removeprefix("/v1/"), "model": _model_of(request), "status": status}
    with _lock:
        day = now.date().isoformat()
        _today[day] = requests_today() + 1 if day not in _today else _today[day] + 1
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass


def requests_today() -> int:
    """Requests logged today (all tags), counting from the log file the first time."""
    day = datetime.now().astimezone().date().isoformat()
    if day not in _today:
        n = 0
        if LOG.exists():
            with LOG.open(encoding="utf-8") as f:
                n = sum(1 for line in f if line.startswith('{"time": "' + day))
        _today[day] = n
    return _today[day]


# ----------------------------------------------------------------------------
# Client factories
#   The only way the app makes OpenAI clients, so nothing escapes the log, and the tests can swap
#   the transport for one that refuses real calls.
# ----------------------------------------------------------------------------

def async_client(purpose: str, **http):
    from openai import AsyncOpenAI, DefaultAsyncHttpxClient

    async def on_response(response) -> None:
        record(response.request, response.status_code, purpose)

    return AsyncOpenAI(api_key=_api_key(),
                       http_client=DefaultAsyncHttpxClient(event_hooks={"response": [on_response]}, **http))


def sync_client(purpose: str, **http):
    from openai import DefaultHttpxClient, OpenAI

    def on_response(response) -> None:
        record(response.request, response.status_code, purpose)

    return OpenAI(api_key=_api_key(),
                  http_client=DefaultHttpxClient(event_hooks={"response": [on_response]}, **http))
