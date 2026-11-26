# SPDX-License-Identifier: MIT
"""Small ANSI helpers (stdlib only)."""

from __future__ import annotations

import os
import shutil
import sys


def supports_color(stream=None) -> bool:
    s = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(s, "isatty") and s.isatty()


COLOR = supports_color()


def _wrap(code: str, text: str) -> str:
    if not COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(text: str) -> str:
    return _wrap("1", text)


def dim(text: str) -> str:
    return _wrap("2", text)


def red(text: str) -> str:
    return _wrap("31", text)


def green(text: str) -> str:
    return _wrap("32", text)


def yellow(text: str) -> str:
    return _wrap("33", text)


def blue(text: str) -> str:
    return _wrap("34", text)


def cyan(text: str) -> str:
    return _wrap("36", text)


def gray(text: str) -> str:
    return _wrap("90", text)


def clear_line() -> str:
    return "\033[2K\r" if COLOR else "\r"


def term_width() -> int:
    return shutil.get_terminal_size((80, 24)).columns
