# SPDX-License-Identifier: MIT
"""Readline interface with history and tab completion."""

from __future__ import annotations

import atexit
import os
import readline
from pathlib import Path
from typing import Callable, Optional


class HistoryManager:
    """Manages command history."""

    def __init__(self, history_file: Path, max_size: int = 1000) -> None:
        self.history_file = history_file
        self.max_size = max_size
        self._load()

    def _load(self) -> None:
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        if self.history_file.exists():
            readline.read_history_file(str(self.history_file))
        readline.set_history_length(self.max_size)

    def save(self) -> None:
        readline.write_history_file(str(self.history_file))

    def add(self, line: str) -> None:
        readline.add_history(line)

    def get_history(self) -> list[str]:
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