# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-opus-5-5
# Licensed under MIT License
"""Install-time shell choice for Windows, kept in the install folder's ``ai-rex.json``.

At install time (``scripts/install_ai_rex.py``, or ``install.cmd`` in the install folder to redo only this step):

* Git Bash found (PATH, ``%LOCALAPPDATA%\\Programs\\Git\\bin\\bash.exe``, ``%ProgramFiles%\\Git\\bin\\bash.exe``)
  -> ``tools.shell = bash`` with its full path, without asking;
* not found -> ask once: 1) PowerShell (pwsh if present, else Windows PowerShell 5.1)  2) cmd;
  ``--shell`` answers it for a non-interactive install, and without a terminal the answer is PowerShell.

At run time on Windows the precedence is ``--shell`` > ``AI_REX_SHELL`` > config ``tools.shell`` > this recorded
setting > automatic detection (only without a setting, e.g. from a repo checkout). A recorded shell that no
longer exists gives one stderr line and PowerShell for that run (never a silent cmd).

On Linux, macOS and the BSDs nothing is recorded: ``auto`` uses the login shell (``SHELL``) first.

    python -m ai_runtime_rex.runtime.shellsetup DEST [--shell git-bash|powershell|cmd]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

SETTINGS_NAME = "ai-rex.json"
CHOICES = ("git-bash", "powershell", "cmd")
_KIND = {"git-bash": "bash", "powershell": "powershell", "cmd": "cmd"}


def settings_path(install: Path) -> Path:
    return Path(install) / SETTINGS_NAME


def read_settings(install: Optional[Path]) -> dict[str, Any]:
    """The install folder's settings ({} when missing or unreadable)."""
    if install is None:
        return {}
    try:
        data = json.loads(settings_path(install).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_settings(install: Path, data: dict[str, Any]) -> Path:
    """Write the settings atomically (temporary file + rename)."""
    path = settings_path(install)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def find_git_bash(environ: Optional[Mapping[str, str]] = None, which: Optional[Callable[[str], Any]] = None,
                  isfile: Callable[[str], bool] = os.path.isfile) -> Optional[str]:
    """Git Bash: ``bash.exe`` on PATH (not the WSL launcher), then the two standard Git for Windows folders."""
    env = os.environ if environ is None else environ
    if which is None:
        import shutil
        which = shutil.which
    found = which("bash")
    if found:
        low = str(found).lower().replace("\\", "/")
        if low.endswith(".exe") and "/system32/" not in low and "/windowsapps/" not in low:
            if low.endswith("/usr/bin/bash.exe"):
                # Inside Git Bash, PATH finds <git>/usr/bin/bash.exe; <git>/bin/bash.exe is the launcher that
                # sets up the Git Bash environment (PATH with the Unix tools) for a program started from Windows.
                top = os.path.dirname(os.path.dirname(os.path.dirname(str(found))))
                wrapper = os.path.join(top, "bin", "bash.exe")
                if isfile(wrapper):
                    return wrapper
            return str(found)
    for var, rel in (("LOCALAPPDATA", ("Programs", "Git", "bin", "bash.exe")),
                     ("ProgramFiles", ("Git", "bin", "bash.exe"))):
        root = env.get(var)
        if root:
            cand = os.path.join(root, *rel)
            if isfile(cand):
                return cand
    return None


def find_choice_path(choice: str, environ: Optional[Mapping[str, str]] = None,
                     which: Optional[Callable[[str], Any]] = None,
                     isfile: Callable[[str], bool] = os.path.isfile) -> Optional[tuple[str, str]]:
    """(path, label) for ``powershell`` (pwsh, then Windows PowerShell 5.1) or ``cmd``; None if absent."""
    env = os.environ if environ is None else environ
    if which is None:
        import shutil
        which = shutil.which
    system_root = env.get("SystemRoot") or r"C:\Windows"
    if choice == "powershell":
        for name, label in (("pwsh", "PowerShell 7"), ("powershell", "Windows PowerShell 5.1")):
            path = which(name)
            if path and str(path).lower().endswith(".exe"):
                return str(path), label
        cand = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        return (cand, "Windows PowerShell 5.1") if isfile(cand) else None
    if choice == "cmd":
        cand = os.path.join(system_root, "System32", "cmd.exe")
        return (cand, "cmd.exe") if isfile(cand) else None
    return None


def ask_choice(ask: Callable[[str], str], out: Any) -> str:
    """Ask once (no Git Bash): 1) PowerShell  2) cmd. Empty or anything else = PowerShell."""
    print("Git Bash was not found. Which shell should ai-rex use for commands?", file=out)
    print("  1) PowerShell (pwsh if present, else Windows PowerShell 5.1)", file=out)
    print("  2) cmd", file=out)
    try:
        answer = ask("Choice [1]: ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    return "cmd" if answer == "2" else "powershell"


def choose_shell(choice: Optional[str] = None, *, interactive: bool = False, ask: Callable[[str], str] = input,
                 out: Any = None, environ: Optional[Mapping[str, str]] = None,
                 which: Optional[Callable[[str], Any]] = None,
                 isfile: Callable[[str], bool] = os.path.isfile) -> dict[str, str]:
    """The ``tools`` settings to record: ``{"shell": kind, "shell_path": path, "shell_label": label}``.

    ``choice`` (``--shell``) wins; else Git Bash when found (no question); else the question when
    ``interactive``, else PowerShell. Raises SystemExit when the wanted shell is not installed.
    """
    out = out or sys.stdout
    if choice is not None and choice not in CHOICES:
        raise SystemExit(f"unknown shell {choice!r} (use {', '.join(CHOICES)})")
    if choice in (None, "git-bash"):
        bash = find_git_bash(environ, which, isfile)
        if bash:
            return {"shell": "bash", "shell_path": bash, "shell_label": "Git Bash"}
        if choice == "git-bash":
            raise SystemExit("Git Bash not found (install Git for Windows, or use --shell powershell / cmd)")
        choice = ask_choice(ask, out) if interactive else "powershell"
    found = find_choice_path(choice, environ, which, isfile)
    if found is None and choice == "cmd":
        raise SystemExit("cmd.exe not found")
    if found is None:
        raise SystemExit("PowerShell not found (use --shell cmd)")
    return {"shell": _KIND[choice], "shell_path": found[0], "shell_label": found[1]}


def record(install: Path, tools: Mapping[str, str]) -> Path:
    """Merge ``tools`` into the install folder's settings file."""
    data = read_settings(install)
    data.setdefault("version", 1)
    section = data.get("tools") if isinstance(data.get("tools"), dict) else {}
    section.update(tools)
    data["tools"] = section
    return write_settings(install, data)


def setup(install: Path, choice: Optional[str] = None, *, interactive: Optional[bool] = None,
          out: Any = None, **finders: Any) -> Optional[dict[str, str]]:
    """Detect / ask and record the shell (Windows only; returns None elsewhere)."""
    out = out or sys.stdout
    if os.name != "nt" and not finders.get("force"):
        return None
    finders.pop("force", None)
    if interactive is None:
        interactive = bool(sys.stdin and sys.stdin.isatty())
    tools = choose_shell(choice, interactive=interactive, out=out, **finders)
    path = record(install, tools)
    print(f"shell: {tools['shell_label']} ({tools['shell_path']}), recorded in {path}", file=out)
    return tools


# ------------------------------------------------------------------ run time

def apply_recorded_shell(config: Any, install: Optional[Path], err: Any = None,
                         isfile: Callable[[str], bool] = os.path.isfile,
                         ps_finder: Optional[Callable[[], Optional[tuple[str, str]]]] = None) -> Optional[str]:
    """Use the install folder's recorded shell when nothing else chose one (Windows).

    Call only when no ``--shell``, ``AI_REX_SHELL`` or config ``tools.shell`` is set. Returns the kind used,
    or None (no record). A recorded path that is gone: one stderr line and PowerShell for this run.
    """
    tools = read_settings(install).get("tools")
    if not isinstance(tools, dict) or tools.get("shell") not in ("bash", "powershell", "cmd"):
        return None
    kind, path = tools["shell"], str(tools.get("shell_path") or "")
    if path and isfile(path):
        config.set("tools.shell", kind)
        config.set("tools.shell_path", path)
        return kind
    print(f"ai-rex: recorded shell {path or kind} not found; run install again or use --shell",
          file=err or sys.stderr)
    found = (ps_finder or (lambda: find_choice_path("powershell")))()
    config.set("tools.shell", "powershell")
    if found:
        config.set("tools.shell_path", found[0])
    return "powershell"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="install.cmd", description="Detect and record the shell ai-rex uses")
    parser.add_argument("dest", type=Path, help="the ai-rex install folder")
    parser.add_argument("--shell", choices=CHOICES, default=None,
                        help="record this shell without asking (default: Git Bash if found, else ask)")
    args = parser.parse_args(argv)
    if not (args.dest / ".ai-rex-install").is_file():
        print(f"refusing: {args.dest} holds no ai-rex install", file=sys.stderr)
        return 1
    if os.name != "nt":
        print("nothing to record: on Linux, macOS and the BSDs ai-rex uses your login shell (SHELL)")
        return 0
    setup(args.dest, args.shell)
    return 0


if __name__ == "__main__":
    sys.exit(main())
