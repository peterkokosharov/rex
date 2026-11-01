# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""LiteLLM client using kikolib.http_client for OpenAI-compatible API."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator, Optional

from ..config import Config
from ..kikolib import http_client, retry
from ..kikolib.http_client import HttpBackend, Response


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

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "function": self.function}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCall":
        return cls(
            id=data["id"],
            type=data.get("type", "function"),
            function=data.get("function", {}),
        )


@dataclass
class ChatCompletionChunk:
    id: str
    model: str
    choices: list[dict[str, Any]]
    created: int
    object: str = "chat.completion.chunk"

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
        self._http = http_backend or http_client.client()
        self._retry_policy = retry.RetryPolicy()

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
                yield ChatCompletionChunk(**parsed)
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