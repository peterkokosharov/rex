# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""Small process helpers shared by the diagram tool and the Claude CLI gateway (stdlib only)."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path


def popen_group_kwargs() -> dict:
    """Popen kwargs that put the child in its own process group/session, so kill_tree can reach its children."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill the process and its children (taskkill /T on Windows, the process group elsewhere)."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
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


def safe_path_entries() -> list[str]:
    """PATH entries without "" , "." and the current working directory."""
    cwd = Path.cwd().resolve()
    entries: list[str] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry in ("", "."):
            continue
        try:
            if Path(entry).resolve() == cwd:
                continue
        except OSError:
            continue
        entries.append(entry)
    return entries


def safe_which(name: str) -> str | None:
    """Like shutil.which, but never finds a binary in the current directory (a workspace can plant one)."""
    entries = safe_path_entries()
    if not entries:
        return None
    # Windows adds the cwd in front of an explicit path too, unless this variable is set.
    old = os.environ.get("NoDefaultCurrentDirectoryInExePath")
    os.environ["NoDefaultCurrentDirectoryInExePath"] = "1"
    try:
        found = shutil.which(name, path=os.pathsep.join(entries))
    finally:
        if old is None:
            os.environ.pop("NoDefaultCurrentDirectoryInExePath", None)
        else:
            os.environ["NoDefaultCurrentDirectoryInExePath"] = old
    if found is None:
        return None
    try:
        if Path(found).resolve().parent == Path.cwd().resolve():
            return None
    except OSError:
        return None
    return found
