# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License

"""Precise edit tool: exact match, read-before-edit hash check, unified diff.

The same code serves the dedicated ``edit`` tool and ``fileops edit`` so both
behave identically in local mode and in guard mode.
"""

from __future__ import annotations

import difflib
import hashlib
import threading
from pathlib import Path
from typing import Any

from .base import Tool, ToolSchema
from ..config import Config

MAX_DIFF_LINES = 400
DIFF_CONTEXT = 3


class EditError(ValueError):
    """The edit was refused; the message tells the model what to do next."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


class ReadTracker:
    """Remembers the sha256 of every file the model has read or written.

    ``edit`` refuses files that are not recorded or whose content changed
    since. One tracker is shared by the tools of a process (a session).
    """

    def __init__(self) -> None:
        self._hashes: dict[str, str] = {}
        self._lock = threading.Lock()

    def record(self, key: str, digest: str) -> None:
        with self._lock:
            self._hashes[key] = digest

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._hashes.get(key)

    def clear(self) -> None:
        with self._lock:
            self._hashes.clear()


# Default tracker shared by fileops and edit.
READ_TRACKER = ReadTracker()


# --- file access adapters ---------------------------------------------------

class LocalFile:
    """A file on the host, already resolved inside the workspace."""

    def __init__(self, raw_path: str, path: Path) -> None:
        self.display = raw_path
        self.path = path
        self.key = "local:" + str(path)

    def read(self) -> tuple[str, str]:
        """Return (text, digest of the raw bytes)."""
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            raise EditError(f"file not found: {self.display} (use fileops write to create files)")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise EditError(f"{self.display} is not valid UTF-8; edit refuses binary files")
        return text, sha256_bytes(data)

    def write(self, text: str) -> str:
        data = text.encode("utf-8")
        self.path.write_bytes(data)
        return sha256_bytes(data)


class GuardFile:
    """A file reached through the guard client (``fs_read`` / ``fs_write``)."""

    def __init__(self, guard: Any, raw_path: str) -> None:
        self.display = raw_path
        self.guard = guard
        norm = raw_path.replace("\\", "/")
        while norm.startswith("./"):
            norm = norm[2:]
        self.key = "guard:" + norm

    def read(self) -> tuple[str, str]:
        text = self.guard.fs_read(self.display)
        return text, sha256_text(text)

    def write(self, text: str) -> str:
        self.guard.fs_write(self.display, text)
        return sha256_text(text)


# --- core -------------------------------------------------------------------

def _split_keepends(text: str) -> list[str]:
    """Lines split on "\n" only, each keeping its newline; the last line has none if the text lacks one."""
    parts = text.split("\n")
    last = parts.pop()
    out = [p + "\n" for p in parts]
    if last:
        out.append(last)
    return out


def make_diff(old: str, new: str, path: str, max_lines: int = MAX_DIFF_LINES) -> str:
    """Unified diff (3 lines context), capped at ``max_lines`` lines."""
    raw = list(difflib.unified_diff(
        _split_keepends(old), _split_keepends(new),
        fromfile=f"a/{path}", tofile=f"b/{path}",
        n=DIFF_CONTEXT, lineterm="",
    ))
    lines: list[str] = []
    for i, ln in enumerate(raw):
        if i < 2 or ln.startswith("@@"):  # file headers and hunk headers
            lines.append(ln)
        elif ln.endswith("\n"):
            lines.append(ln[:-1])
        else:  # a body line whose source line has no trailing newline
            lines.append(ln)
            lines.append("\\ No newline at end of file")
    if len(lines) > max_lines:
        extra = len(lines) - max_lines
        lines = lines[:max_lines] + [f"... {extra} more lines"]
    return "\n".join(lines)


def _not_found_hint(text: str, old: str) -> str:
    hint = "old_string was not found; it must match exactly, including whitespace and indentation"
    first = next((ln.strip() for ln in old.splitlines() if ln.strip()), "")
    if first and first in text:
        hint += " (its first non-blank line does appear in the file: check indentation and the following lines)"
    else:
        hint += "; re-read the file and copy the text exactly"
    return hint


def plan_edit(
    f: Any,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    tracker: ReadTracker | None = None,
    check_read: bool = True,
) -> dict[str, Any]:
    """Validate and compute an edit without writing.

    Returns ``{"text", "crlf", "replacements", "diff"}`` where ``text`` is the
    new content with the file's original line endings.
    """
    tracker = READ_TRACKER if tracker is None else tracker
    if not isinstance(old_string, str) or not isinstance(new_string, str):
        raise EditError("old_string and new_string must be strings")
    if old_string == "":
        raise EditError("old_string must not be empty (use fileops write to create or overwrite a file)")
    if old_string == new_string:
        raise EditError("old_string and new_string are identical; nothing to change")

    text, digest = f.read()
    if check_read:
        known = tracker.get(f.key)
        if known is None:
            raise EditError(f"{f.display} has not been read in this session; read it with fileops read first")
        if known != digest:
            raise EditError(f"{f.display} changed since it was read; read it again before editing")

    crlf = "\r\n" in text
    if crlf and text.count("\n") > text.count("\r\n"):
        raise EditError(
            f"{f.display} mixes CRLF and LF line endings; edit refuses it because it could not keep them "
            "intact. Normalise the line endings first (for example rewrite the file with fileops write)"
        )
    if crlf:
        text = text.replace("\r\n", "\n")
    old = old_string.replace("\r\n", "\n")
    new = new_string.replace("\r\n", "\n")

    count = text.count(old)
    if count == 0:
        raise EditError(_not_found_hint(text, old))
    if count > 1 and not replace_all:
        raise EditError(
            f"old_string occurs {count} times in {f.display}; add more surrounding context "
            "to make it unique, or set replace_all to true"
        )
    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    diff = make_diff(text, updated, f.display)
    if crlf:
        updated = updated.replace("\n", "\r\n")
    return {"text": updated, "crlf": crlf, "replacements": count if replace_all else 1, "diff": diff}


def apply_edit(
    f: Any,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    tracker: ReadTracker | None = None,
) -> dict[str, Any]:
    """Edit ``f`` (LocalFile or GuardFile); returns ``{"ok","path","replacements","diff"}``."""
    tracker = READ_TRACKER if tracker is None else tracker
    plan = plan_edit(f, old_string, new_string, replace_all, tracker)
    digest = f.write(plan["text"])
    tracker.record(f.key, digest)
    return {
        "ok": True,
        "path": f.display,
        "replacements": plan["replacements"],
        "diff": plan["diff"],
    }


# --- tool -------------------------------------------------------------------

class EditTool(Tool):
    """Replace exact text in a file that was read earlier in the session."""

    def __init__(self, config: Config | None = None, tracker: ReadTracker | None = None) -> None:
        schema = ToolSchema(
            name="edit",
            description=(
                "Replace exact text in an existing file. The file must have been read with fileops read "
                "in this session and not changed since. old_string must match exactly once (or set "
                "replace_all). Returns a unified diff. Use fileops write to create new files."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "old_string": {"type": "string", "description": "Exact text to replace"},
                    "new_string": {"type": "string", "description": "Replacement text"},
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence",
                        "default": False,
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        )
        super().__init__("edit", "Precise file edit with diff", schema)
        self._config = config
        self._tracker = READ_TRACKER if tracker is None else tracker
        self._guard = None

    def _file(self, path: str) -> Any:
        if self._config and self._config.get("cli.unsafe_local_tools"):
            from .fileops import resolve_in_workspace
            root = Path.cwd().resolve()
            return LocalFile(path, resolve_in_workspace(path, root))
        if self._guard is None:
            from ..guard import get_guard_client
            self._guard = get_guard_client(self._config)
        return GuardFile(self._guard, path)

    def execute(self, **kwargs: Any) -> Any:
        path = kwargs.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path must be a non-empty string")
        return apply_edit(
            self._file(path),
            kwargs.get("old_string", ""),
            kwargs.get("new_string", ""),
            bool(kwargs.get("replace_all", False)),
            self._tracker,
        )

    def describe(self, args: dict[str, Any]) -> tuple[str, str]:
        """(summary, detail) for permission prompts; detail is the diff preview."""
        path = str(args.get("path", ""))
        summary = f"edit {path}"
        try:
            plan = plan_edit(
                self._file(path),
                args.get("old_string", ""),
                args.get("new_string", ""),
                bool(args.get("replace_all", False)),
                self._tracker,
            )
            return summary, plan["diff"]
        except Exception as exc:  # preview only; the real call reports the error
            return summary, f"(no preview: {exc})"


def create_edit_tool(config: Config | None = None) -> EditTool:
    return EditTool(config)
