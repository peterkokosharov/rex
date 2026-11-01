# SPDX-License-Identifier: MIT
"""Slash command registry and core commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .agent import ToolResult


CommandHandler = Callable[[dict[str, Any]], int]


class CommandRegistry:
    """Registry for slash commands."""

    def __init__(self) -> None:
        self._commands: dict[str, CommandHandler] = {}
        self._help: dict[str, str] = {}

    def register(self, name: str, help_text: str) -> Callable[[CommandHandler], CommandHandler]:
        """Decorator to register a command."""
        def decorator(handler: CommandHandler) -> CommandHandler:
            self._commands[name] = handler
            self._help[name] = help_text
            return handler
        return decorator

    def execute(self, name: str, args: dict[str, Any]) -> int:
        """Execute a command by name."""
        if name not in self._commands:
            raise ValueError(f"Незната команда: /{name}. Използвайте /help")
        return self._commands[name](args)

    def list_commands(self) -> list[str]:
        return sorted(self._commands.keys())

    def get_help(self, name: str) -> str:
        return self._help.get(name, "Няма описание")


def register_core_commands(registry: CommandRegistry, config: Config) -> None:
    """Register core slash commands."""

    @registry.register("help", "Покажи помощ за команди")
    def cmd_help(args: dict[str, Any]) -> int:
        if args:
            cmd = args.get("command", "")
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
                        import json
                        parsed = json.loads(value)
                    except json.JSONDecodeError:
                        parsed = value
                    # Note: This only modifies in-memory config
                    print(f"Ще зададе {key} = {parsed} (само за тази сесия)")
            return 0

        return 0

    @registry.register("models", "Покажи налични модели от LiteLLM proxy")
    def cmd_models(args: dict[str, Any]) -> int:
        from .llm.client import LiteLLMClient
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
        print(f"✓ Config: {Config.load()._data.get('liteLLM', {}).get('base_url', 'не зададен')}")

        # Check LiteLLM
        from .llm.client import LiteLLMClient
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
        prompt = args.get("prompt", "")
        if not prompt:
            print("Използване: /chat prompt=...")
            return 1

        from .agent import create_agent_loop

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
        import sys
        sys.exit(0)

    @registry.register("skills", "Управление на skills")
    def cmd_skills(args: dict[str, Any]) -> int:
        print("Skills система — не е имплементирана още")
        return 0

    @registry.register("session", "Управление на сесии")
    def cmd_session(args: dict[str, Any]) -> int:
        print("Session система — не е имплементирана още")
        return 0