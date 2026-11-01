#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Runtime CLI entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config, get_config
from .commands import CommandRegistry, register_core_commands


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="runtime",
        description="Claude-like runtime for OpenAI-compatible models via LiteLLM proxy",
    )
    parser.add_argument(
        "--version", action="version", version="%(prog)s 0.1.0"
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to config file",
        default=None,
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Path to .env file",
        default=None,
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Prompt to send to the agent (if omitted, starts interactive mode)",
    )
    return parser


def main() -> int:
    parser = create_parser()
    args = parser.parse_args()

    # Load config
    config = Config.load(args.config, args.env_file)

    # Register commands
    registry = CommandRegistry()
    register_core_commands(registry, config)

    # If prompt provided, execute as single command
    if args.prompt:
        prompt = " ".join(args.prompt)
        return registry.execute("chat", {"prompt": prompt})

    # Interactive mode
    return run_interactive(registry, config)


def run_interactive(registry: CommandRegistry, config: Config) -> int:
    """Run interactive REPL with readline support."""
    try:
        import readline
    except ImportError:
        readline = None

    # Setup history
    history_file = Path.home() / ".config" / "runtime" / "history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    if readline:
        try:
            readline.read_history_file(str(history_file))
        except FileNotFoundError:
            pass
        readline.set_history_length(1000)

    # Tab completion
    if readline:
        def completer(text: str, state: int) -> str | None:
            commands = [f"/{cmd}" for cmd in registry.list_commands()]
            matches = [cmd for cmd in commands if cmd.startswith(text)]
            if state < len(matches):
                return matches[state]
            return None
        readline.set_completer(completer)
        readline.parse_and_bind("tab: complete")

    print("Runtime v0.1.0 — Type /help for commands, Ctrl+C to exit")
    print("─" * 60)

    while True:
        try:
            line = input("runtime> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nИзход...")
            break

        if not line:
            continue

        if readline:
            readline.write_history_file(str(history_file))

        if line.startswith("/"):
            # Slash command
            parts = line[1:].split(maxsplit=1)
            cmd = parts[0]
            arg_str = parts[1] if len(parts) > 1 else ""
            try:
                # Parse args as simple key=value pairs
                args = {}
                if arg_str:
                    for pair in arg_str.split():
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            args[k] = v
                registry.execute(cmd, args)
            except Exception as e:
                print(f"Грешка: {e}")
        else:
            # Regular prompt → chat
            try:
                registry.execute("chat", {"prompt": line})
            except Exception as e:
                print(f"Грешка: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())