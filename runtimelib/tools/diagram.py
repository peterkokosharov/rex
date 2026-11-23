# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""Diagram tool: renders Mermaid source to an image with merman-cli."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .base import Tool, ToolSchema
from .fileops import resolve_in_workspace

DIAGRAM_DIR = "diagrams"
TIMEOUT_SECONDS = 30
FORMATS = ("svg", "png")
MAX_ERROR_CHARS = 800
RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

DESCRIPTION = (
    "Render a Mermaid diagram (flowchart, sequence, class, state, ER, gantt, UML-like) "
    "to an image file in the workspace. Use it whenever the user asks for a diagram. "
    "In the answer put a compact text sketch of the same diagram (Unicode box-drawing "
    "characters, at most ~60 columns, inside a ``` block; if the diagram is too big, "
    "sketch only the main path), followed by the returned Markdown image line. "
    "Terminal front-ends show the sketch and can open the image on demand; GUI "
    "front-ends show the image. If render_diagram fails, say so in one line and give only the compact sketch."
)


def sanitize_name(name: Any) -> str:
    """Reduce ``name`` to a safe file stem (letters, digits, ``-``, ``_``); may be empty."""
    if not isinstance(name, str):
        return ""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip()).strip("-_")
    return stem[:60]


def find_merman() -> str | None:
    """merman-cli from $MERMAN_HOME first, then from PATH."""
    home = os.environ.get("MERMAN_HOME")
    if home:
        for exe in ("merman-cli", "merman-cli.exe"):
            cand = Path(home) / exe
            if cand.is_file():
                return str(cand)
    return shutil.which("merman-cli")


def build_command(binary: str, src: str, out: str, fmt: str) -> list[str]:
    return [binary, "render", src, "-o", out, "-f", fmt, "-q"]


class DiagramTool(Tool):
    """render_diagram: Mermaid source -> <workspace>/diagrams/<name>.<svg|png>."""

    def __init__(self, config: Any = None) -> None:
        schema = ToolSchema(
            name="render_diagram",
            description=DESCRIPTION,
            parameters={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Mermaid diagram source"},
                    "name": {"type": "string", "description": "Short file name without extension (optional)"},
                    "format": {"type": "string", "enum": list(FORMATS), "description": "svg (default) or png"},
                },
                "required": ["source"],
            },
        )
        super().__init__("render_diagram", DESCRIPTION, schema)
        self._config = config

    def _workspace_root(self) -> Path:
        return Path.cwd().resolve()

    def execute(self, **kwargs: Any) -> Any:
        source = kwargs.get("source")
        if not isinstance(source, str) or not source.strip():
            return {"ok": False, "error": "source must be a non-empty Mermaid string"}
        fmt = kwargs.get("format") or "svg"
        if fmt not in FORMATS:
            return {"ok": False, "error": f"format must be one of {', '.join(FORMATS)}"}
        label = sanitize_name(kwargs.get("name"))
        stem = label or hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
        if stem.split(".")[0].upper() in RESERVED_NAMES:
            stem = "d-" + stem
        rel = f"{DIAGRAM_DIR}/{stem}.{fmt}"

        binary = find_merman()
        if binary is None:
            home = os.environ.get("MERMAN_HOME")
            if home:
                return {"ok": False, "error": f"MERMAN_HOME={home} has no merman-cli(.exe)"}
            return {
                "ok": False,
                "error": "merman-cli not found: set MERMAN_HOME to the folder containing "
                "merman-cli (or put merman-cli on PATH)",
            }

        root = self._workspace_root()
        try:
            resolve_in_workspace(rel, root)
        except (PermissionError, ValueError, OSError) as exc:
            return {"ok": False, "error": f"cannot write diagram: {exc}"}

        tmp_dir = tempfile.mkdtemp(prefix="rex-diagram-")
        try:
            src = Path(tmp_dir) / "diagram.mmd"
            out = Path(tmp_dir) / f"out.{fmt}"
            src.write_text(source, encoding="utf-8", newline="\n")
            err = _run(build_command(binary, str(src), str(out), fmt))
            if err is not None:
                return {"ok": False, "error": err}
            if not out.is_file():
                return {"ok": False, "error": "merman-cli produced no output file"}
            data = out.read_bytes()
        except OSError as exc:
            return {"ok": False, "error": f"cannot run merman-cli: {exc}"}
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        try:
            _store(root, stem, fmt, data)
        except OSError as exc:  # PermissionError is an OSError
            return {"ok": False, "error": f"cannot write diagram: {exc}"}
        return {"ok": True, "path": rel, "markdown": f"![{label or stem}]({rel})"}


def _kill_tree(proc: subprocess.Popen) -> None:
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


def _run(cmd: list[str]) -> str | None:
    """Run merman; return an error message, or None on success. Timeout kills the process tree."""
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        **kwargs,
    )
    try:
        stdout, stderr = proc.communicate(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        return f"merman-cli timed out after {TIMEOUT_SECONDS} s"
    if proc.returncode != 0:
        msg = (stderr or stdout or "").strip()[:MAX_ERROR_CHARS]
        return f"merman-cli failed (exit {proc.returncode}): {msg}"
    return None


def _is_link(path: str | Path) -> bool:
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


def _store(root: Path, stem: str, fmt: str, data: bytes) -> None:
    """Write data to <root>/diagrams/<stem>.<fmt> without following symlinks."""
    ddir = root / DIAGRAM_DIR
    name = f"{stem}.{fmt}"
    if _is_link(ddir):
        raise PermissionError("diagrams/ is a symlink")
    ddir.mkdir(exist_ok=True)
    if _is_link(ddir) or not ddir.resolve().is_relative_to(root):
        raise PermissionError("diagrams/ is outside the workspace")
    target = ddir / name
    if _is_link(target):
        raise PermissionError(f"{DIAGRAM_DIR}/{name} is a symlink")

    if os.name != "nt" and hasattr(os, "O_NOFOLLOW"):
        dfd = os.open(ddir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            # O_NONBLOCK: a FIFO planted at the target must not hang the open. O_TRUNC is
            # applied only after the regular-file check below.
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                0o644,
                dir_fd=dfd,
            )
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise PermissionError(f"{DIAGRAM_DIR}/{name} is not a regular file")
                os.ftruncate(fd, 0)
            except BaseException:
                os.close(fd)
                raise
            with os.fdopen(fd, "wb") as f:
                f.write(data)
        finally:
            os.close(dfd)
        return

    tmp = ddir / f".{name}.{os.getpid()}.tmp"

    def check() -> None:
        if _is_link(ddir) or _is_link(target) or not Path(os.path.realpath(ddir)).is_relative_to(root):
            raise PermissionError("diagrams/ changed during write (symlink)")

    created = False
    try:
        check()
        with open(tmp, "xb") as f:  # exclusive: never follows or reuses an existing path
            created = True
            f.write(data)
        check()
        os.replace(tmp, target)
    finally:
        if created:
            tmp.unlink(missing_ok=True)


def create_diagram_tool(config: Any = None) -> DiagramTool:
    return DiagramTool(config)
