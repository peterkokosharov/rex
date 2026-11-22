# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
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


def run_shell(command: str, cwd: Optional[Path] = None, timeout: Optional[float] = None) -> ShellResult:
    """Run ``command`` with the system shell in ``cwd``."""
    if not command.strip():
        return ShellResult(command=command, stderr="[shell: empty command]", exit_code=1)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd or Path.cwd()),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        return ShellResult(command, proc.stdout, proc.stderr, proc.returncode)
    except subprocess.TimeoutExpired as exc:
        stderr = (exc.stderr or "") + f"\n[shell: timed out after {timeout}s]"
        return ShellResult(command, exc.stdout or "", stderr, 124)
    except OSError as exc:
        return ShellResult(command, "", str(exc), 1)


def print_shell_result(result: ShellResult) -> int:
    """Print a shell result and return its exit code."""
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    if result.exit_code:
        print(f"[shell: exit {result.exit_code}]", file=sys.stderr)
    return result.exit_code
