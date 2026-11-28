# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-opus-5-5, claude-sonnet-5-5
# Licensed under MIT License

"""File operations tool using guard client."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .base import Tool, ToolSchema
from .edit import READ_TRACKER, GuardFile, LocalFile, ReadTracker, apply_edit, sha256_bytes, sha256_text
from ..guard import get_guard_client
from ..config import Config

READ_LIMIT = 2000  # lines per read unless the model asks for fewer
LINE_CAP = 2000  # characters per line in read output
READ_CHAR_CAP = 200 * 1024  # characters per read result
# Folders that are build output, caches or tool data: list/glob/grep leave them out.
SKIP_DIRS = frozenset({".git", "__pycache__", ".venv", "venv", "node_modules", ".idea", "target", ".gradle",
                       ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "dist", "build", ".svn", ".hg"})

DESCRIPTION = """Workspace files. Operations:
- list: entries of a folder (path, default "."); recursive=true for a whole small tree. Build/cache folders
  (.git, target, node_modules, __pycache__, .venv, .idea, ...) below the path are skipped; pass such a folder
  as the path to look inside it. Example: {"operation": "list", "path": "src"}
- glob: files matching a pattern, e.g. {"operation": "glob", "pattern": "**/*.java"}
- grep: regex search in file contents, result lines "path:line: text"; narrow with path and include
  (e.g. "*.py"). Example: {"operation": "grep", "pattern": "def main", "include": "*.py"}
- read: file with numbered lines ("  12\\tcode"); offset = first line (1-based), limit = number of lines
  (default 2000). A longer file ends with a note giving the next offset. Cite path:line from these numbers;
  never copy the number prefix into an edit. Example: {"operation": "read", "path": "src/app.py", "offset": 200, "limit": 100}
- write: create or replace a whole file (content). edit: replace old_str with new_str in a file you have read."""


def _norm(p: Any) -> str:
    text = str(p or ".").replace("\\", "/").strip("/")
    return "" if text in ("", ".") else (text[2:] if text.startswith("./") else text)


def _skipped(rel: Any, base: str = "") -> bool:
    """True when ``rel`` lies in a build/cache folder BELOW ``base`` (a folder the model asked for is never hidden)."""
    text = _norm(rel.get("path", "") if isinstance(rel, dict) else rel)
    if base and (text == base or text.startswith(base + "/")):
        text = text[len(base):].lstrip("/")
    return any(seg in SKIP_DIRS for seg in text.split("/") if seg)


def _keep(items: Any, base: str = "") -> Any:
    if isinstance(items, list):
        return [i for i in items if not _skipped(i, base)]
    return items


def number_lines(text: Any, offset: Any = None, limit: Any = None) -> Any:
    """``cat -n`` style view of ``text`` from line ``offset`` (1-based), at most ``limit`` lines, size capped."""
    if not isinstance(text, str):
        return text
    try:
        start = max(1, int(offset)) if offset not in (None, "") else 1
    except (TypeError, ValueError):
        start = 1
    try:
        count = max(1, int(limit)) if limit not in (None, "") else READ_LIMIT
    except (TypeError, ValueError):
        count = READ_LIMIT
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    total = len(lines)
    if total == 0:
        return "(empty file)"
    if start > total:
        return f"(offset {start} is past the end: the file has {total} lines)"
    out: list[str] = []
    size = 0
    end = start - 1
    width = len(str(min(total, start + count - 1)))
    for n in range(start, min(total, start + count - 1) + 1):
        line = lines[n - 1]
        if len(line) > LINE_CAP:
            line = line[:LINE_CAP] + " [... line cut]"
        row = f"{n:>{width}}\t{line}"
        if size + len(row) > READ_CHAR_CAP and out:
            break
        out.append(row)
        size += len(row) + 1
        end = n
    if end < total:
        out.append(f"[... truncated: lines {end + 1}-{total} not shown; read again with offset={end + 1}]")
    return "\n".join(out)


class FileOpsTool(Tool):
    """File operations tool (read, write, edit, list, glob, grep)."""

    def __init__(self, config: Config | None = None, tracker: ReadTracker | None = None) -> None:
        schema = ToolSchema(
            name="fileops",
            description=DESCRIPTION,
            parameters={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["read", "write", "edit", "list", "glob", "grep"],
                        "description": "Operation to perform",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory path",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content for write operation",
                    },
                    "old_str": {
                        "type": "string",
                        "description": "String to replace for edit operation",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "Replacement string for edit operation",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace all occurrences in edit",
                        "default": False,
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Pattern for glob/grep",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Recursive list/glob",
                        "default": False,
                    },
                    "include": {
                        "type": "string",
                        "description": "Include pattern for grep",
                    },
                    "exclude": {
                        "type": "string",
                        "description": "Exclude pattern for grep",
                    },
                    "root": {
                        "type": "string",
                        "description": "glob: folder to search in (default .)",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "read: first line to show (1-based)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "read: number of lines to show (default 2000)",
                    },
                },
                "required": ["operation"],
            },
        )
        super().__init__("fileops", "File operations via guard", schema)
        self._config = config
        self._guard = None
        self._tracker = READ_TRACKER if tracker is None else tracker

    def _get_guard(self):
        if self._guard is None:
            self._guard = get_guard_client(self._config)
        return self._guard

    def execute(self, **kwargs: Any) -> Any:
        operation = kwargs.pop("operation")
        offset = kwargs.pop("offset", None)
        limit = kwargs.pop("limit", None)
        base = _norm(kwargs.get("root") if operation == "glob" else kwargs.get("path"))
        result = self._execute(operation, **kwargs)
        if operation == "read":
            return number_lines(result, offset, limit)
        if operation in ("list", "glob", "grep") and isinstance(result, dict):
            return {k: _keep(v, base) for k, v in result.items()}
        return result

    def _execute(self, operation: str, **kwargs: Any) -> Any:
        if self._config and self._config.get("cli.unsafe_local_tools"):
            return self._exec_local(operation, **kwargs)
        guard = self._get_guard()

        if operation == "read":
            path = kwargs.pop("path")
            content = guard.fs_read(path)
            if isinstance(content, str):
                self._tracker.record(GuardFile(guard, path).key, sha256_text(content))
            return content
        elif operation == "write":
            path = kwargs.pop("path")
            content = kwargs.pop("content", "")
            guard.fs_write(path, content)
            self._tracker.record(GuardFile(guard, path).key, sha256_text(content))
            return {"ok": True, "path": path}
        elif operation == "edit":
            path = kwargs.pop("path")
            old_str = kwargs.pop("old_str")
            new_str = kwargs.pop("new_str")
            replace_all = kwargs.pop("replace_all", False)
            return apply_edit(GuardFile(guard, path), old_str, new_str, replace_all, self._tracker)
        elif operation == "list":
            path = kwargs.pop("path", ".")
            recursive = kwargs.pop("recursive", False)
            return {"files": guard.fs_list(path, recursive)}
        elif operation == "glob":
            pattern = kwargs.pop("pattern")
            root = kwargs.pop("root", ".")
            return {"matches": guard.fs_glob(pattern, root)}
        elif operation == "grep":
            pattern = kwargs.pop("pattern")
            path = kwargs.pop("path", ".")
            include = kwargs.pop("include", None)
            exclude = kwargs.pop("exclude", None)
            return {"matches": guard.fs_grep(pattern, path, include, exclude)}
        else:
            raise ValueError(f"Unknown operation: {operation}")

    def _workspace_root(self) -> Path:
        """Root that local file operations may not leave (the current directory)."""
        return Path.cwd().resolve()

    def _exec_local(self, operation: str, **kwargs: Any) -> Any:
        """Run a file operation on the host, confined to the workspace root.

        Every path is resolved against the workspace root (cwd). Absolute
        paths and anything that resolves outside the root, including through
        ``..`` or symlinks, are rejected. Results of list/glob/grep are filtered
        with the same check.
        """
        import fnmatch
        import os
        import re

        root = self._workspace_root()

        if operation == "read":
            path = resolve_in_workspace(kwargs["path"], root)
            data = path.read_bytes()
            self._tracker.record(LocalFile(kwargs["path"], path).key, sha256_bytes(data))
            text = data.decode("utf-8", errors="replace")
            return text.replace("\r\n", "\n").replace("\r", "\n")
        if operation == "write":
            path = resolve_in_workspace(kwargs["path"], root)
            content = kwargs.get("content", "")
            path.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode("utf-8")
            path.write_bytes(data)
            self._tracker.record(LocalFile(kwargs["path"], path).key, sha256_bytes(data))
            return {"ok": True, "path": kwargs["path"]}
        if operation == "edit":
            path = resolve_in_workspace(kwargs["path"], root)
            return apply_edit(
                LocalFile(kwargs["path"], path),
                kwargs["old_str"],
                kwargs["new_str"],
                kwargs.get("replace_all", False),
                self._tracker,
            )
        if operation == "list":
            p = resolve_in_workspace(kwargs.get("path", "."), root)
            recursive = kwargs.get("recursive", False)
            items = p.rglob("*") if recursive else p.iterdir()
            return {"files": sorted(_inside(items, root))}
        if operation == "glob":
            pattern = kwargs["pattern"]
            _check_pattern(pattern)
            base = resolve_in_workspace(kwargs.get("root", "."), root)
            from ..native import glob as native_glob, _fallback
            try:
                found = native_glob(str(base), pattern)
            except Exception:
                # The native library failed: use the pure-Python glob instead
                # of silently returning nothing.
                found = _fallback.glob(str(base), pattern)
            return {"matches": sorted(_inside(found, root))}
        if operation == "grep":
            pattern = kwargs["pattern"]
            base = resolve_in_workspace(kwargs.get("path", "."), root)
            include = kwargs.get("include")
            exclude = kwargs.get("exclude")
            rx = re.compile(pattern)
            matches = []
            for dirpath, dirnames, fnames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for fn in fnames:
                    if include and not fnmatch.fnmatch(fn, include):
                        continue
                    if exclude and fnmatch.fnmatch(fn, exclude):
                        continue
                    fp = Path(dirpath) / fn
                    rel = _relative_inside(fp, root)
                    if rel is None:  # symlink pointing outside the workspace
                        continue
                    try:
                        with open(fp, "r", encoding="utf-8", errors="replace") as f:
                            for i, line in enumerate(f, 1):
                                if rx.search(line):
                                    matches.append({"path": rel, "line": i, "text": line.rstrip("\n")[:300]})
                                    if len(matches) >= 200:
                                        return {"matches": matches}
                    except OSError:
                        pass
            return {"matches": matches}
        raise ValueError(f"Unknown operation: {operation}")


def _is_absolute_like(raw: str) -> bool:
    """Absolute on this OS or on the other one (``/etc``, ``C:\\x``, ``\\\\host``)."""
    return (
        Path(raw).is_absolute()
        or PurePosixPath(raw).is_absolute()
        or bool(PureWindowsPath(raw).drive)
        or PureWindowsPath(raw).root != ""
    )


def resolve_in_workspace(raw: Any, root: Path) -> Path:
    """Resolve ``raw`` inside ``root`` or raise PermissionError."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("path must be a non-empty string")
    if "\x00" in raw:
        raise PermissionError("path contains a NUL byte")
    if _is_absolute_like(raw) or raw.startswith("~"):
        raise PermissionError(f"absolute paths are not allowed: {raw}")
    resolved = (root / raw).resolve()
    if not resolved.is_relative_to(root):
        raise PermissionError(f"path is outside the workspace: {raw}")
    return resolved


def _check_pattern(pattern: Any) -> None:
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("pattern must be a non-empty string")
    if _is_absolute_like(pattern) or pattern.startswith("~"):
        raise PermissionError(f"absolute glob patterns are not allowed: {pattern}")
    if any(seg == ".." for seg in pattern.replace("\\", "/").split("/")):
        raise PermissionError(f"'..' is not allowed in glob patterns: {pattern}")


def _relative_inside(path: Any, root: Path) -> str | None:
    """Workspace-relative POSIX path, or None when it resolves outside ``root``."""
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    try:
        resolved = p.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(root):
        return None
    return resolved.relative_to(root).as_posix() or "."


def _inside(paths: Any, root: Path) -> list[str]:
    out = []
    for item in paths:
        rel = _relative_inside(item, root)
        if rel is not None:
            out.append(rel)
    return out


def create_fileops_tool(config: Config | None = None) -> FileOpsTool:
    return FileOpsTool(config)