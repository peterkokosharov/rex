# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License

"""Small process helpers shared by the diagram tool and the Claude CLI gateway (stdlib only)."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path


def popen_group_kwargs() -> dict:
    """Popen kwargs that put the child in its own process group/session, so kill_tree can reach its children."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _system32(name: str) -> str:
    """Full path of a Windows system program: a bare name would be searched in the current directory first."""
    return os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", name)


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill the process and its children (taskkill /T on Windows, the process group elsewhere)."""
    try:
        if os.name == "nt":
            subprocess.run(
                [_system32("taskkill.exe"), "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
                shell=False,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _resolved(path: str | os.PathLike) -> Path | None:
    try:
        return Path(path).resolve()
    except (OSError, RuntimeError):
        return None


def safe_path_entries(exclude: tuple = ()) -> list[str]:
    """PATH entries without "" , "." , the current working directory and any ``exclude`` directory."""
    banned = [r for r in (_resolved(p) for p in (Path.cwd(), *exclude) if p) if r is not None]
    entries: list[str] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry in ("", "."):
            continue
        resolved = _resolved(entry)
        if resolved is None or resolved in banned:
            continue
        entries.append(entry)
    return entries


def safe_which(name: str, exclude: tuple = ()) -> str | None:
    """Like shutil.which for a bare command name, but never finds a binary in the current directory
    or in an ``exclude`` directory (a workspace can plant one). Does not touch os.environ."""
    if not name or os.path.dirname(name) or (os.altsep and os.altsep in name):
        return None
    exts = [""]
    if os.name == "nt":
        pathext = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep)
        exts = [e.lower() for e in pathext if e]
        if Path(name).suffix.lower() in exts:
            exts = [""]
    for entry in safe_path_entries(exclude):
        for ext in exts:
            candidate = Path(entry) / (name + ext)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None
