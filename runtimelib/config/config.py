# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-opus-5-5
# Licensed under MIT License

"""Configuration system using kikolib.yaml_lite and kikolib.dotenv."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping, Optional

from kikolib import yaml_lite, dotenv

from . import paths


DEFAULT_CONFIG_PATH = paths.config_dir() / "config.yaml"
DEFAULT_ENV_PATH = paths.config_dir() / ".env"


class Config:
    """Runtime configuration with YAML + env override."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @classmethod
    def load(
        cls,
        config_path: Optional[Path] = None,
        env_path: Optional[Path] = None,
    ) -> "Config":
        """Load config from YAML file and .env file."""
        config_path = config_path or DEFAULT_CONFIG_PATH
        env_path = env_path or DEFAULT_ENV_PATH

        # Load .env first (lower priority)
        env_data = {}
        if env_path.exists():
            text = env_path.read_text(encoding="utf-8-sig")
            env_data = dotenv.parse(text)

        # Load YAML config
        yaml_data = {}
        if config_path.exists():
            yaml_data = yaml_lite.load_file(config_path)

        # Merge: .env overrides YAML
        merged = {**yaml_data, **env_data}

        # Apply env var overrides (highest priority)
        merged = Config._apply_env_overrides(merged)

        return cls(merged)

    @staticmethod
    def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
        """Apply AI_REX_* (legacy RUNTIME_*) environment variable overrides."""
        result = dict(data)

        for config_key, value in paths.env_overrides():
            parts = Config._parse_env_key(config_key)
            Config._set_nested(result, parts, value)

        return result

    @staticmethod
    def _parse_env_key(key: str) -> list[str]:
        """Parse environment variable key into nested config parts.

        Supports:
        - Double underscore for nesting: LITELLM__BASE_URL -> ['liteLLM', 'base_url']
        - Known prefixes with single underscore: LITELLM_BASE_URL -> ['liteLLM', 'base_url']
        """
        # Try double underscore first
        if "__" in key:
            parts = key.split("__")
            # First part: map known prefixes
            known_prefixes = {
                "LITELLM": "liteLLM",
                "DOCKER_GUARD": "docker_guard",
            }
            first = parts[0]
            if first in known_prefixes:
                first = known_prefixes[first]
            # Rest of parts: lowercase to match YAML conventions
            rest = [p.lower() for p in parts[1:]]
            return [first] + rest

        # Known prefixes that should preserve case
        known_prefixes = {
            "LITELLM": "liteLLM",
            "DOCKER_GUARD": "docker_guard",
        }

        for prefix, mapped in known_prefixes.items():
            if key.startswith(prefix + "_"):
                rest = key[len(prefix) + 1:]
                # Preserve rest as single key but lowercase to match YAML
                return [mapped, rest.lower()]

        # Fallback: all lowercase, split by underscore
        return key.lower().split("_")

    @staticmethod
    def _set_nested(data: dict[str, Any], parts: list[str], value: str) -> None:
        """Set nested dict value from parts."""
        current = data
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        # Try to parse as JSON for complex types
        try:
            import json
            current[parts[-1]] = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            current[parts[-1]] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value with dot notation (e.g., 'liteLLM.base_url')."""
        parts = key.split(".")
        current = self._data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def set(self, key: str, value: Any) -> None:
        """Set config value with dot notation."""
        parts = key.split(".")
        current = self._data
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    def to_safe_dict(self) -> dict[str, Any]:
        """Deep copy for display with secret values masked."""
        return mask_secrets(self._data)

    def save(self, path: Optional[Path] = None) -> None:
        """YAML config is read-only (kikolib yaml_lite has no writer)."""
        raise NotImplementedError("YAML config is read-only")


SECRET_WORDS = {
    "key", "apikey", "token", "secret", "password", "passwd", "credential", "credentials",
    "secrets", "passwords", "authorization", "auth", "pat", "cookie", "cookies",
}
MASK = "***"

# scheme://user:pass@host or scheme://token@host -> scheme://***@host
_URL_USERINFO_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@")


def is_secret_key(name: str) -> bool:
    """True for config keys whose values must never be printed.

    ``api_key``, ``apiKey``, ``auth_token``, ``client_secret``, ``github_pat``,
    ``Authorization`` and ``session_cookie`` match; ``max_tokens``,
    ``keep_recent`` and ``auth_type`` do not.
    """
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name)).lower().replace("-", "_")
    words = [w for w in snake.split("_") if w]
    if not words:
        return False
    return words[-1] in SECRET_WORDS or "secret" in words or "password" in words


def mask_url_credentials(text: str) -> str:
    """Hide the user:pass (or token) part of URLs inside ``text``."""
    return _URL_USERINFO_RE.sub(lambda m: m.group(1) + MASK + "@", text)


def _mask_all(value: Any) -> Any:
    """Everything under a secret key: every non-empty scalar becomes MASK."""
    if isinstance(value, dict):
        return {k: _mask_all(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_all(v) for v in value]
    return MASK if value not in (None, "") else value


def mask_value(key: str, value: Any) -> Any:
    """Mask ``value`` when ``key`` (dot path or leaf name) names a secret."""
    leaf = str(key).rsplit(".", 1)[-1]
    if is_secret_key(leaf):
        return _mask_all(value)
    if isinstance(value, (dict, list)):
        return mask_secrets(value)
    if isinstance(value, str):
        return mask_url_credentials(value)
    return value


def mask_secrets(data: Any) -> Any:
    """Return a deep copy of ``data`` with secret-looking values masked."""
    if isinstance(data, dict):
        return {k: mask_value(k, v) for k, v in data.items()}
    if isinstance(data, list):
        return [mask_secrets(v) for v in data]
    if isinstance(data, str):
        return mask_url_credentials(data)
    return data


def get_config() -> Config:
    """Global config instance (lazy loaded)."""
    global _config
    if _config is None:
        _config = Config.load()
    return _config


_config: Optional[Config] = None