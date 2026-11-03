"""LLM backends behind one interface: OpenAI (default) and local Ollama.

The agent keeps history in a neutral format and each backend converts it:
    {"role": "system" | "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [ToolCall, ...]}
    {"role": "tool", "content": str, "tool_call_id": str, "tool_name": str}
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import Settings


# ----------------------------------------------------------------------------
# Shared types
#   Tool definitions, tool calls, replies and token usage: the same for every backend.
# ----------------------------------------------------------------------------

@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class Usage:
    """Token usage. `cached` is the part of `input` the provider served from its prompt cache."""

    calls: int = 0
    input: int = 0
    cached: int = 0
    output: int = 0

    def add(self, input: int, output: int, cached: int = 0) -> None:
        self.calls += 1
        self.input += input or 0
        self.output += output or 0
        self.cached += cached or 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.calls + other.calls, self.input + other.input, self.cached + other.cached, self.output + other.output)

    def __sub__(self, other: "Usage") -> "Usage":
        return Usage(self.calls - other.calls, self.input - other.input, self.cached - other.cached, self.output - other.output)

    @property
    def total(self) -> int:
        return self.input + self.output

    def cost(self, prices: tuple[float, float, float] | None) -> float | None:
        """prices = USD per 1M tokens: (input, cached input, output)."""
        if prices is None:
            return None
        fresh = self.input - self.cached
        return (fresh * prices[0] + self.cached * prices[1] + self.output * prices[2]) / 1_000_000


# ----------------------------------------------------------------------------
# The cost footer
# ----------------------------------------------------------------------------

def usage_line(usage: Usage, prices: tuple[float, float, float] | None, local: bool = False,
               voice_seconds: float = 0.0, voice_cost: float = 0.0, requests_today: int = 0) -> str:
    """The panel's footer: tokens, minutes of OpenAI voice (speech-to-text + spoken replies) and the
    session total, then every OpenAI request made today (any purpose, evals included)."""
    if not usage.calls and not voice_seconds:
        return f"{requests_today:,} OpenAI requests today" if requests_today else ""
    parts = [f"{usage.total:,} tokens" + (" · local, free" if local else "")]
    if voice_seconds:
        parts.append(f"voice {int(voice_seconds // 60)}:{int(voice_seconds % 60):02d}")
    text_cost = 0.0 if local else usage.cost(prices)
    if text_cost is not None and (text_cost or voice_cost):
        total = f"≈${text_cost + voice_cost:.4f}"
        parts.append(total + (f" (voice ${voice_cost:.4f})" if voice_cost else ""))
    return " · ".join(parts) + " this session" + (f" · {requests_today:,} requests today" if requests_today else "")


# ----------------------------------------------------------------------------
# Backend interface
#   chat() for tool-using turns, complete() for short text.
# ----------------------------------------------------------------------------

class LLMBackend(Protocol):
    name: str
    model: str
    usage: Usage  # running total for this backend; callers diff it to get per-turn usage

    async def chat(self, messages: list[dict], tools: list[ToolSpec]) -> LLMReply: ...

    async def complete(self, messages: list[dict], max_tokens: int) -> str: ...


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _schema(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params or params.get("type") != "object":
        return {"type": "object", "properties": {}}
    return params


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}
    return value if isinstance(value, dict) else {"_raw": value}


# ----------------------------------------------------------------------------
# Model-specific parameters
#   Reasoning models (gpt-5/6, o-series) take max_completion_tokens and reasoning_effort instead
#   of temperature / max_tokens.
# ----------------------------------------------------------------------------

def openai_params(model: str, max_tokens: int | None, temperature: float, effort: str) -> dict[str, Any]:
    """Sampling parameters the model accepts. GPT-5/6 and o-series are reasoning models: they reject
    max_tokens and a custom temperature, and take max_completion_tokens and reasoning_effort."""
    if model.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
        params: dict[str, Any] = {"reasoning_effort": effort} if effort else {}
        if max_tokens:
            params["max_completion_tokens"] = max_tokens
        return params
    params = {"temperature": temperature}
    if max_tokens:
        params["max_tokens"] = max_tokens
    return params


# ----------------------------------------------------------------------------
# OpenAI backend
#   Chat Completions with tools; usage (incl. cached input) counted per call.
# ----------------------------------------------------------------------------

class OpenAIBackend:
    name = "openai"

    def __init__(self, settings: Settings, model: str | None = None, purpose: str = "chat"):
        from .openai_client import async_client

        # Strip: a stray newline in the key breaks the HTTP headers with an unhelpful "Connection error".
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key or api_key == "sk-...":
            raise RuntimeError("OPENAI_API_KEY is not set. Put it in .env (see .env.example).")
        self.client = async_client(purpose)
        self.model = model or settings.openai_model
        self.temperature = settings.temperature
        self.effort = settings.reasoning_effort
        self.usage = Usage()

    def params(self, max_tokens: int | None = None, temperature: float | None = None) -> dict[str, Any]:
        return openai_params(self.model, max_tokens, self.temperature if temperature is None else temperature, self.effort)

    def _record(self, resp) -> None:
        u = getattr(resp, "usage", None)
        if u is None:
            return
        details = getattr(u, "prompt_tokens_details", None)
        self.usage.add(u.prompt_tokens, u.completion_tokens, getattr(details, "cached_tokens", 0) or 0)

    @staticmethod
    def _convert(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)},
                        }
                        for c in m["tool_calls"]
                    ]
                out.append(msg)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                out.append({"role": m["role"], "content": m["content"]})
        return out

    async def chat(self, messages: list[dict], tools: list[ToolSpec]) -> LLMReply:
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": _schema(t.parameters)}}
                for t in tools
            ]
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=self._convert(messages),
            **self.params(),
            **kwargs,
        )
        self._record(resp)
        msg = resp.choices[0].message
        calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=_parse_args(c.function.arguments))
            for c in (msg.tool_calls or [])
        ]
        return LLMReply(text=msg.content or "", tool_calls=calls)

    async def complete(self, messages: list[dict], max_tokens: int) -> str:
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=self._convert(messages),
            **self.params(max_tokens, temperature=0.3),
        )
        self._record(resp)
        return (resp.choices[0].message.content or "").strip()


# ----------------------------------------------------------------------------
# Local backend (Ollama)
#   A private fallback: everything stays on the machine (slow on a laptop CPU).
# ----------------------------------------------------------------------------

class OllamaBackend:
    name = "local"

    def __init__(self, settings: Settings):
        from ollama import AsyncClient

        self.client = AsyncClient(host=settings.ollama_host)
        self.model = settings.local_model
        self.options = {"num_ctx": settings.num_ctx, "temperature": settings.temperature,
                        "num_gpu": settings.local_gpu_layers}
        self.usage = Usage()

    def _record(self, resp) -> None:
        self.usage.add(getattr(resp, "prompt_eval_count", 0) or 0, getattr(resp, "eval_count", 0) or 0)

    @staticmethod
    def _convert(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or ""}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"function": {"name": c.name, "arguments": c.arguments}} for c in m["tool_calls"]]
                out.append(msg)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_name": m["tool_name"], "content": m["content"]})
            else:
                out.append({"role": m["role"], "content": m["content"]})
        return out

    async def chat(self, messages: list[dict], tools: list[ToolSpec]) -> LLMReply:
        resp = await self.client.chat(
            model=self.model,
            messages=self._convert(messages),
            tools=[
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": _schema(t.parameters)}}
                for t in tools
            ] or None,
            think=False,
            keep_alive="30m",
            options=self.options,
        )
        self._record(resp)
        calls = [
            ToolCall(id=uuid.uuid4().hex[:12], name=c.function.name, arguments=_parse_args(c.function.arguments))
            for c in (resp.message.tool_calls or [])
        ]
        return LLMReply(text=resp.message.content or "", tool_calls=calls)

    async def complete(self, messages: list[dict], max_tokens: int) -> str:
        resp = await self.client.chat(
            model=self.model,
            messages=self._convert(messages),
            think=False,
            options={**self.options, "num_predict": max_tokens},
        )
        self._record(resp)
        return (resp.message.content or "").strip()


# ----------------------------------------------------------------------------
# Choosing a backend
# ----------------------------------------------------------------------------

def make_backend(settings: Settings) -> LLMBackend:
    if settings.backend == "openai":
        return OpenAIBackend(settings)
    if settings.backend == "local":
        return OllamaBackend(settings)
    raise ValueError(f"Unknown backend: {settings.backend!r} (use 'openai' or 'local')")
