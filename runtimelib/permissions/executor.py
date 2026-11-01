# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Permission-checking tool executor wrapper."""

from __future__ import annotations

from typing import Any

from ..tools import Tool, ToolExecutor, ToolResult
from . import PermissionPolicy, PermissionResult, PermissionAskError, PermissionDeniedError


class PermissionCheckingExecutor(ToolExecutor):
    """ToolExecutor wrapper that enforces PermissionPolicy before execution."""

    def __init__(
        self,
        policy: PermissionPolicy,
        inner: ToolExecutor | None = None,
        on_ask: Any | None = None,
    ) -> None:
        super().__init__()
        self.policy = policy
        self.inner = inner or ToolExecutor()
        self.on_ask = on_ask  # Callback when permission is ASK

    def _extract_operation(self, tool_name: str, args: dict[str, Any]) -> str:
        """Extract operation string from tool arguments."""
        if tool_name == "bash":
            # For bash, operation is the command
            return args.get("command", "")
        elif tool_name == "fileops":
            # For fileops, operation is the operation type
            return args.get("operation", "")
        elif tool_name == "git":
            # For git, operation is the git operation
            return args.get("operation", "")
        elif tool_name == "websearch":
            return "search"
        elif tool_name == "task":
            return "spawn"
        return ""

    def execute(
        self,
        tool: Tool,
        args: dict[str, Any],
        timeout: float | None = None,
        on_start: Any | None = None,
        on_end: Any | None = None,
    ) -> ToolResult:
        """Execute tool with permission check."""

        # Extract operation from tool-specific args
        operation = self._extract_operation(tool.name, args)

        # Check permission
        role = args.get("_role", "CODER")
        args_with_role = {**args, "_role": role}

        result = self.policy.check(tool.name, operation, args_with_role)

        if result == PermissionResult.DENY:
            reason = "Permission denied by policy"
            return ToolResult(
                tool_name=tool.name,
                success=False,
                error=f"Permission denied: {tool.name}:{operation} - {reason}"
            )

        if result == PermissionResult.ASK:
            # Notify asker callback if provided
            if self.on_ask:
                self.on_ask(tool.name, operation, args_with_role)
            # Raise exception for caller to handle
            raise PermissionAskError(tool.name, operation, "Requires approval")

        # ALLOW - execute with inner executor
        return self.inner.execute(tool, args, timeout=timeout, on_start=on_start, on_end=on_end)


def create_permission_executor(
    policy: PermissionPolicy,
    executor: ToolExecutor | None = None,
    on_ask: Any | None = None,
) -> PermissionCheckingExecutor:
    """Factory function to create permission-checking executor."""
    return PermissionCheckingExecutor(policy, executor, on_ask)