#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

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
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    parser.add_argument("--config", type=Path, default=None, help="Path to config file")
    parser.add_argument("--env-file", type=Path, default=None, help="Path to .env file")
    parser.add_argument("--model", default=None, help="Model alias/id to use")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument("--no-stream", action="store_true", help="Disable token streaming")
    parser.add_argument("--verbose", action="store_true", help="Show verbose tool output")
    parser.add_argument("--quiet", action="store_true", help="Reduce non-essential output")
    parser.add_argument("--unsafe-local-tools", "--local-tools", action="store_true", dest="unsafe_local_tools", help="DEV ONLY: run tools locally without Docker guard")
    parser.add_argument("prompt", nargs="*", help="Prompt to send to the agent (if omitted, starts interactive mode)")
    return parser


def main() -> int:
    parser = create_parser()
    args = parser.parse_args()

    # Load config
    if args.config or args.env_file:
        config = Config.load(args.config, args.env_file)
    else:
        config = get_config()

    if args.model:
        config.set("liteLLM.model", args.model)
    config.set("cli.json", bool(args.json))
    config.set("cli.no_stream", bool(args.no_stream))
    config.set("cli.verbose", bool(args.verbose))
    config.set("cli.quiet", bool(args.quiet))
    config.set("cli.unsafe_local_tools", bool(args.unsafe_local_tools))
    if args.unsafe_local_tools:
        import sys
        print("WARNING: --unsafe-local-tools enabled. Tools run on host without Docker guard.", file=sys.stderr)

    # Register commands
    registry = CommandRegistry()
    register_core_commands(registry, config)

    from runtime.commands import register_runtime_commands
    register_runtime_commands(registry, config)

    if args.prompt:
        prompt = " ".join(args.prompt)
        return registry.execute("chat", {"prompt": prompt})

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
        try:
            models = client.list_models()
        except Exception as e:
            print(f"Грешка: {e}")
            return 1
        if not models:
            print("Няма налични модели")
            return 0
        current = config.get("liteLLM.model", "")
        for m in models:
            mid = m.get("id", "")
            mark = "*" if mid == current else " "
            print(f"{mark} {mid}")
        return 0

    @registry.register("model", "Покажи или смени активния модел")
    def cmd_model(args: dict[str, Any]) -> int:
        name = args.get("_0", "") or args.get("name", "")
        if not name:
            print(config.get("liteLLM.model", "(не е зададен)"))
            return 0
        config.set("liteLLM.model", name)
        print(f"Модел: {name}")
        return 0

    @registry.register("clear", "Изчисти екрана")
    def cmd_clear(args: dict[str, Any]) -> int:
        import sys
        if sys.stdout.isatty():
            print("\033[2J\033[H", end="")
        return 0

    @registry.register("verbose", "Включи/изключи verbose изход")
    def cmd_verbose(args: dict[str, Any]) -> int:
        cur = bool(config.get("cli.verbose", False))
        new = not cur
        if "_0" in args:
            v = str(args["_0"]).lower()
            new = v in ("1", "true", "on", "yes")
        config.set("cli.verbose", new)
        print(f"verbose: {'on' if new else 'off'}")
        return 0

    @registry.register("local", "DEV: включи/изключи локални tools без Docker guard")
    def cmd_local(args: dict[str, Any]) -> int:
        cur = bool(config.get("cli.unsafe_local_tools", False))
        new = not cur
        if "_0" in args:
            v = str(args["_0"]).lower()
            new = v in ("1", "true", "on", "yes")
        config.set("cli.unsafe_local_tools", new)
        if new:
            print("WARNING: local tools ON. Tools run on host without Docker guard.")
        else:
            print("local tools: off (Docker guard)")
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
            print("Използване: /chat <prompt> или /chat prompt=...")
            return 1

        import json
        from runtimelib.agent import create_agent_loop
        from runtimelib.cli.render import Renderer

        as_json = bool(config.get("cli.json", False))
        no_stream = bool(config.get("cli.no_stream", False))
        verbose = bool(config.get("cli.verbose", False))
        events: list[dict[str, Any]] = []
        tokens: list[str] = []

        if as_json:
            def on_token(tok: str) -> None:
                if tok:
                    tokens.append(tok)
            def on_tool_start(name: str, a: dict) -> None:
                events.append({"type": "tool_start", "name": name, "args": {k: v for k, v in a.items() if not k.startswith("_")}})
            def on_tool_end(result: Any) -> None:
                events.append({"type": "tool_end", "ok": bool(getattr(result, "success", False)), "error": getattr(result, "error", None)})
        else:
            renderer = Renderer(verbose=verbose)
            on_token = (lambda t: tokens.append(t)) if no_stream else renderer.on_token
            def on_tool_start(name: str, a: dict) -> None:
                renderer.on_tool_start(name, a)
            def on_tool_end(result: Any) -> None:
                renderer.on_tool_end(result)

        loop = create_agent_loop(
            config,
            on_token=on_token,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
        )

        try:
            response = loop.run(prompt)
        except KeyboardInterrupt:
            if as_json:
                print(json.dumps({"ok": False, "error": "interrupted", "events": events}, ensure_ascii=False))
            else:
                renderer.error("прекъснато")
            return 130
        except Exception as e:
            if as_json:
                print(json.dumps({"ok": False, "error": str(e), "events": events}, ensure_ascii=False))
            else:
                renderer.error(str(e))
            return 1

        if as_json:
            out = {"ok": True, "model": config.get("liteLLM.model"), "response": response or "".join(tokens), "events": events}
            print(json.dumps(out, ensure_ascii=False))
        else:
            if no_stream:
                # renderer wasn't used for tokens; print collected/final
                text = response or "".join(tokens)
                if text:
                    print(text)
            else:
                renderer.final(response)
        return 0

    @registry.register("exit", "Изход от runtime", aliases=["quit", "q"])
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
    from runtimelib.cli import ansi

    history_file = Path.home() / ".config" / "runtime" / "history"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history = HistoryManager(history_file)
    interface = ReadlineInterface(history, completer=lambda text, state: completer(text, state, registry, config))

    model = config.get("liteLLM.model", "?")
    base = config.get("liteLLM.base_url", "?")
    print(ansi.bold("rex") + ansi.gray(f"  model={model}  endpoint={base}"))
    print(ansi.gray("Команди: /help /model /models /clear /doctor /exit  |  Ctrl+C прекъсва, Ctrl+D изход"))
    print(ansi.gray("─" * min(80, ansi.term_width())))

    while True:
        try:
            prompt = ansi.cyan("rex") + ansi.gray(" › ")
            line = interface.read(prompt).strip()
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            continue

        if not line:
            continue

        if line.startswith("/"):
            parts = line[1:].split(maxsplit=1)
            cmd = parts[0]
            arg_str = parts[1] if len(parts) > 1 else ""
            try:
                args = ArgumentParser.parse(arg_str)
                registry.execute(cmd, args)
            except KeyboardInterrupt:
                print()
            except Exception as e:
                print(ansi.red(f"Грешка: {e}"))
        else:
            try:
                registry.execute("chat", {"prompt": line})
            except KeyboardInterrupt:
                print()
            except Exception as e:
                print(ansi.red(f"Грешка: {e}"))

    return 0


_MODEL_CACHE: list[str] = []


def _get_model_ids(config: Config) -> list[str]:
    global _MODEL_CACHE
    if _MODEL_CACHE:
        return _MODEL_CACHE
    try:
        from runtimelib.llm import LiteLLMClient
        client = LiteLLMClient.from_config(config)
        _MODEL_CACHE = [m.get("id", "") for m in client.list_models() if m.get("id")]
    except Exception:
        _MODEL_CACHE = []
    return _MODEL_CACHE


def completer(text: str, state: int, registry: CommandRegistry, config: Config | None = None) -> str | None:
    # complete /model <name>
    if config is not None:
        try:
            import readline as _rl
            line = _rl.get_line_buffer()
        except Exception:
            line = text
        if line.startswith("/model "):
            prefix = line[len("/model "):]
            models = _get_model_ids(config)
            matches = [m for m in models if m.startswith(prefix)]
            if state < len(matches):
                return matches[state]
            return None
    commands = [f"/{cmd}" for cmd in registry.list_commands()]
    matches = [cmd for cmd in commands if cmd.startswith(text)]
    if state < len(matches):
        return matches[state]
    return None


if __name__ == "__main__":
    sys.exit(main())