# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Readline interface with history and tab completion."""

from __future__ import annotations

import atexit
import os
from pathlib import Path
from typing import Callable, Optional

# GNU readline is missing on Windows. Use pyreadline3 when installed, else
# fall back to plain input() without history or completion.
try:
    import readline  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - depends on platform
    try:
        import pyreadline3  # type: ignore[import-not-found]  # noqa: F401
        import readline  # type: ignore[import-not-found]
    except ImportError:
        readline = None  # type: ignore[assignment]

READLINE_AVAILABLE = readline is not None


class HistoryManager:
    """Manages command history."""

    def __init__(self, history_file: Path, max_size: int = 1000) -> None:
        self.history_file = history_file
        self.max_size = max_size
        self._load()

    def _load(self) -> None:
        self._lines: list[str] = []
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        if readline is None:
            if self.history_file.exists():
                text = self.history_file.read_text(encoding="utf-8", errors="replace")
                self._lines = text.splitlines()[-self.max_size:]
            return
        if self.history_file.exists():
            try:
                readline.read_history_file(str(self.history_file))
            except OSError:
                pass
        readline.set_history_length(self.max_size)

    def save(self) -> None:
        try:
            if readline is None:
                lines = self._lines[-self.max_size:]
                self.history_file.write_text(
                    "".join(line + "\n" for line in lines),
                    encoding="utf-8",
                )
                return
            readline.write_history_file(str(self.history_file))
        except OSError:
            pass

    def add(self, line: str) -> None:
        if readline is None:
            self._lines.append(line)
            return
        readline.add_history(line)

    def get_history(self) -> list[str]:
        if readline is None:
            return list(self._lines)
        return [readline.get_history_item(i) for i in range(1, readline.get_current_history_length() + 1)]


class ReadlineInterface:
    """Readline-based REPL interface with tab completion."""

    def __init__(
        self,
        history_manager: HistoryManager,
        completer: Optional[Callable[[str, int], list[str]]] = None,
    ) -> None:
        self.history = history_manager
        self.completer = completer
        self._setup_readline()
        atexit.register(self.history.save)

    def _setup_readline(self) -> None:
        if readline is None:
            return
        if self.completer:
            readline.set_completer(self.completer)
            readline.parse_and_bind("tab: complete")
        readline.parse_and_bind("set editing-mode emacs")
        readline.parse_and_bind('"\\e[A": history-search-backward')
        readline.parse_and_bind('"\\e[B": history-search-forward')

    def read(self, prompt: str = "> ") -> str:
        """Read a line with history and completion."""
        line = input(prompt)
        if line.strip():
            self.history.add(line)
        return line

    def read_multiline(self, prompt: str = "> ", continuation_prompt: str = "... ") -> str:
        """Read multiple lines until EOF (Ctrl+D) or empty line."""
        lines = []
        first = True
        while True:
            p = prompt if first else continuation_prompt
            try:
                line = input(p)
            except EOFError:
                break
            if first and not line.strip():
                continue
            lines.append(line)
            first = False
            if not line.strip() and lines:
                # Empty line ends multiline input
                break
        return "\n".join(lines).rstrip()