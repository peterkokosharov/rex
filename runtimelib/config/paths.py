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
_quiet = False  # set by the entry point for --jsonl / acp: the streams belong to the protocol


def set_quiet(value: bool) -> None:
    global _quiet
    _quiet = value


def config_dir(home: Path | None = None) -> Path:
    """``~/.config/ai-rex``."""
    return (home or Path.home()) / ".config" / APP_NAME


def _has_files(path: Path) -> bool:
    return path.is_dir() and any(p.is_file() for p in path.rglob("*"))


def migrate_legacy_config(home: Path | None = None) -> bool:
    """Copy the old config dir to the new one when the new one is missing or holds no files.

    One-time and non-destructive: the old directory is never deleted. The copy goes to a temporary sibling
    (``.ai-rex.tmp-<pid>``) and is moved into place with ``os.replace``, so a crash never leaves a half-copied
    ``ai-rex`` directory. Returns True when the new directory was created by this call.
    """
    new = config_dir(home)
    if _has_files(new):
        return False
    base = (home or Path.home()) / ".config"
    for old_name in LEGACY_CONFIG_DIR_NAMES:
        old = base / old_name
        if not old.is_dir():
            continue
        tmp = base / f".{APP_NAME}.tmp-{os.getpid()}"
        try:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(old, tmp)
            if new.is_dir():
                shutil.rmtree(new)  # holds no files (checked above): only empty directories are removed
            os.replace(tmp, new)
        except OSError as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            if _has_files(new):  # somebody else created it meanwhile: keep theirs
                return False
            print(f"ai-rex: could not copy {old} to {new}: {exc}", file=sys.stderr)
            return False
        print(f"ai-rex: copied the old config directory {old} to {new} (the old one was kept).",
              file=sys.stderr)
        return True
    return False


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
    if _quiet or old in _warned:
        return
    _warned.add(old)
    print(f"ai-rex: environment variable {old} is deprecated, use {new}.", file=sys.stderr)
