"""Optional native helpers for rex (C++ via ctypes, Python fallback)."""
from __future__ import annotations

import ctypes
import json
import os
from typing import Any

from . import _fallback

NATIVE_AVAILABLE = False
_backend = "python"
_lib = None


def _load():
    global NATIVE_AVAILABLE, _backend, _lib
    here = os.path.dirname(__file__)
    names = ["librex_native.so", "librex_native.dylib", "rex_native.dll", "librex_native.dll"]
    path = None
    for n in names:
        p = os.path.join(here, n)
        if os.path.exists(p):
            path = p
            break
    if not path:
        return
    try:
        lib = ctypes.CDLL(path)
        lib.rex_json_escape.argtypes = [ctypes.c_char_p]
        lib.rex_json_escape.restype = ctypes.c_void_p
        lib.rex_json_get_string.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        lib.rex_json_get_string.restype = ctypes.c_void_p
        lib.rex_parse_sse_data.argtypes = [ctypes.c_char_p]
        lib.rex_parse_sse_data.restype = ctypes.c_void_p
        lib.rex_grep.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        lib.rex_grep.restype = ctypes.c_void_p
        lib.rex_glob.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        lib.rex_glob.restype = ctypes.c_void_p
        lib.rex_free.argtypes = [ctypes.c_void_p]
        lib.rex_free.restype = None
        _lib = lib
        NATIVE_AVAILABLE = True
        _backend = "native"
    except Exception:
        _lib = None
        NATIVE_AVAILABLE = False
        _backend = "python"


_load()


def backend() -> str:
    return _backend


def _str(ptr) -> str:
    if not ptr:
        return ""
    try:
        return ctypes.string_at(ptr).decode("utf-8", errors="replace")
    finally:
        _lib.rex_free(ptr)


def json_escape(s: str) -> str:
    if not NATIVE_AVAILABLE:
        return _fallback.json_escape(s)
    ptr = _lib.rex_json_escape(s.encode("utf-8"))
    return _str(ptr)


def json_get_string(json_text: str, key: str) -> str:
    if not NATIVE_AVAILABLE:
        return _fallback.json_get_string(json_text, key)
    ptr = _lib.rex_json_get_string(json_text.encode("utf-8"), key.encode("utf-8"))
    return _str(ptr)


def parse_sse_data(line: str) -> str | None:
    if not NATIVE_AVAILABLE:
        return _fallback.parse_sse_data(line)
    ptr = _lib.rex_parse_sse_data(line.encode("utf-8"))
    if not ptr:
        return None
    return _str(ptr)


def grep(root: str, pattern: str, include: str | None = None, exclude: str | None = None, max_matches: int = 200) -> list[dict[str, Any]]:
    if not NATIVE_AVAILABLE:
        return _fallback.grep(root, pattern, include, exclude, max_matches)
    ptr = _lib.rex_grep(
        root.encode("utf-8"),
        pattern.encode("utf-8"),
        include.encode("utf-8") if include else None,
        exclude.encode("utf-8") if exclude else None,
        int(max_matches),
    )
    if not ptr:
        # C++ std::regex uses ECMAScript syntax; Python patterns may differ.
        return _fallback.grep(root, pattern, include, exclude, max_matches)
    raw = _str(ptr)
    try:
        return json.loads(raw)
    except ValueError:
        # Malformed native output must not look like "no matches".
        return _fallback.grep(root, pattern, include, exclude, max_matches)


def glob(root: str, pattern: str) -> list[str]:
    # The native helper only matches direct children for patterns without
    # '**', so 'src/*.py' found nothing. Such patterns use the Python path.
    nested = ('/' in pattern or os.sep in pattern) and '**' not in pattern
    if not NATIVE_AVAILABLE or nested:
        return _fallback.glob(root, pattern)
    ptr = _lib.rex_glob(root.encode("utf-8"), pattern.encode("utf-8"))
    raw = _str(ptr)
    try:
        return json.loads(raw)
    except ValueError as exc:
        # Raise instead of returning [] so callers (fileops) fall back to the
        # Python glob rather than reporting "no matches".
        raise RuntimeError(f"native glob returned malformed JSON: {exc}") from None


__all__ = ["NATIVE_AVAILABLE", "backend", "json_escape", "json_get_string", "parse_sse_data", "grep", "glob"]
