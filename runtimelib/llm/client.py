# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-opus-5-5, claude-sonnet-5-5
# Licensed under MIT License

"""LiteLLM client using kikolib.http for OpenAI-compatible API."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from typing import Any, AsyncIterator, Iterator, Optional

from ..config import Config
from kikolib import http, retry
from kikolib.http import HttpBackend, Response

# A LiteLLM ``max_tokens`` below this is taken as an output limit, not a window.
MIN_MAX_TOKENS_WINDOW = 32768


def _known_fields(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    """Keep only keys that are fields of dataclass ``cls``.

    OpenAI-compatible servers add fields over time (system_fingerprint,
    usage, service_tier, ...); unknown keys must not crash construction.
    """
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


@dataclass
class ChatMessage:
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {"role": self.role}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d


@dataclass
class ToolCall:
    id: str
    type: str = "function"
    function: dict[str, Any] = field(default_factory=dict)
    # Position of the call in a streamed response. Only set on stream deltas;
    # continuation deltas carry the index but no id. Never sent to the API.
    index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "function": self.function}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCall":
        index = data.get("index")
        return cls(
            id=data.get("id") or "",
            type=data.get("type") or "function",
            function=data.get("function") or {},
            index=index if isinstance(index, int) else None,
        )


@dataclass
class ChatCompletionChunk:
    id: str = ""
    model: str = ""
    choices: list[dict[str, Any]] = field(default_factory=list)
    created: int = 0
    object: str = "chat.completion.chunk"
    error: Any = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatCompletionChunk":
        """Build from API JSON, ignoring fields this class does not know."""
        return cls(**_known_fields(cls, data))

    @property
    def delta(self) -> dict[str, Any]:
        return self.choices[0].get("delta", {}) if self.choices else {}

    @property
    def content(self) -> str | None:
        return self.delta.get("content")

    @property
    def tool_calls(self) -> list[ToolCall] | None:
        tcs = self.delta.get("tool_calls")
        if tcs:
            return [ToolCall.from_dict(tc) for tc in tcs]
        return None

    @property
    def finish_reason(self) -> str | None:
        return self.choices[0].get("finish_reason") if self.choices else None


@dataclass
class ChatCompletion:
    id: str
    model: str
    choices: list[dict[str, Any]]
    created: int
    object: str = "chat.completion"
    usage: dict[str, int] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatCompletion":
        """Build from API JSON, ignoring fields this class does not know."""
        return cls(**_known_fields(cls, data))

    @property
    def message(self) -> ChatMessage:
        choice = self.choices[0] if self.choices else {}
        msg_data = choice.get("message", {})
        tool_calls = None
        if msg_data.get("tool_calls"):
            tool_calls = [ToolCall.from_dict(tc) for tc in msg_data["tool_calls"]]
        return ChatMessage(
            role=msg_data.get("role", "assistant"),
            content=msg_data.get("content"),
            tool_calls=tool_calls,
        )


class LiteLLMClient:
    """Client for LiteLLM proxy (OpenAI-compatible API)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        http_backend: Optional[HttpBackend] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = model
        self.timeout = timeout
        self._http = http_backend or http.client()
        self._retry_policy = retry.RetryPolicy()
        self._window_cache: dict[str, Optional[int]] = {}

    @classmethod
    def from_config(cls, config: Config) -> "LiteLLMClient":
        lite_cfg = config.get("liteLLM", {})
        base_url = lite_cfg.get("base_url") or os.environ.get("LITELLM_BASE_URL")
        api_key = lite_cfg.get("api_key") or os.environ.get("LITELLM_API_KEY")
        model = lite_cfg.get("model") or "gpt-4o"
        timeout = lite_cfg.get("timeout", 60.0)

        if not base_url:
            raise ValueError("liteLLM.base_url не е зададен в конфигурацията")
        if not api_key:
            raise ValueError("liteLLM.api_key не е зададен в конфигурацията")

        return cls(base_url, api_key, model, timeout)

    def _headers(self, stream: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if stream:
            headers["Accept"] = "text/event-stream"
        return headers

    def _parse_sse_line(self, line: str) -> Optional[dict[str, Any]]:
        """Parse a single SSE data line."""
        if not line.startswith("data: "):
            return None
        data = line[6:].strip()
        try:
            from ..native import parse_sse_data
            parsed = parse_sse_data(line)
        except Exception:
            parsed = None
        if parsed is not None:
            data = parsed
        if data == "[DONE]":
            return {"done": True}
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None

    def list_models(self) -> list[dict[str, Any]]:
        """GET /v1/models"""
        url = f"{self.base_url}/v1/models"
        resp = self._request_with_retry("GET", url, headers=self._headers())
        if resp.status != 200:
            raise RuntimeError(f"Failed to list models: {resp.status}")
        data = resp.json()
        return data.get("data", [])

    def context_window(self, model: Optional[str] = None, timeout: float = 3.0) -> Optional[int]:
        """Context window the endpoint reports for ``model``, or None.

        Tries the LiteLLM proxy ``GET /model/info`` (``max_input_tokens``;
        ``max_tokens`` only when it is at least 32768, since it is usually the
        output limit) and then ``GET /v1/models`` (``context_length`` and similar). One short
        attempt per model, cached for the life of the client; any failure
        returns None silently.
        """
        name = model or self.default_model
        if name in self._window_cache:
            return self._window_cache[name]
        found: Optional[int] = None
        try:
            found = self._probe_model_info(name, timeout) or self._probe_v1_models(name, timeout)
        except Exception:
            found = None
        self._window_cache[name] = found
        return found

    def _probe_get(self, path: str, timeout: float) -> Any:
        resp = self._http.request("GET", f"{self.base_url}{path}", headers=self._headers(), timeout=timeout)
        if resp.status != 200:
            return None
        return resp.json()

    @staticmethod
    def _positive(value: Any) -> Optional[int]:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    def _probe_model_info(self, name: str, timeout: float) -> Optional[int]:
        data = self._probe_get("/model/info", timeout)
        for entry in (data or {}).get("data") or []:
            if not isinstance(entry, dict) or entry.get("model_name") != name:
                continue
            info = entry.get("model_info") or {}
            found = self._positive(info.get("max_input_tokens"))
            if found:
                return found
            # LiteLLM's max_tokens is often the OUTPUT limit (4k-16k); trusting it
            # would compact far too early. Only a large value can be a window.
            out = self._positive(info.get("max_tokens"))
            return out if out and out >= MIN_MAX_TOKENS_WINDOW else None
        return None

    def _probe_v1_models(self, name: str, timeout: float) -> Optional[int]:
        data = self._probe_get("/v1/models", timeout)
        for entry in (data or {}).get("data") or []:
            if not isinstance(entry, dict) or entry.get("id") != name:
                continue
            for key in ("context_length", "context_window", "max_model_len", "max_context_length"):
                value = self._positive(entry.get(key))
                if value:
                    return value
        return None

    def chat_completion(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str | dict[str, Any]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> Response | Iterator[ChatCompletionChunk]:
        """POST /v1/chat/completions"""
        url = f"{self.base_url}/v1/chat/completions"

        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
        if max_tokens:
            payload["max_tokens"] = max_tokens

        body = json.dumps(payload).encode("utf-8")

        if stream:
            return self._stream_chat(url, body)
        else:
            resp = self._request_with_retry("POST", url, headers=self._headers(), body=body)
            return resp

    def _request_with_retry(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> Response:
        def send() -> Response:
            return self._http.request(
                method, url, headers=headers, body=body, timeout=self.timeout
            )

        resp, _ = retry.send_with_retry(send, self._retry_policy)
        return resp

    def _stream_chat(
        self, url: str, body: bytes
    ) -> Iterator[ChatCompletionChunk]:
        resp = self._http.request(
            "POST", url, headers=self._headers(stream=True), body=body, timeout=self.timeout, stream=True
        )

        try:
            for line in resp.iter_lines():
                if not line.strip():
                    continue
                parsed = self._parse_sse_line(line)
                if not parsed:
                    continue
                if parsed.get("done"):
                    break
                yield ChatCompletionChunk.from_dict(parsed)
        finally:
            resp.close()

    async def achat_completion(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str | dict[str, Any]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> Response | AsyncIterator[ChatCompletionChunk]:
        """Async version - delegates to sync for now (kikolib is sync)."""
        # TODO: implement true async when needed
        if stream:
            async def async_gen():
                for chunk in self.chat_completion(
                    messages, model, tools, tool_choice, temperature, max_tokens, stream=True
                ):
                    yield chunk
            return async_gen()
        else:
            return self.chat_completion(
                messages, model, tools, tool_choice, temperature, max_tokens, stream=False
            )


def create_client(config: Optional[Config] = None) -> LiteLLMClient:
    """Factory function to create client from global config."""
    if config is None:
        from ..config import get_config
        config = get_config()
    return LiteLLMClient.from_config(config)