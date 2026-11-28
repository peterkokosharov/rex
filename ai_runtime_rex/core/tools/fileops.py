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
READ_BYTES_CAP = 16 * 1024 * 1024  # bytes read from one file (a read result is cut to READ_CHAR_CAP anyway)
# Caches and tool data: list/glob/grep leave them out at any depth.
CACHE_DIRS = frozenset({".git", "__pycache__", ".venv", "venv", "node_modules", ".idea", ".gradle", ".mypy_cache",
                        ".pytest_cache", ".ruff_cache", ".tox", ".svn", ".hg"})
# Build output: left out only at the workspace root or next to a build file (src/build/ may be real source).
BUILD_DIRS = frozenset({"target", "dist", "build"})
BUILD_FILES = ("pom.xml", "build.gradle", "build.gradle.kts", "package.json", "pyproject.toml", "setup.py")
SKIP_DIRS = CACHE_DIRS | BUILD_DIRS  # all names that may be skipped (kept for callers of the old name)

DESCRIPTION = """Workspace files. Operations:
- list: entries of a folder (path, default "."); recursive=true for a whole small tree. Cache folders (.git,
  node_modules, __pycache__, .venv, .idea, ...) and build output (target, build, dist at the root or next to
  pom.xml / package.json / ...) below the path are skipped and counted; pass such a folder as the path to look
  inside it. Example: {"operation": "list", "path": "src"}
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


def _build_output(parent: str, root: Path | None = None) -> bool:
    """A build folder inside ``parent`` (workspace-relative, "" = root) is build output: at the root, or when
    ``parent`` holds a build file."""
    if not parent:
        return True
    folder = (root or Path.cwd()) / parent
    return any((folder / name).is_file() for name in BUILD_FILES)


def _skipped(rel: Any, base: str = "", root: Path | None = None) -> bool:
    """True when ``rel`` lies in a cache or build folder BELOW ``base`` (a folder the model asked for is never
    hidden)."""
    text = _norm(rel.get("path", "") if isinstance(rel, dict) else rel)
    parts = [seg for seg in text.split("/") if seg]
    start = 0
    if base and (text == base or text.startswith(base + "/")):
        start = len([seg for seg in base.split("/") if seg])
    for i in range(start, len(parts)):
        seg = parts[i]
        if seg in CACHE_DIRS:
            return True
        if seg in BUILD_DIRS and _build_output("/".join(parts[:i]), root):
            return True
    return False


def _keep(items: Any, base: str = "") -> tuple[Any, int]:
    """(items outside skipped folders, number left out)."""
    if not isinstance(items, list):
        return items, 0
    kept = [i for i in items if not _skipped(i, base)]
    return kept, len(items) - len(kept)


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
        self._read_note = ""
        result = self._execute(operation, **kwargs)
        if operation == "read":
            text = number_lines(result, offset, limit)
            return f"{text}\n{self._read_note}" if self._read_note and isinstance(text, str) else text
        if operation in ("list", "glob", "grep") and isinstance(result, dict):
            out: dict[str, Any] = {}
            hidden = int(result.get("hidden") or 0)
            for key, value in result.items():
                if key == "hidden":
                    continue
                out[key], dropped = _keep(value, base)
                hidden += dropped
            if hidden:
                out["hidden"] = hidden
            return out
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
            size = path.stat().st_size
            with open(path, "rb") as fh:
                data = fh.read(READ_BYTES_CAP)
            if size > READ_BYTES_CAP:
                # Part of a file: not recorded as read, so an edit of it is refused.
                self._read_note = (f"[... file is {size} bytes; only the first {READ_BYTES_CAP} bytes were read]")
            else:
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
            if not recursive:
                return {"files": sorted(_inside(p.iterdir(), root))}
            items, hidden = _walk(p, root)
            out: dict[str, Any] = {"files": sorted(_inside(items, root))}
            if hidden:
                out["hidden"] = hidden
            return out
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
            if base.is_file():
                walk = [(str(base.parent), [], [base.name])]
            else:
                walk = _pruned_walk(base, root)
            for dirpath, _dirs, fnames in walk:
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


def _pruned_walk(top: Path, root: Path, hidden: list[int] | None = None):
    """``os.walk`` that never descends into a skipped folder below ``top`` (counted in ``hidden[0]``)."""
    import os
    asked = _norm(_relative_inside(top, root) or "")
    for dirpath, dirnames, fnames in os.walk(top):
        rel = _relative_inside(dirpath, root)
        keep = []
        for d in dirnames:
            child = f"{rel}/{d}" if rel and rel != "." else d
            if _skipped(child, asked, root):
                if hidden is not None:
                    hidden[0] += 1
            else:
                keep.append(d)
        dirnames[:] = keep
        yield dirpath, dirnames, fnames


def _walk(top: Path, root: Path) -> tuple[list[Path], int]:
    """Every file and folder below ``top`` outside skipped folders, and how many folders were skipped."""
    hidden = [0]
    items: list[Path] = []
    for dirpath, dirnames, fnames in _pruned_walk(top, root, hidden):
        base = Path(dirpath)
        items.extend(base / d for d in dirnames)
        items.extend(base / f for f in fnames)
    return items, hidden[0]


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