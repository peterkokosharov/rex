# SPDX-License-Identifier: MIT
"""Configuration system using kikolib.yaml_lite and kikolib.dotenv."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Optional

from .kikolib import yaml_lite, dotenv


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "runtime" / "config.yaml"
DEFAULT_ENV_PATH = Path.home() / ".config" / "runtime" / ".env"


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
        """Apply RUNTIME_* environment variable overrides."""
        prefix = "RUNTIME_"
        result = dict(data)

        for key, value in os.environ.items():
            if key.startswith(prefix):
                config_key = key[len(prefix):]
                # Convert nested keys:
                # RUNTIME_LITELLM__BASE_URL -> liteLLM.base_url (double underscore = nested)
                # RUNTIME_LITELLM_BASE_URL -> liteLLM.BASE_URL (special case for known prefixes)
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

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    def save(self, path: Optional[Path] = None) -> None:
        """Save config to YAML file."""
        path = path or DEFAULT_CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        yaml_lite.save(path, self._data)


def get_config() -> Config:
    """Global config instance (lazy loaded)."""
    global _config
    if _config is None:
        _config = Config.load()
    return _config


_config: Optional[Config] = None