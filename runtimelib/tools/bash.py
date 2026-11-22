# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Bash execution tool using guard client."""

from __future__ import annotations

from typing import Any

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
        import os
        import subprocess
        command = kwargs["command"]
        timeout = kwargs.get("timeout", 30)
        cwd = kwargs.get("cwd", ".")
        env_extra = kwargs.get("env") or {}
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in env_extra.items()})
        try:
            proc = subprocess.run(
                command, shell=True, cwd=cwd, env=env,
                capture_output=True, text=True, timeout=timeout,
            )
            return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
        except subprocess.TimeoutExpired as e:
            return {"exit_code": 124, "stdout": e.stdout or "", "stderr": (e.stderr or "") + f"\nTimeout after {timeout}s"}
        except Exception as e:
            return {"exit_code": 1, "stdout": "", "stderr": str(e)}


def create_bash_tool(config: Config | None = None) -> BashTool:
    return BashTool(config)