#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Runtime CLI entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import runtimelib
from runtimelib import Config, get_config
from runtimelib.cli import CommandRegistry, ReadlineInterface, HistoryManager, ArgumentParser


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
    if args.config or args.env_file:
        config = Config.load(args.config, args.env_file)
    else:
        config = get_config()

    # Register commands
    registry = CommandRegistry()
    register_core_commands(registry, config)

    # Register runtime-specific commands
    from runtime.commands import register_runtime_commands
    register_runtime_commands(registry, config)

    # If prompt provided, execute as single command
    if args.prompt:
        prompt = " ".join(args.prompt)
        return registry.execute("chat", {"prompt": prompt})

    # Interactive mode
    return run_interactive(registry, config)


def register_core_commands(registry: CommandRegistry, config: Config) -> None:
    """Register core slash commands."""

    @registry.register("help", "Покажи помощ за команди")
    def cmd_help(args: dict[str, Any]) -> int:
        if args:
            cmd = args.get("_0", "") or args.get("command", "")
            if cmd in registry._help:
                print(f"/{cmd} — {registry._help[cmd]}")
            else:
                print(f"Незната команда: /{cmd}")
            return 0

        print("Налични команди:")
        for name in registry.list_commands():
            print(f"  /{name:<20} — {registry._help[name]}")
        return 0

    @registry.register("config", "Покажи или промени конфигурация")
    def cmd_config(args: dict[str, Any]) -> int:
        import json
        if not args:
            print("Текуща конфигурация:")
            print(json.dumps(config.to_dict(), indent=2, ensure_ascii=False))
            return 0

        if "show" in args:
            key = args["show"]
            value = config.get(key)
            print(f"{key} = {json.dumps(value, ensure_ascii=False)}")
            return 0

        if "set" in args:
            # Format: set key=value
            for pair in args.values():
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    try:
                        parsed = json.loads(value)
                    except json.JSONDecodeError:
                        parsed = value
                    print(f"Ще зададе {key} = {parsed} (само за тази сесия)")
            return 0

        return 0

    @registry.register("models", "Покажи налични модели от LiteLLM proxy")
    def cmd_models(args: dict[str, Any]) -> int:
        from runtimelib.llm import LiteLLMClient
        client = LiteLLMClient.from_config(config)
        models = client.list_models()
        if not models:
            print("Няма налични модели")
            return 0

        print(f"{'ID':<35} {'Вход':>10} {'Изход':>10} {'Режим'}")
        print("-" * 70)
        for m in models:
            print(f"{m.get('id', ''):<35} "
                  f"{m.get('max_input_tokens', '?'):>10} "
                  f"{m.get('max_output_tokens', '?'):>10} "
                  f"{m.get('mode', '?')}")
        return 0

    @registry.register("doctor", "Проверете състоянието на системата")
    def cmd_doctor(args: dict[str, Any]) -> int:
        print("🏥 Doctor Check")
        print("─" * 40)

        # Check config
        print(f"✓ Config: {config.get('liteLLM.base_url', 'не зададен')}")

        # Check LiteLLM
        from runtimelib.llm import LiteLLMClient
        try:
            client = LiteLLMClient.from_config(config)
            models = client.list_models()
            print(f"✓ LiteLLM: {len(models)} модела достъпни")
        except Exception as e:
            print(f"✗ LiteLLM: {e}")

        # Check Docker
        import subprocess
        try:
            result = subprocess.run(["docker", "version"], capture_output=True, timeout=5)
            if result.returncode == 0:
                print("✓ Docker: достъпен")
            else:
                print("✗ Docker: не работи")
        except Exception:
            print("✗ Docker: не е инсталиран")

        # Check kiko.db
        kiko_db = Path.home() / ".kiko" / "kiko.db"
        if kiko_db.exists():
            print(f"✓ kiko.db: {kiko_db}")
        else:
            print(f"✗ kiko.db: не е намерен ({kiko_db})")

        return 0

    @registry.register("chat", "Изпрати съобщение към агента")
    def cmd_chat(args: dict[str, Any]) -> int:
        prompt = args.get("prompt", "") or args.get("_0", "")
        if not prompt:
            print("Използване: /chat prompt=...")
            return 1

        from runtimelib.agent import create_agent_loop
        from runtimelib.tools.base import ToolResult
        import json

        # Create agent loop with streaming output
        def on_token(token: str) -> None:
            print(token, end="", flush=True)

        def on_tool_start(name: str, args: dict) -> None:
            print(f"\n[Tool: {name}] {args}")

        def on_tool_end(result: ToolResult) -> None:
            if result.success:
                print(f"[Result] {json.dumps(result.result, ensure_ascii=False)[:200]}")
            else:
                print(f"[Error] {result.error}")

        loop = create_agent_loop(
            config,
            on_token=on_token,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
        )

        print()
        response = loop.run(prompt)
        if response:
            print(response)
        print()
        return 0

    @registry.register("exit", "Изход от runtime")
    def cmd_exit(args: dict[str, Any]) -> int:
        print("Изход...")
        sys.exit(0)

    @registry.register("skills", "Управление на skills")
    def cmd_skills(args: dict[str, Any]) -> int:
        print("Skills система — не е имплементирана още")
        return 0

    @registry.register("session", "Управление на сесии")
    def cmd_session(args: dict[str, Any]) -> int:
        print("Session система — не е имплементирана още")
        return 0


def run_interactive(registry: CommandRegistry, config: Config) -> int:
    """Run interactive REPL with readline support."""
    # Setup history
    history_file = Path.home() / ".config" / "runtime" / "history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    history = HistoryManager(history_file)
    interface = ReadlineInterface(history, completer=lambda text, state: completer(text, state, registry))

    print("Runtime v0.1.0 — Type /help for commands, Ctrl+C to exit")
    print("─" * 60)

    while True:
        try:
            line = interface.read("runtime> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nИзход...")
            break

        if not line:
            continue

        if line.startswith("/"):
            # Slash command
            parts = line[1:].split(maxsplit=1)
            cmd = parts[0]
            arg_str = parts[1] if len(parts) > 1 else ""
            try:
                args = ArgumentParser.parse(arg_str)
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


def completer(text: str, state: int, registry: CommandRegistry) -> str | None:
    commands = [f"/{cmd}" for cmd in registry.list_commands()]
    matches = [cmd for cmd in commands if cmd.startswith(text)]
    if state < len(matches):
        return matches[state]
    return None


if __name__ == "__main__":
    sys.exit(main())