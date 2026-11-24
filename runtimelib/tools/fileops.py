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


class FileOpsTool(Tool):
    """File operations tool (read, write, edit, list, glob, grep)."""

    def __init__(self, config: Config | None = None, tracker: ReadTracker | None = None) -> None:
        schema = ToolSchema(
            name="fileops",
            description="File operations: read, write, edit, list, glob, grep",
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
            for dirpath, _, fnames in os.walk(base):
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