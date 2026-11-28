# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-opus-5-5, claude-sonnet-5-5
# Licensed under MIT License

"""Bash execution tool using guard client."""

from __future__ import annotations

import os
import re
import shutil
from typing import Any, Mapping

from .base import Tool, ToolSchema
from ..guard import get_guard_client
from ..config import Config
from ..procutil import safe_which

NO_BASH_WINDOWS = ("ai-rex needs Git Bash on Windows: install Git for Windows from "
                   "https://git-scm.com/download/win")


def find_bash() -> str | None:
    """Absolute path of the bash that runs local commands, or None.

    Never cmd.exe. On Windows the WSL launcher (System32 / WindowsApps ``bash.exe``)
    is skipped: rex needs Git Bash. Elsewhere ``bash`` on PATH, else ``/bin/sh``.
    """
    found = safe_which("bash")
    if os.name == "nt":
        if found and not found.lower().endswith(".exe"):
            found = None  # bash.bat / bash.cmd would run through cmd.exe
        if found and not any(x in found.lower().replace("\\", "/") for x in ("/system32/", "/windowsapps/")):
            return found
        roots = [os.environ.get(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData")]
        git = safe_which("git")
        if git:
            roots.append(os.path.dirname(os.path.dirname(git)))
        for root in roots:
            for rel in ("Git/bin/bash.exe", "Programs/Git/bin/bash.exe", "bin/bash.exe"):
                cand = os.path.join(root, rel) if root else ""
                if cand and os.path.isfile(cand):
                    return cand
        return None
    return found or shutil.which("sh") or "/bin/sh"


BASH_DESCRIPTION = (
    "Run one shell command (bash; Git Bash on Windows) in the workspace; returns exit code, stdout, stderr.\n"
    "Use it for builds, tests, linters, version checks and quick inspections; read the output before the "
    "next step (errors are usually at the end; pipe long output through tail -n 60).\n"
    "Default timeout 30 s: pass timeout=600 for builds and test runs.\n"
    "Prefer fileops for listing, reading and searching files.\n"
    'Example: {"command": "mvn -q -o test", "description": "run the unit tests", "timeout": 600}'
)


class BashTool(Tool):
    """Bash command execution tool."""

    def __init__(self, config: Config | None = None) -> None:
        schema = ToolSchema(
            name="bash",
            description=BASH_DESCRIPTION,
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
        shell = find_bash()
        if shell is None:
            return {"exit_code": 1, "stdout": "", "stderr": f"[bash: {NO_BASH_WINDOWS}]"}
        env_extra = {str(k): str(v) for k, v in (kwargs.get("env") or {}).items()}
        env = scrubbed_env(extra=env_extra)
        try:
            argv = [shell, "-c", command]
            if os.name == "nt":
                # Git Bash re-parses the Windows command line (expands `@file`, mangles
                # quotes). Pass the text in the environment; the argv is then constant.
                env["REX_COMMAND"] = command
                argv = [shell, "-c", 'eval "$REX_COMMAND"']
            proc = subprocess.run(
                argv, shell=False, cwd=cwd, env=env,
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
# This scrub and the `git push` check below are guard rails against accidents,
# NOT a security boundary: on Linux a command can still read the parent's
# environment from /proc/<ppid>/environ. Real isolation needs the Docker guard.
_SECRET_NAME_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL",
                      "AUTH", "COOKIE", "DATABASE_URL")
_SECRET_SUFFIXES = ("_PAT",)
_SECRET_PREFIXES = ("AI_REX_LITELLM", "RUNTIME_LITELLM", "LITELLM")
# Exact names that match a secret pattern but are needed and hold no secret
# themselves (XAUTHORITY is the path X11 GUI programs need to find their cookie).
_NOT_SECRET_NAMES = frozenset({"XAUTHORITY"})

# `git push` check. This is a guard rail against an accidental push by the
# model, NOT a security boundary: aliases, scripts or `sh -c` with quoting
# tricks can still reach the network. Real isolation needs the Docker guard.
_GIT_PUSH_RE = re.compile(r"\bgit\b[^;&|\n]*\bpush\b")


def is_secret_env_name(name: str) -> bool:
    upper = name.upper()
    if upper in _NOT_SECRET_NAMES:
        return False
    return (
        upper.startswith(_SECRET_PREFIXES)
        or upper.endswith(_SECRET_SUFFIXES)
        or any(part in upper for part in _SECRET_NAME_PARTS)
    )


def is_secret_env(name: str, value: str) -> bool:
    """Name looks like a credential, or a proxy URL carries user:pass@."""
    if is_secret_env_name(name):
        return True
    return "PROXY" in name.upper() and "@" in (value or "")


def scrubbed_env(base: Mapping[str, str] | None = None, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy of the environment without credential-like variables.

    PATH, HOME/USERPROFILE, SYSTEMROOT, TEMP/TMP, LANG and other ordinary
    variables are kept so commands still work on Windows, Linux and macOS.
    """
    source = dict(os.environ if base is None else base)
    if extra:
        source.update(extra)
    return {k: v for k, v in source.items() if not is_secret_env(k, v)}


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