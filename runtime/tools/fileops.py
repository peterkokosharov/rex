# SPDX-License-Identifier: MIT
"""File operations tool using guard client."""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolSchema
from ..guard import get_guard_client
from ..config import Config


class FileOpsTool(Tool):
    """File operations tool (read, write, edit, list, glob, grep)."""

    def __init__(self, config: Config | None = None) -> None:
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

    def _get_guard(self):
        if self._guard is None:
            self._guard = get_guard_client(self._config)
        return self._guard

    def execute(self, **kwargs: Any) -> Any:
        operation = kwargs.pop("operation")
        guard = self._get_guard()

        if operation == "read":
            path = kwargs.pop("path")
            return guard.fs_read(path)

        elif operation == "write":
            path = kwargs.pop("path")
            content = kwargs.pop("content", "")
            guard.fs_write(path, content)
            return {"ok": True, "path": path}

        elif operation == "edit":
            path = kwargs.pop("path")
            old_str = kwargs.pop("old_str")
            new_str = kwargs.pop("new_str")
            replace_all = kwargs.pop("replace_all", False)
            guard.fs_edit(path, old_str, new_str, replace_all)
            return {"ok": True, "path": path}

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


def create_fileops_tool(config: Config | None = None) -> FileOpsTool:
    return FileOpsTool(config)