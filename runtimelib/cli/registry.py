# SPDX-License-Identifier: MIT
"""Command registry with decorator-based registration."""

from __future__ import annotations

from typing import Any, Callable

CommandHandler = Callable[[dict[str, Any]], int]


class CommandRegistry:
    """Registry for slash commands."""

    def __init__(self) -> None:
        self._commands: dict[str, CommandHandler] = {}
        self._help: dict[str, str] = {}
        self._aliases: dict[str, str] = {}

    def register(
        self, name: str, help_text: str = "", aliases: list[str] | None = None
    ) -> Callable[[CommandHandler], CommandHandler]:
        """Decorator to register a command."""
        def decorator(handler: CommandHandler) -> CommandHandler:
            self._commands[name] = handler
            self._help[name] = help_text
            if aliases:
                for alias in aliases:
                    self._aliases[alias] = name
            return handler
        return decorator

    def execute(self, name: str, args: dict[str, Any]) -> int:
        """Execute a command by name."""
        # Resolve alias
        actual_name = self._aliases.get(name, name)
        if actual_name not in self._commands:
            raise ValueError(f"Незната команда: /{name}. Използвайте /help")
        return self._commands[actual_name](args)

    def list_commands(self) -> list[str]:
        return sorted(self._commands.keys())

    def get_help(self, name: str) -> str:
        actual_name = self._aliases.get(name, name)
        return self._help.get(actual_name, "Няма описание")

    def has_command(self, name: str) -> bool:
        actual_name = self._aliases.get(name, name)
        return actual_name in self._commands