# SPDX-License-Identifier: MIT
"""Configuration sources."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from ..kikolib import yaml_lite, dotenv


class ConfigSource:
    """Base class for configuration sources."""

    def load(self) -> dict[str, Any]:
        raise NotImplementedError

    def save(self, data: dict[str, Any]) -> None:
        raise NotImplementedError


class FileSource(ConfigSource):
    """YAML file configuration source."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            return yaml_lite.load_file(self.path)
        return {}

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        yaml_lite.save(self.path, data)


class EnvSource(ConfigSource):
    """Environment variable configuration source (.env file)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            text = self.path.read_text(encoding="utf-8-sig")
            return dotenv.parse(text)
        return {}

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for key, value in data.items():
            if isinstance(value, str) and (" " in value or "\n" in value):
                lines.append(f'{key}="{value}"')
            else:
                lines.append(f"{key}={value}")
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class CLISource(ConfigSource):
    """Command-line argument configuration source."""

    def __init__(self, args: dict[str, Any]) -> None:
        self.args = args

    def load(self) -> dict[str, Any]:
        # Convert flat CLI args to nested dict
        result = {}
        for key, value in self.args.items():
            if key.startswith("_"):
                continue  # Skip positional args
            parts = key.split(".")
            ConfigSource._set_nested(result, parts, value)
        return result

    def save(self, data: dict[str, Any]) -> None:
        raise NotImplementedError("CLI source is read-only")

    @staticmethod
    def _set_nested(data: dict[str, Any], parts: list[str], value: Any) -> None:
        current = data
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value


class EnvVarSource(ConfigSource):
    """RUNTIME_* environment variable source."""

    PREFIX = "RUNTIME_"

    def load(self) -> dict[str, Any]:
        result = {}
        for key, value in os.environ.items():
            if key.startswith(self.PREFIX):
                config_key = key[len(self.PREFIX):]
                parts = self._parse_env_key(config_key)
                self._set_nested(result, parts, value)
        return result

    def save(self, data: dict[str, Any]) -> None:
        raise NotImplementedError("Env var source is read-only")

    @staticmethod
    def _parse_env_key(key: str) -> list[str]:
        if "__" in key:
            parts = key.split("__")
            known_prefixes = {
                "LITELLM": "liteLLM",
                "DOCKER_GUARD": "docker_guard",
            }
            first = parts[0]
            if first in known_prefixes:
                first = known_prefixes[first]
            rest = [p.lower() for p in parts[1:]]
            return [first] + rest

        known_prefixes = {
            "LITELLM": "liteLLM",
            "DOCKER_GUARD": "docker_guard",
        }

        for prefix, mapped in known_prefixes.items():
            if key.startswith(prefix + "_"):
                rest = key[len(prefix) + 1:]
                return [mapped, rest.lower()]

        return key.lower().split("_")

    @staticmethod
    def _set_nested(data: dict[str, Any], parts: list[str], value: str) -> None:
        current = data
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        try:
            import json
            current[parts[-1]] = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            current[parts[-1]] = value


def merge_sources(sources: list[ConfigSource], priority: Optional[list[int]] = None) -> dict[str, Any]:
    """Merge multiple config sources. Later sources override earlier ones."""
    result = {}
    for i, source in enumerate(sources):
        data = source.load()
        if priority and i < len(priority):
            # Apply priority weights if needed
            pass
        result = deep_merge(result, data)
    return result


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dicts. Override takes precedence."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result