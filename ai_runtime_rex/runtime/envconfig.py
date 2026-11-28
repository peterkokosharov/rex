# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License
"""Environment-only configuration of the ``ai-rex`` command and of the embeddable ``Agent``.

No file is read or written. Precedence: flag/argument > environment > default. Variables (all optional):

- ``AI_REX_MODEL``     model name, default ``sonnet`` (``claude-sonnet-5-5`` / ``gemini-3.8-flash`` for the native providers)
- ``AI_REX_PROVIDER``  ``auto`` (default) | ``openai`` | ``anthropic`` | ``gemini``
- ``AI_REX_BASE_URL``  gateway base URL (``LITELLM_BASE_URL`` is the fallback); wins over the port
- ``AI_REX_PORT``      port of the local gateway, default ``4000``
- ``AI_REX_API_KEY``   API key; else the provider's own variable (``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``,
  ``GEMINI_API_KEY``), else ``LITELLM_API_KEY``. ``--api-key-env NAME`` names another variable. Local kiko needs none.
- ``AI_REX_TIMEOUT``   HTTP timeout in seconds, default 60
- ``AI_REX_<SECTION>__<KEY>`` any other engine setting, e.g. ``AI_REX_AGENT__MAX_STEPS=30``

Keys are only ever read from the environment; nothing here prints or logs them.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Optional
from urllib.parse import urlparse

from runtimelib.config import Config

DEFAULT_MODEL = "sonnet"
DEFAULT_PORT = 4000
NO_KEY = "none"  # the HTTP client insists on a key; local kiko ignores it
PROVIDERS = ("auto", "openai", "anthropic", "gemini")
PUBLIC_URL = {"anthropic": "https://api.anthropic.com", "gemini": "https://generativelanguage.googleapis.com"}
PUBLIC_MODEL = {"anthropic": "claude-sonnet-5-5", "gemini": "gemini-3.8-flash"}
KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}


def detect_provider(provider: Optional[str], base_url: Optional[str]) -> str:
    """auto = OpenAI-compatible unless the base URL clearly names Anthropic or Google."""
    if provider and provider != "auto":
        return provider
    host = (urlparse(base_url or "").hostname or "").lower()
    if host.endswith("anthropic.com"):
        return "anthropic"
    if host.endswith("googleapis.com"):
        return "gemini"
    return "openai"


def resolve_endpoint(model: Optional[str] = None, port: Optional[int] = None, base_url: Optional[str] = None,
                     api_key: Optional[str] = None, provider: Optional[str] = None,
                     api_key_env: Optional[str] = None, timeout: Optional[float] = None,
                     environ: Optional[Mapping[str, str]] = None) -> dict:
    env = os.environ if environ is None else environ
    provider = provider or env.get("AI_REX_PROVIDER") or "auto"
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r} (use auto, openai, anthropic or gemini)")
    if base_url is None and port is None:
        base_url = env.get("AI_REX_BASE_URL") or env.get("LITELLM_BASE_URL")
    kind = detect_provider(provider, base_url)
    if kind in PUBLIC_URL and base_url is None and port is None:
        base_url = PUBLIC_URL[kind]
    if port is None:
        raw = env.get("AI_REX_PORT")
        port = int(raw) if raw and raw.isdigit() else DEFAULT_PORT
    named = None
    if api_key_env:
        named = env.get(api_key_env)
        if not named:
            raise ValueError(f"environment variable {api_key_env} is not set")
    key = api_key or named or env.get("AI_REX_API_KEY") or env.get(KEY_ENV.get(kind, "")) or env.get("LITELLM_API_KEY")
    if kind != "openai" and not key:
        raise ValueError(f"provider {kind} needs an API key: set {KEY_ENV[kind]} (or use --api-key-env NAME)")
    raw_timeout = timeout if timeout is not None else env.get("AI_REX_TIMEOUT")
    out = {
        "model": model or env.get("AI_REX_MODEL") or PUBLIC_MODEL.get(kind, DEFAULT_MODEL),
        "base_url": base_url or f"http://127.0.0.1:{port}",
        "api_key": key or NO_KEY,
        "provider": kind,
    }
    if raw_timeout:
        out["timeout"] = float(raw_timeout)
    return out


def env_config(model: Optional[str] = None, port: Optional[int] = None, base_url: Optional[str] = None,
               api_key: Optional[str] = None, provider: Optional[str] = None,
               api_key_env: Optional[str] = None, timeout: Optional[float] = None) -> Config:
    """A ``Config`` from arguments, the process environment and defaults only (no config file, no .env)."""
    data: dict = Config._apply_env_overrides({})
    lite = data.setdefault("liteLLM", {})
    explicit = {"model": model, "base_url": base_url or port, "api_key": api_key or api_key_env,
                "provider": provider, "timeout": timeout}
    for key, value in resolve_endpoint(model, port, base_url, api_key, provider, api_key_env, timeout).items():
        if explicit.get(key) or key not in lite:
            lite[key] = value
    return Config(data)
