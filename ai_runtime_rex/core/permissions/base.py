# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Base permission types - shared to avoid circular imports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PermissionResult(Enum):
    """Result of a permission check."""
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PermissionRule:
    """A single permission rule."""
    tool: str                    # Tool name (e.g., "bash", "fileops", "git")
    operation: str | None = None # Specific operation (e.g., "bash:pytest", "git:commit")
    pattern: str | None = None   # Glob pattern for args (e.g., "pytest *", "commit -m *")
    result: PermissionResult = PermissionResult.ALLOW
    reason: str = ""

    def matches(self, tool: str, operation: str, args: dict[str, Any]) -> bool:
        """Check if this rule matches the given tool call."""
        if self.tool != tool:
            return False
        if self.operation:
            # Support prefix matching: "commit *" matches "commit" and "commit -m test"
            import fnmatch
            if self.operation.endswith("*"):
                prefix = self.operation[:-1]  # Remove trailing *
                # Match exact prefix OR prefix + space + anything
                if not (operation == prefix or operation.startswith(prefix + " ")):
                    return False
            elif not fnmatch.fnmatch(operation, self.operation):
                return False
        if self.pattern:
            # Check if any arg value matches the pattern
            import fnmatch
            for val in args.values():
                if isinstance(val, str) and fnmatch.fnmatch(val, self.pattern):
                    return True
            return False
        return True


class PermissionPolicy(ABC):
    """Abstract base class for permission policies."""

    @abstractmethod
    def check(self, tool: str, operation: str, args: dict[str, Any]) -> PermissionResult:
        """Check if a tool call is allowed.

        Args:
            tool: Tool name (e.g., "bash", "fileops")
            operation: Specific operation (e.g., "pytest", "commit")
            args: Tool arguments

        Returns:
            PermissionResult.ALLOW/DENY/ASK
        """
        pass

    @abstractmethod
    def get_allowed_tools(self, role: str) -> list[str]:
        """Get list of allowed tools for a role."""
        pass


class PermissionDeniedError(Exception):
    """Raised when permission is denied."""
    def __init__(self, tool: str, operation: str, reason: str):
        self.tool = tool
        self.operation = operation
        self.reason = reason
        super().__init__(f"Permission denied for {tool}:{operation} - {reason}")


class PermissionAskError(Exception):
    """Raised when permission requires user/orchestrator approval."""
    def __init__(self, tool: str, operation: str, reason: str):
        self.tool = tool
        self.operation = operation
        self.reason = reason
        super().__init__(f"Permission requires approval for {tool}:{operation} - {reason}")