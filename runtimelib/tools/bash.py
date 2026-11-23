# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-opus-5-5
# Licensed under MIT License

"""Bash execution tool using guard client."""

from __future__ import annotations

import os
import re
from typing import Any, Mapping

from .base import Tool, ToolSchema
from ..guard import get_guard_client
from ..config import Config


class BashTool(Tool):
    """Bash command execution tool."""

    def __init__(self, config: Config | None = None) -> None:
        schema = ToolSchema(
            name="bash",
            description="Execute shell commands in sandboxed environment",
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description of what the command does",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds",
                        "default": 30,
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory",
                        "default": ".",
                    },
                    "env": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Environment variables",
                    },
                },
                "required": ["command", "description"],
            },
        )
        super().__init__("bash", "Execute shell commands via guard", schema)
        self._config = config
        self._guard = None

    def _get_guard(self):
        if self._guard is None:
            self._guard = get_guard_client(self._config)
        return self._guard

    def execute(self, **kwargs: Any) -> Any:
        if self._config and self._config.get("cli.unsafe_local_tools"):
            return self._exec_local(**kwargs)
        guard = self._get_guard()
        return guard.bash_exec(
            command=kwargs["command"],
            timeout=kwargs.get("timeout", 30),
            cwd=kwargs.get("cwd", "."),
            env=kwargs.get("env"),
        )

    def _exec_local(self, **kwargs: Any) -> dict[str, Any]:
        import subprocess
        command = kwargs["command"]
        timeout = kwargs.get("timeout", 30)
        from pathlib import Path
        from .fileops import resolve_in_workspace
        try:
            cwd = resolve_in_workspace(kwargs.get("cwd", ".") or ".", Path.cwd().resolve())
        except (PermissionError, ValueError) as e:
            return {"exit_code": 1, "stdout": "", "stderr": f"[bash: {e}]"}
        if blocks_git_push(command):
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": "[bash: 'git push' is not allowed from the agent; push by hand]",
            }
        env_extra = {str(k): str(v) for k, v in (kwargs.get("env") or {}).items()}
        env = scrubbed_env(extra=env_extra)
        try:
            proc = subprocess.run(
                command, shell=True, cwd=cwd, env=env,
                capture_output=True, text=True, errors="replace", timeout=timeout,
            )
            return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
        except subprocess.TimeoutExpired as e:
            return {
                "exit_code": 124,
                "stdout": to_text(e.stdout),
                "stderr": to_text(e.stderr) + f"\nTimeout after {timeout}s",
            }
        except Exception as e:
            return {"exit_code": 1, "stdout": "", "stderr": str(e)}


# Variables that look like credentials are never passed to agent commands.
_SECRET_NAME_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")
_SECRET_PREFIXES = ("RUNTIME_LITELLM", "LITELLM")

# `git push` check. This is a guard rail against an accidental push by the
# model, NOT a security boundary: aliases, scripts or `sh -c` with quoting
# tricks can still reach the network. Real isolation needs the Docker guard.
_GIT_PUSH_RE = re.compile(r"\bgit\b[^;&|\n]*\bpush\b")


def is_secret_env_name(name: str) -> bool:
    upper = name.upper()
    return upper.startswith(_SECRET_PREFIXES) or any(part in upper for part in _SECRET_NAME_PARTS)


def scrubbed_env(base: Mapping[str, str] | None = None, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy of the environment without credential-like variables.

    PATH, HOME/USERPROFILE, SYSTEMROOT, TEMP/TMP, LANG and other ordinary
    variables are kept so commands still work on Windows, Linux and macOS.
    """
    source = dict(os.environ if base is None else base)
    if extra:
        source.update(extra)
    return {k: v for k, v in source.items() if not is_secret_env_name(k)}


def blocks_git_push(command: str) -> bool:
    return bool(_GIT_PUSH_RE.search(command or ""))


def to_text(value: Any) -> str:
    """Output of TimeoutExpired is bytes on POSIX even with text=True."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def create_bash_tool(config: Config | None = None) -> BashTool:
    return BashTool(config)