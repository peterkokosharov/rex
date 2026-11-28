# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: nvidia nemotron ultra, claude-opus-5-5
# Licensed under MIT License

"""Explicit shell escape for the CLI: a prompt starting with ``!`` runs locally.

This never goes through the LLM. It is the user typing a command, so shell
metacharacters, pipes and quoting behave exactly like in a terminal.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SHELLS = {"/bin/bash", "bash", "/bin/sh", "sh", "/usr/bin/bash"}


@dataclass
class ShellResult:
    """Outcome of one local shell command."""

    command: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def is_shell_line(line: str) -> bool:
    """True when the user asked for a local shell command."""
    return line.lstrip().startswith("!")


def shell_command(line: str) -> str:
    """Strip the leading ``!`` from a user line.

    ``! /bin/bash pwd`` is treated as ``/bin/bash -c pwd`` for convenience;
    explicit flags (``-c``, ``-lc``) and script paths are left untouched.
    """
    command = line.lstrip()[1:].strip()
    parts = command.split(None, 1)
    if len(parts) == 2 and parts[0] in SHELLS:
        rest = parts[1].strip()
        first = rest.split(None, 1)[0]
        if not rest.startswith("-") and "/" not in first and "." not in first:
            return f"{parts[0]} -c {shlex.quote(rest)}"
    return command


def run_shell(command: str, cwd: Optional[Path] = None, timeout: Optional[float] = None,
              config: object = None) -> ShellResult:
    """Run ``command`` in ``cwd`` with the same shell as the bash tool (config tools.shell / env AI_REX_SHELL).

    Same rules as the tool: stdin is empty, a launched program (start msedge x.html) does not block the
    prompt, a timeout or Ctrl+C ends the command's own process tree.
    """
    if not command.strip():
        return ShellResult(command=command, stderr="[shell: empty command]", exit_code=1)
    import os
    from ..tools.bash import config_shell, run_tree, shell_choice
    shell = config_shell(config)
    if shell is None:
        return ShellResult(command, "", f"[shell: no shell found for '{shell_choice(config)}']", 1)
    env = dict(os.environ)  # the user's own command: nothing is scrubbed
    try:
        code, out, err, ended = run_tree(shell.command_line(command, env), cwd=str(cwd or Path.cwd()), env=env,
                                         timeout=timeout or 24 * 3600, encoding=shell.encoding)
    except OSError as exc:
        return ShellResult(command, "", str(exc), 1)
    if ended:
        return ShellResult(command, out, err + f"\n[shell: timed out after {timeout}s]", 124)
    return ShellResult(command, out, err, code)


def _to_text(value: object) -> str:
    """TimeoutExpired carries bytes on POSIX even when text=True was asked."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def print_shell_result(result: ShellResult) -> int:
    """Print a shell result and return its exit code."""
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    if result.exit_code:
        print(f"[shell: exit {result.exit_code}]", file=sys.stderr)
    return result.exit_code
