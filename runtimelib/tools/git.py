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
        if self._config and self._config.get("cli.unsafe_local_tools"):
            return self._exec_local(operation, **kwargs)
        guard = self._get_guard()

        if operation == "status":
            return guard.git_status(kwargs.pop("cwd", "."))
        elif operation == "diff":
            return guard.git_diff(kwargs.pop("cwd", "."), kwargs.pop("staged", False), kwargs.pop("files", None))
        elif operation == "add":
            return guard.git_add(kwargs.pop("files", []), kwargs.pop("cwd", "."))
        elif operation == "commit":
            return guard.git_commit(kwargs.pop("message", ""), kwargs.pop("cwd", "."), kwargs.pop("author", None))
        elif operation == "log":
            return guard.git_log(kwargs.pop("cwd", "."), kwargs.pop("max_count", 10), kwargs.pop("oneline", True))
        elif operation == "branch":
            return guard.git_branch(kwargs.pop("cwd", "."), kwargs.pop("all_branches", False))
        elif operation == "checkout":
            return guard.git_checkout(kwargs.pop("branch", ""), kwargs.pop("create", False), kwargs.pop("cwd", "."))
        else:
            raise ValueError(f"Unknown git operation: {operation}")

    def _exec_local(self, operation: str, **kwargs: Any) -> Any:
        import subprocess
        cwd = kwargs.pop("cwd", ".")

        def run(args: list[str]) -> dict[str, Any]:
            p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
            return {"exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}

        if operation == "status":
            return run(["git", "status", "--porcelain=v1", "-b"])
        if operation == "diff":
            args = ["git", "diff"]
            if kwargs.get("staged"):
                args.append("--staged")
            files = kwargs.get("files")
            if files:
                args += list(files)
            return run(args)
        if operation == "add":
            files = kwargs.get("files") or []
            return run(["git", "add", *files])
        if operation == "commit":
            args = ["git", "commit", "-m", kwargs.get("message", "")]
            author = kwargs.get("author")
            if author:
                args += ["--author", author]
            return run(args)
        if operation == "log":
            args = ["git", "log", f"-n{kwargs.get('max_count', 10)}"]
            if kwargs.get("oneline", True):
                args.append("--oneline")
            return run(args)
        if operation == "branch":
            args = ["git", "branch"]
            if kwargs.get("all_branches"):
                args.append("-a")
            return run(args)
        if operation == "checkout":
            branch = kwargs.get("branch", "")
            args = ["git", "checkout"]
            if kwargs.get("create"):
                args.append("-b")
            args.append(branch)
            return run(args)
        raise ValueError(f"Unknown git operation: {operation}")


def create_git_tool(config: Config | None = None) -> GitTool:
    return GitTool(config)