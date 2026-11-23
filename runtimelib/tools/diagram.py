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

DESCRIPTION = (
    "Render a Mermaid diagram (flowchart, sequence, class, state, ER, gantt, UML-like) "
    "to an image file in the workspace. Use it whenever the user asks for a diagram. "
    "In the answer put a compact text sketch of the same diagram (Unicode box-drawing "
    "characters, at most ~60 columns, inside a ``` block; if the diagram is too big, "
    "sketch only the main path), followed by the returned Markdown image line. "
    "Terminal front-ends show the sketch and can open the image on demand; GUI "
    "front-ends show the image."
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

        binary = find_merman()
        if binary is None:
            return {
                "ok": False,
                "error": "merman-cli not found: set MERMAN_HOME to the folder containing "
                "merman-cli (or put merman-cli on PATH)",
            }

        root = self._workspace_root()
        try:
            out_path = resolve_in_workspace(f"{DIAGRAM_DIR}/{stem}.{fmt}", root)
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except (PermissionError, ValueError, OSError) as exc:
            return {"ok": False, "error": f"cannot write diagram: {exc}"}

        tmp_dir = tempfile.mkdtemp(prefix="rex-diagram-")
        try:
            src = Path(tmp_dir) / "diagram.mmd"
            src.write_text(source, encoding="utf-8", newline="\n")
            try:
                proc = subprocess.run(
                    build_command(binary, str(src), str(out_path), fmt),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=TIMEOUT_SECONDS,
                    shell=False,
                )
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": f"merman-cli timed out after {TIMEOUT_SECONDS} s"}
            except OSError as exc:
                return {"ok": False, "error": f"cannot run merman-cli: {exc}"}
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        if proc.returncode != 0 or not out_path.is_file():
            msg = (proc.stderr or proc.stdout or "").strip()[:MAX_ERROR_CHARS]
            return {"ok": False, "error": f"merman-cli failed (exit {proc.returncode}): {msg}"}

        rel = f"{DIAGRAM_DIR}/{out_path.name}"
        return {"ok": True, "path": rel, "markdown": f"![{label or stem}]({rel})"}


def create_diagram_tool(config: Any = None) -> DiagramTool:
    return DiagramTool(config)
