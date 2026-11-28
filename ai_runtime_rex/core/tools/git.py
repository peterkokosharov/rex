# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-opus-5-5
# Licensed under MIT License

"""Git tool using guard client."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Tool, ToolSchema
from ..guard import get_guard_client
from ..config import Config


class GitTool(Tool):
    """Git operations tool (via guard)."""

    def __init__(self, config: Config | None = None) -> None:
        schema = ToolSchema(
            name="git",
            description=(
                "Repository state and history: status, diff (files, staged), log (max_count, oneline), branch.\n"
                "Use log and diff to learn what changed recently and why.\n"
                "add, commit and checkout change the repository: only when the user asked for it.\n"
                'Example: {"operation": "log", "max_count": 15, "oneline": true}'
            ),
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
        from .fileops import resolve_in_workspace
        root = Path.cwd().resolve()
        cwd = resolve_in_workspace(kwargs.pop("cwd", ".") or ".", root)

        def run(args: list[str]) -> dict[str, Any]:
            p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
            return {"exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}

        if operation == "status":
            return run(["git", "status", "--porcelain=v1", "-b"])
        if operation == "diff":
            args = ["git", "diff"]
            if kwargs.get("staged"):
                args.append("--staged")
            files = _safe_files(kwargs.get("files"))
            if files:
                args += ["--", *files]
            return run(args)
        if operation == "add":
            files = _safe_files(kwargs.get("files"))
            if not files:
                raise ValueError("git add needs at least one file")
            return run(["git", "add", "--", *files])
        if operation == "commit":
            args = ["git", "commit", "-m", kwargs.get("message", "")]
            author = kwargs.get("author")
            if author:
                args += ["--author", author]
            return run(args)
        if operation == "log":
            args = ["git", "log", f"-n{int(kwargs.get('max_count', 10))}"]
            if kwargs.get("oneline", True):
                args.append("--oneline")
            return run(args)
        if operation == "branch":
            args = ["git", "branch"]
            if kwargs.get("all_branches"):
                args.append("-a")
            return run(args)
        if operation == "checkout":
            branch = _safe_branch(kwargs.get("branch", ""))
            # `git switch` only ever changes branches. `git checkout <x>`
            # with x = "." or a file path would discard uncommitted work.
            args = ["git", "switch"]
            if kwargs.get("create"):
                args.append("-c")
            args.append(branch)
            return run(args)
        raise ValueError(f"Unknown git operation: {operation}")


def _safe_branch(branch: Any) -> str:
    """Branch name for `git switch`; option-like names and '..' are rejected."""
    if not isinstance(branch, str) or not branch.strip():
        raise ValueError(f"invalid branch name: {branch!r}")
    if (
        branch.startswith(("-", "/", "~"))
        or branch in (".", "..")
        or ".." in branch
        or "\\" in branch
        or ":" in branch
        or branch.endswith(("/", "."))
        or any(c.isspace() or ord(c) < 32 for c in branch)
    ):
        raise ValueError(f"invalid branch name: {branch!r}")
    # No "existing path" check: `git switch` never treats its argument as a
    # path, so a branch named like a file or directory is harmless.
    return branch


def _safe_files(files: Any) -> list[str]:
    """File arguments for git; entries that look like options are rejected.

    Without this a model could pass e.g. ``--output=/x`` to ``git diff`` or
    ``--exec`` style flags. A ``--`` separator is added by the callers too.
    There is deliberately no push operation in this tool.
    """
    if not files:
        return []
    if isinstance(files, str):
        files = [files]
    out = []
    for f in files:
        if not isinstance(f, str) or not f:
            raise ValueError(f"invalid file entry: {f!r}")
        if f.startswith("-"):
            raise ValueError(f"file entries must not start with '-': {f!r}")
        out.append(f)
    return out


def create_git_tool(config: Config | None = None) -> GitTool:
    return GitTool(config)