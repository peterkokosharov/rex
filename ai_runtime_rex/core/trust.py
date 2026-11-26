# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""One trust switch for repository content (project skills and project AGENTS.md)."""

from __future__ import annotations

import sys
from typing import Any

_TRUE = ("1", "true", "yes", "on")
_WARNED_ALIAS = False


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in _TRUE
    return value is True


def trust_workspace(config: Any) -> bool:
    """True only when ``workspace.trust`` is true (default false).

    The old key ``skills.trust_workspace`` is a deprecated alias: true means trusted, with a one-time warning.
    """
    global _WARNED_ALIAS
    if config is None:
        return False
    if _truthy(config.get("workspace.trust")):
        return True
    if _truthy(config.get("skills.trust_workspace")):
        if not _WARNED_ALIAS:
            _WARNED_ALIAS = True
            print("WARNING: skills.trust_workspace is deprecated; use workspace.trust", file=sys.stderr)
        return True
    return False
