# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Git tool using guard client."""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolSchema
from ..guard import get_guard_client
from ..config import Config


class GitTool(Tool):
    """Git operations tool (via guard)."""

    def __init__(self, config: Config | None = None) -> None:
        schema = ToolSchema(
            name="git",
            description="Git operations: status, diff, add, commit, log, branch, checkout",
            parameters={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["status", "diff", "add", "commit", "log", "branch", "checkout"],
                        "description": "Git operation to perform",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory",
                        "default": ".",
                    },
                    "message": {
                        "type": "string",
                        "description": "Commit message (for commit operation)",
                    },
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Files to stage (for add) or diff",
                    },
                    "staged": {
                        "type": "boolean",
                        "description": "Show staged diff (for diff operation)",
                        "default": False,
                    },
                    "max_count": {
                        "type": "integer",
                        "description": "Max commits to show (for log operation)",
                        "default": 10,
                    },
                    "oneline": {
                        "type": "boolean",
                        "description": "Use oneline format (for log operation)",
                        "default": True,
                    },
                    "all_branches": {
                        "type": "boolean",
                        "description": "Show all branches (for branch operation)",
                        "default": False,
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch name (for checkout operation)",
                    },
                    "create": {
                        "type": "boolean",
                        "description": "Create new branch (for checkout operation)",
                        "default": False,
                    },
                    "author": {
                        "type": "string",
                        "description": "Author for commit (optional)",
                    },
                },
                "required": ["operation"],
            },
        )
        super().__init__("git", "Git operations via guard", schema)
        self._config = config
        self._guard = None

    def _get_guard(self):
        if self._guard is None:
            self._guard = get_guard_client(self._config)
        return self._guard

    def execute(self, **kwargs: Any) -> Any:
        operation = kwargs.pop("operation")
        guard = self._get_guard()

        if operation == "status":
            cwd = kwargs.pop("cwd", ".")
            return guard.git_status(cwd)

        elif operation == "diff":
            cwd = kwargs.pop("cwd", ".")
            staged = kwargs.pop("staged", False)
            files = kwargs.pop("files", None)
            return guard.git_diff(cwd, staged, files)

        elif operation == "add":
            files = kwargs.pop("files", [])
            cwd = kwargs.pop("cwd", ".")
            return guard.git_add(files, cwd)

        elif operation == "commit":
            message = kwargs.pop("message", "")
            cwd = kwargs.pop("cwd", ".")
            author = kwargs.pop("author", None)
            return guard.git_commit(message, cwd, author)

        elif operation == "log":
            cwd = kwargs.pop("cwd", ".")
            max_count = kwargs.pop("max_count", 10)
            oneline = kwargs.pop("oneline", True)
            return guard.git_log(cwd, max_count, oneline)

        elif operation == "branch":
            cwd = kwargs.pop("cwd", ".")
            all_branches = kwargs.pop("all_branches", False)
            return guard.git_branch(cwd, all_branches)

        elif operation == "checkout":
            branch = kwargs.pop("branch", "")
            create = kwargs.pop("create", False)
            cwd = kwargs.pop("cwd", ".")
            return guard.git_checkout(branch, create, cwd)

        else:
            raise ValueError(f"Unknown git operation: {operation}")


def create_git_tool(config: Config | None = None) -> GitTool:
    return GitTool(config)