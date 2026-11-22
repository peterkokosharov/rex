"""Pure Python fallback for rex native helpers."""
from __future__ import annotations

import glob as _glob
import json
import os
import re
from typing import Any


def json_escape(s: str) -> str:
    # JSON string content escaping, without surrounding quotes
    return json.dumps(s, ensure_ascii=False)[1:-1]


def json_get_string(json_text: str, key: str) -> str:
    # Simple string field extraction (matches native behavior for common cases)
    m = re.search(r'"' + re.escape(key) + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', json_text)
    if not m:
        return ""
    try:
        return json.loads('"' + m.group(1) + '"')
    except Exception:
        return m.group(1)


def parse_sse_data(line: str) -> str | None:
    s = line.lstrip(" \t")
    if not s.startswith("data:"):
        return None
    data = s[5:]
    data = data.lstrip(" \t")
    data = data.rstrip(" \t\r\n")
    if data == "[DONE]":
        return "[DONE]"
    return data


def grep(root: str, pattern: str, include: str | None = None, exclude: str | None = None, max_matches: int = 200) -> list[dict[str, Any]]:
    rx = re.compile(pattern)
    matches: list[dict[str, Any]] = []
    for dirpath, _, fnames in os.walk(root):
        for fn in fnames:
            if include and not _glob.fnmatch.fnmatch(fn, include):
                continue
            if exclude and _glob.fnmatch.fnmatch(fn, exclude):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            matches.append({"path": fp, "line": i, "text": line.rstrip("\n")[:300]})
                            if len(matches) >= max_matches:
                                return matches
            except Exception:
                pass
    return matches


def glob(root: str, pattern: str) -> list[str]:
    # Similar to native: support ** and basename matching
    matches = []
    recursive = "**" in pattern
    if recursive:
        for dirpath, dirnames, fnames in os.walk(root):
            for name in dirnames + fnames:
                p = os.path.join(dirpath, name)
                rel = os.path.relpath(p, root)
                base = os.path.basename(p)
                tail = pattern.rsplit("/", 1)[-1]
                if _glob.fnmatch.fnmatch(rel, pattern) or _glob.fnmatch.fnmatch(base, pattern) or _glob.fnmatch.fnmatch(base, tail):
                    matches.append(p)
    else:
        for name in os.listdir(root):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, root)
            if _glob.fnmatch.fnmatch(rel, pattern) or _glob.fnmatch.fnmatch(name, pattern):
                matches.append(p)
    return sorted(matches)
