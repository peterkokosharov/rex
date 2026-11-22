# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""CLI framework for runtime."""

from .registry import CommandRegistry, CommandHandler
from .readline import ReadlineInterface, HistoryManager
from .parser import ArgumentParser
from .jsonl_bridge import JsonlBridge, run_jsonl
from .shell import (
    ShellResult,
    is_shell_line,
    print_shell_result,
    run_shell,
    shell_command,
)

__all__ = [
    "CommandRegistry",
    "CommandHandler",
    "ReadlineInterface",
    "HistoryManager",
    "ArgumentParser",
    "JsonlBridge",
    "run_jsonl",
    "ShellResult",
    "is_shell_line",
    "print_shell_result",
    "run_shell",
    "shell_command",
]