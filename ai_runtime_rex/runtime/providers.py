# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License
"""Native provider adapters (stdlib HTTP only) behind the OpenAI-shaped ``chat(body)`` seam of ``GatewayLLM``.

The internal representation is the OpenAI chat shape (messages with ``tool_calls`` / ``tool`` results, function
tool schemas, ``chat.completion.chunk`` dicts). The ``openai`` provider needs no adapter (the engine's own client
talks /v1/chat/completions: OpenAI, LiteLLM, kiko, NIM, vLLM, Ollama, llama.cpp, OpenRouter). This module maps that
shape to and from:

- ``anthropic``: native Messages API, ``POST {base}/v1/messages`` (x-api-key, anthropic-version, content blocks,
  tool_use / tool_result, SSE event types).
- ``gemini``: native ``POST {base}/v1beta/models/{model}:streamGenerateContent?alt=sse`` (x-goog-api-key,
  contents/parts, functionDeclarations / functionCall / functionResponse).

Keys travel only in request headers and are never put into URLs, errors or logs.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from urllib.parse import quote
from collections.abc import Iterator
from typing import Any, Optional

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 8192  # output limit for models not listed below
# Claude Sonnet / Opus write whole files through tool arguments; 8192 cut them off ("Invalid tool arguments").
LARGE_OUTPUT_MODELS = ("sonnet", "opus")
LARGE_MAX_TOKENS = 32000
MAX_RESPONSE_BYTES = 64 * 1024 * 1024  # one streamed model reply; more is a broken or hostile endpoint


class ProviderError(RuntimeError):
    pass


def model_max_tokens(model: str) -> int:
    """Output token limit to ask for when the caller set none."""
    name = (model or "").lower()
    return LARGE_MAX_TOKENS if any(m in name for m in LARGE_OUTPUT_MODELS) else DEFAULT_MAX_TOKENS


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: urllib would resend the key headers (x-api-key, x-goog-api-key, Authorization) to
    whatever host the Location names. An API endpoint that redirects is misconfigured; say so instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise ProviderError(f"HTTP {code} redirect from {urllib.request.urlparse(req.full_url).netloc} refused "
                            f"(keys are never sent on); use the final URL as base URL")


_OPENER = urllib.request.build_opener(_NoRedirect)


def _open(url: str, headers: dict[str, str], body: dict, timeout: float):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "text/event-stream", **headers})
    try:
        return _OPENER.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(600).decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001
            detail = ""
        raise ProviderError(f"HTTP {exc.code} from {urllib.request.urlparse(url).netloc}: {detail}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise ProviderError(f"cannot reach {urllib.request.urlparse(url).netloc}: {getattr(exc, 'reason', exc)}") from None


def _sse(resp: Any) -> Iterator[tuple[str, str]]:
    """Yield (event, data) from a Server-Sent-Events body (at most ``MAX_RESPONSE_BYTES``)."""
    event, data = "", []
    total = 0
    for raw in resp:
        total += len(raw)
        if total > MAX_RESPONSE_BYTES:
            raise ProviderError(f"model response too large (over {MAX_RESPONSE_BYTES} bytes)")
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data:
                yield event, "\n".join(data)
            event, data = "", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, "\n".join(data)


def _chunk(delta: dict, finish: Optional[str] = None, usage: Optional[dict] = None, model: str = "") -> dict:
    out: dict[str, Any] = {"model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage:
        out["usage"] = usage
    return out


def _tool_calls_of(body: dict) -> list[dict]:
    """OpenAI function tool schemas as plain (name, description, parameters) dicts."""
    out = []
    for t in body.get("tools") or []:
        fn = t.get("function", t)
        out.append({"name": fn["name"], "description": fn.get("description", ""),
                    "parameters": fn.get("parameters") or {"type": "object", "properties": {}}})
    return out


def _loads(text: Any) -> dict:
    if isinstance(text, dict):
        return text
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {"value": value}
    except json.JSONDecodeError:
        return {}


def _text(content: Any) -> str:
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return "" if content is None else str(content)


def aggregate(chunks: Iterator[dict]) -> dict:
    """Fold chunk dicts into one non-streaming ``chat.completion`` dict."""
    content, calls, finish, usage = [], {}, "stop", None
    for ch in chunks:
        usage = ch.get("usage") or usage
        for choice in ch.get("choices", []):
            d = choice.get("delta", {})
            if d.get("content"):
                content.append(d["content"])
            for tc in d.get("tool_calls") or []:
                cur = calls.setdefault(tc.get("index", 0), {"id": "", "type": "function",
                                                            "function": {"name": "", "arguments": ""}})
                cur["id"] = tc.get("id") or cur["id"]
                fn = tc.get("function") or {}
                cur["function"]["name"] = fn.get("name") or cur["function"]["name"]
                cur["function"]["arguments"] += fn.get("arguments") or ""
            finish = choice.get("finish_reason") or finish
    msg: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
    if calls:
        msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    return {"choices": [{"message": msg, "finish_reason": finish}], "usage": usage}


# ---------------------------------------------------------------- Anthropic
def anthropic_request(body: dict, default_max_tokens: Optional[int] = None) -> dict:
    system, messages = [], []
    pending_results: list[dict] = []

    def flush() -> None:
        if pending_results:
            messages.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for m in body.get("messages", []):
        role = m.get("role")
        if role == "system":
            system.append(_text(m.get("content")))
        elif role == "tool":
            pending_results.append({"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""),
                                    "content": _text(m.get("content"))})
        elif role == "assistant":
            flush()
            blocks: list[dict] = []
            if _text(m.get("content")):
                blocks.append({"type": "text", "text": _text(m.get("content"))})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                blocks.append({"type": "tool_use", "id": tc.get("id", ""), "name": fn.get("name", ""),
                               "input": _loads(fn.get("arguments"))})
            if blocks:
                messages.append({"role": "assistant", "content": blocks})
        else:
            flush()
            messages.append({"role": "user", "content": _text(m.get("content"))})
    flush()
    req: dict[str, Any] = {"model": body["model"], "messages": messages, "stream": True,
                           "max_tokens": body.get("max_tokens") or default_max_tokens
                           or model_max_tokens(body["model"])}
    if system:
        req["system"] = "\n\n".join(s for s in system if s)
    if body.get("temperature") is not None:
        req["temperature"] = body["temperature"]
    tools = _tool_calls_of(body)
    if tools:
        req["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                        for t in tools]
        if body.get("tool_choice") == "none":
            req["tool_choice"] = {"type": "none"}
    return req


def anthropic_events(events: Iterator[tuple[str, str]], model: str = "") -> Iterator[dict]:
    tool_index: dict[int, int] = {}
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    stop: Optional[str] = None
    for event, data in events:
        if not data or data == "[DONE]":
            continue
        obj = json.loads(data)
        kind = obj.get("type") or event
        if kind == "message_start":
            u = (obj.get("message") or {}).get("usage") or {}
            usage["prompt_tokens"] = int(u.get("input_tokens") or 0)
        elif kind == "content_block_start":
            block = obj.get("content_block") or {}
            if block.get("type") == "tool_use":
                n = len(tool_index)
                tool_index[obj["index"]] = n
                yield _chunk({"tool_calls": [{"index": n, "id": block.get("id", ""), "type": "function",
                                              "function": {"name": block.get("name", ""), "arguments": ""}}]}, model=model)
        elif kind == "content_block_delta":
            d = obj.get("delta") or {}
            if d.get("type") == "text_delta":
                yield _chunk({"content": d.get("text", "")}, model=model)
            elif d.get("type") == "input_json_delta" and obj.get("index") in tool_index:
                yield _chunk({"tool_calls": [{"index": tool_index[obj["index"]],
                                              "function": {"arguments": d.get("partial_json", "")}}]}, model=model)
        elif kind == "message_delta":
            stop = (obj.get("delta") or {}).get("stop_reason") or stop
            usage["completion_tokens"] = int((obj.get("usage") or {}).get("output_tokens") or 0)
        elif kind == "error":
            raise ProviderError(f"anthropic: {(obj.get('error') or {}).get('message', 'stream error')}")
    finish = {"tool_use": "tool_calls", "max_tokens": "length"}.get(stop or "", "stop")
    yield _chunk({}, finish=finish, usage=usage, model=model)


def anthropic_chat(base_url: str, api_key: str, timeout: float = 600.0, default_max_tokens: Optional[int] = None):
    url = base_url.rstrip("/").removesuffix("/v1") + "/v1/messages"

    def chat(body: dict):
        resp = _open(url, {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION},
                     anthropic_request(body, default_max_tokens), timeout)
        stream = anthropic_events(_sse(resp), body.get("model", ""))
        return stream if body.get("stream") else aggregate(stream)

    return chat


# ------------------------------------------------------------------- Gemini
# JSON-Schema keywords Gemini's OpenAPI subset rejects. Dropped as KEYWORDS only, never as property names.
_DROP = {"additionalProperties", "$schema", "$id", "$ref", "definitions", "$defs", "default", "examples"}
_REF_DEPTH = 8  # a recursive $ref is inlined this deep, then becomes a plain object


def _resolve_ref(ref: str, root: dict) -> Any:
    """A local ``#/$defs/X`` or ``#/definitions/X`` reference, or None."""
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    node: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) else None


def _gemini_schema(node: Any, root: Any = None, depth: int = 0) -> Any:
    """JSON Schema -> Gemini schema: drop unsupported keywords (not property names), inline local ``$ref``,
    collapse ``["T", "null"]`` to ``T`` + ``nullable``, keep ``required`` to the properties that exist."""
    root = node if root is None else root
    if isinstance(node, list):
        return [_gemini_schema(v, root, depth) for v in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        target = _resolve_ref(node["$ref"], root) if depth < _REF_DEPTH else None
        merged = {**(target or {"type": "object"}), **{k: v for k, v in node.items() if k != "$ref"}}
        return _gemini_schema(merged, root, depth + 1)
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROP:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {name: _gemini_schema(sub, root, depth) for name, sub in value.items()}
        elif key == "type" and isinstance(value, list):
            kinds = [t for t in value if t != "null"]
            out[key] = kinds[0] if len(kinds) == 1 else (kinds or ["string"])[0]
            if "null" in value:
                out["nullable"] = True
        else:
            out[key] = _gemini_schema(value, root, depth)
    if isinstance(out.get("required"), list):
        props = out.get("properties") if isinstance(out.get("properties"), dict) else {}
        out["required"] = [r for r in out["required"] if r in props]
        if not out["required"]:
            del out["required"]
    return out


def gemini_request(body: dict, signatures: Optional[dict[str, Optional[str]]] = None) -> dict:
    """``signatures``: tool call id -> the ``thoughtSignature`` Gemini attached to that functionCall part (None when
    it had none). Gemini 3 rejects a follow-up turn whose functionCall part comes back without its signature. Ids
    Gemini issued itself (not ``call_*``) are echoed in functionCall / functionResponse."""
    signatures = signatures or {}

    def native(call_id: str) -> bool:
        return call_id in signatures and not call_id.startswith("call_")

    system, contents = [], []
    names: dict[str, str] = {}
    pending: list[dict] = []

    def flush() -> None:
        if pending:
            contents.append({"role": "user", "parts": list(pending)})
            pending.clear()

    for m in body.get("messages", []):
        role = m.get("role")
        if role == "system":
            system.append(_text(m.get("content")))
        elif role == "tool":
            call_id = m.get("tool_call_id", "")
            resp: dict[str, Any] = {"name": names.get(call_id, "tool"), "response": {"result": _text(m.get("content"))}}
            if native(call_id):
                resp["id"] = call_id
            pending.append({"functionResponse": resp})
        elif role == "assistant":
            flush()
            parts: list[dict] = []
            if _text(m.get("content")):
                parts.append({"text": _text(m.get("content"))})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                call_id = tc.get("id", "")
                names[call_id] = fn.get("name", "")
                fc: dict[str, Any] = {"name": fn.get("name", ""), "args": _loads(fn.get("arguments"))}
                if native(call_id):
                    fc["id"] = call_id
                part: dict[str, Any] = {"functionCall": fc}
                if signatures.get(call_id):
                    part["thoughtSignature"] = signatures[call_id]
                parts.append(part)
            if parts:
                contents.append({"role": "model", "parts": parts})
        else:
            flush()
            contents.append({"role": "user", "parts": [{"text": _text(m.get("content"))}]})
    flush()
    req: dict[str, Any] = {"contents": contents}
    if system:
        req["systemInstruction"] = {"parts": [{"text": "\n\n".join(s for s in system if s)}]}
    gen: dict[str, Any] = {}
    if body.get("temperature") is not None:
        gen["temperature"] = body["temperature"]
    if body.get("max_tokens"):
        gen["maxOutputTokens"] = body["max_tokens"]
    if gen:
        req["generationConfig"] = gen
    tools = _tool_calls_of(body)
    if tools:
        req["tools"] = [{"functionDeclarations": [
            {"name": t["name"], "description": t["description"], "parameters": _gemini_schema(t["parameters"])}
            for t in tools]}]
        if body.get("tool_choice") == "none":
            req["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}
    return req


def gemini_events(events: Iterator[tuple[str, str]], model: str = "",
                  signatures: Optional[dict[str, Optional[str]]] = None) -> Iterator[dict]:
    """``signatures`` (optional) collects tool call id -> thoughtSignature (or None) for the next request."""
    n_calls, finish, usage = 0, None, None
    for _event, data in events:
        if not data or data == "[DONE]":
            continue
        obj = json.loads(data)
        if obj.get("error"):
            raise ProviderError(f"gemini: {obj['error'].get('message', 'stream error')}")
        for cand in obj.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                if part.get("text") and not part.get("thought"):
                    yield _chunk({"content": part["text"]}, model=model)
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    # Gemini's own id when it sends one, else unique across turns (the loop keys results on it)
                    call_id = str(fc.get("id") or f"call_{uuid.uuid4().hex[:12]}")
                    if signatures is not None:
                        signatures[call_id] = part.get("thoughtSignature")
                    yield _chunk({"tool_calls": [{"index": n_calls, "id": call_id, "type": "function",
                                                  "function": {"name": fc.get("name", ""),
                                                               "arguments": json.dumps(fc.get("args") or {})}}]},
                                 model=model)
                    n_calls += 1
            finish = cand.get("finishReason") or finish
        meta = obj.get("usageMetadata")
        if meta:
            usage = {"prompt_tokens": int(meta.get("promptTokenCount") or 0),
                     "completion_tokens": int(meta.get("candidatesTokenCount") or 0)}
    reason = "tool_calls" if n_calls else ("length" if finish == "MAX_TOKENS" else "stop")
    yield _chunk({}, finish=reason, usage=usage, model=model)


def gemini_chat(base_url: str, api_key: str, timeout: float = 600.0):
    root = base_url.rstrip("/")
    signatures: dict[str, Optional[str]] = {}  # per client: thoughtSignature of each functionCall it returned

    def chat(body: dict):
        model = body.get("model", "")
        name = quote(model.removeprefix("models/"), safe="-._")  # a model name never adds path or query parts
        url = f"{root}/v1beta/models/{name}:streamGenerateContent?alt=sse"
        resp = _open(url, {"x-goog-api-key": api_key}, gemini_request(body, signatures), timeout)
        stream = gemini_events(_sse(resp), model, signatures)
        return stream if body.get("stream") else aggregate(stream)

    return chat


def make_llm(config: Any) -> Any:
    """None for ``openai`` (the engine builds its own client); a ``GatewayLLM`` for anthropic / gemini.

    Works for every config source (environment, ``--config``, ``--env-file``): the provider name is normalised
    (``Anthropic`` = ``anthropic``, ``auto`` follows the base URL), an unknown one is a ValueError, and a missing
    base URL, key or model is filled like ``envconfig.resolve_endpoint`` does, or reported as a ValueError.
    """
    from ai_runtime_rex.runtime.envconfig import KEY_ENV, PUBLIC_MODEL, PUBLIC_URL, detect_provider

    lite = config.get("liteLLM", {}) or {}
    if not isinstance(lite, dict):
        raise ValueError("liteLLM settings must be a mapping")
    raw = str(lite.get("provider") or "openai").strip().lower()
    kind = detect_provider(raw, lite.get("base_url")) if raw == "auto" else raw
    if kind == "openai":
        return None
    if kind not in ("anthropic", "gemini"):
        raise ValueError(f"unknown provider {lite.get('provider')!r} (use auto, openai, anthropic or gemini)")
    from ai_runtime_rex.runtime.embed import GatewayLLM

    base_url = lite.get("base_url") or PUBLIC_URL[kind]
    api_key = (lite.get("api_key") or os.environ.get("AI_REX_API_KEY") or os.environ.get(KEY_ENV[kind])
               or os.environ.get("LITELLM_API_KEY"))
    if not api_key or api_key == "none":
        raise ValueError(f"provider {kind} needs an API key: set {KEY_ENV[kind]} (or use --api-key-env NAME)")
    model = lite.get("model") or PUBLIC_MODEL[kind]
    try:
        timeout = float(lite.get("timeout") or 600.0)
        raw_max = lite.get("max_tokens") or os.environ.get("AI_REX_MAX_TOKENS")
        max_tokens = int(raw_max) if raw_max not in (None, "") else None
    except (TypeError, ValueError):
        raise ValueError("liteLLM.timeout and max_tokens (AI_REX_MAX_TOKENS) must be numbers") from None
    if kind == "anthropic":
        chat = anthropic_chat(base_url, api_key, timeout, default_max_tokens=max_tokens)
    else:
        chat = gemini_chat(base_url, api_key, timeout)
    return GatewayLLM(chat, model)
