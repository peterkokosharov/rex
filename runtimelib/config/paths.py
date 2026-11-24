# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# Licensed under MIT License
"""Names of ai-rex config/data directories and environment variables, plus the legacy fallbacks.

The user-facing name is ``ai-rex`` (config dir ``~/.config/ai-rex``, env prefix ``AI_REX_``).
The old names (``~/.config/runtime``, ``RUNTIME_*``, ``REX_*``) are still read, never deleted.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "ai-rex"
ENV_PREFIX = "AI_REX_"
LEGACY_ENV_PREFIXES = ("RUNTIME_", "REX_")
LEGACY_CONFIG_DIR_NAMES = ("runtime",)

_warned: set[str] = set()


def config_dir(home: Path | None = None) -> Path:
    """``~/.config/ai-rex``."""
    return (home or Path.home()) / ".config" / APP_NAME


def migrate_legacy_config(home: Path | None = None) -> bool:
    """Copy the old config dir to the new one when the new one does not exist yet.

    One-time and non-destructive: the old directory is left in place. Returns True when a copy was made.
    """
    new = config_dir(home)
    if new.exists():
        return False
    base = (home or Path.home()) / ".config"
    for old_name in LEGACY_CONFIG_DIR_NAMES:
        old = base / old_name
        if old.is_dir():
            try:
                shutil.copytree(old, new)
            except OSError as exc:
                print(f"ai-rex: could not copy {old} to {new}: {exc}", file=sys.stderr)
                return False
            print(f"ai-rex: copied the old config directory {old} to {new} (the old one was kept).",
                  file=sys.stderr)
            return True
    return False


def getenv(name: str, default: str | None = None) -> str | None:
    """Value of ``AI_REX_<name>``; falls back to ``RUNTIME_<name>`` / ``REX_<name>`` (deprecated)."""
    value = os.environ.get(ENV_PREFIX + name)
    if value is not None:
        return value
    for prefix in LEGACY_ENV_PREFIXES:
        value = os.environ.get(prefix + name)
        if value is not None:
            _deprecation(prefix + name, ENV_PREFIX + name)
            return value
    return default


def env_overrides() -> list[tuple[str, str]]:
    """All (suffix, value) pairs from ``AI_REX_*``; legacy ``RUNTIME_*`` fill the gaps, the new name wins."""
    found: dict[str, str] = {}
    for prefix in LEGACY_ENV_PREFIXES[:1]:  # only RUNTIME_ ever carried config keys
        for key, value in os.environ.items():
            if key.startswith(prefix):
                _deprecation(key, ENV_PREFIX + key[len(prefix):])
                found[key[len(prefix):]] = value
    for key, value in os.environ.items():
        if key.startswith(ENV_PREFIX):
            found[key[len(ENV_PREFIX):]] = value
    return list(found.items())


def _deprecation(old: str, new: str) -> None:
    if old in _warned:
        return
    _warned.add(old)
    print(f"ai-rex: environment variable {old} is deprecated, use {new}.", file=sys.stderr)
