#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License

"""Runtime CLI entry point."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Optional

import runtimelib
from runtimelib import Config, get_config
from runtimelib.config.paths import migrate_legacy_config, set_quiet
from runtimelib.cli import (
    ArgumentParser,
    CommandRegistry,
    HistoryManager,
    ReadlineInterface,
    is_shell_line,
    print_shell_result,
    run_shell,
    shell_command,
)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-rex",
        description="Claude-like runtime for OpenAI-compatible models via LiteLLM proxy",
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + runtimelib.__version__)
    parser.add_argument("--config", type=Path, default=None, help="Path to config file")
    parser.add_argument("--env-file", type=Path, default=None, help="Path to .env file")
    parser.add_argument("-m", "--model", default=None, help="Model alias/id (env AI_REX_MODEL, default sonnet)")
    parser.add_argument("-i", "--input", default=None, dest="input_text", metavar="TEXT",
                        help="One-shot: prompt text (piped stdin / -f content is attached to it)")
    parser.add_argument("-f", "--input-file", default=None, metavar="PATH",
                        help="One-shot: read the prompt from PATH ('-' = stdin)")
    parser.add_argument("-o", "--output", default="-", metavar="PATH", help="Write the answer to PATH ('-' = stdout, default)")
    parser.add_argument("--append", action="store_true", help="Append to the --output file instead of overwriting")
    parser.add_argument("-p", "--port", type=int, default=None, help="Local gateway port (env AI_REX_PORT, default 4000)")
    parser.add_argument("--base-url", default=None, help="Gateway base URL, wins over --port (env AI_REX_BASE_URL)")
    parser.add_argument("--provider", choices=("auto", "openai", "anthropic", "gemini"), default=None,
                        help="API dialect (env AI_REX_PROVIDER, default auto = OpenAI-compatible)")
    parser.add_argument("--api-key-env", default=None, metavar="NAME",
                        help="Name of the environment variable that holds the API key (the key itself is never an argument)")
    parser.add_argument("--system", default=None, metavar="TEXT|@FILE", help="Extra system instructions")
    parser.add_argument("--profile", action="append", default=[], metavar="LAYERS",
                        help="Instruction layers, e.g. programming/java (repeatable or comma-separated; 'auto' = "
                             "detect from the workspace (default), 'base' = base layer only; env AI_REX_PROFILE)")
    parser.add_argument("--max-tokens", type=int, default=None, help="Maximum answer tokens")
    parser.add_argument("--temperature", type=float, default=None, help="Sampling temperature")
    parser.add_argument("--timeout", type=float, default=None, help="HTTP timeout in seconds (env AI_REX_TIMEOUT, default 600)")
    parser.add_argument("--no-tools", action="store_true", help="One-shot without any tools (plain chat)")
    parser.add_argument("--session", default=None, metavar="ID",
                        help="One-shot: keep the conversation in session ID (created if new; letters, digits, - _)")
    parser.add_argument("--continue", action="store_true", dest="continue_last",
                        help="One-shot: continue the latest session started in this folder")
    parser.add_argument("--non-interactive", action="store_true",
                        help="Never ask: a tool call that would need permission is denied (exit 3)")
    parser.add_argument("--cwd", "--workspace", default=None, dest="cwd", metavar="DIR", help="Run in DIR (the workspace)")
    parser.add_argument("--stream", action="store_true", help="Print the answer as it arrives (default)")
    parser.add_argument("--log-file", default=None, metavar="PATH", help="Write log lines to PATH instead of stderr")
    parser.add_argument("--log-format", choices=("text", "json"), default="text", help="Log line format")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument("--jsonl", "--ui-jsonl", action="store_true", dest="jsonl", help="JSON-lines stdio bridge for external UIs (e.g. Layka)")
    parser.add_argument("--acp", action="store_true", help="Agent Client Protocol server for editors (JetBrains AI Assistant, VS Code ACP clients)")
    parser.add_argument("--no-stream", action="store_true", help="Print the answer only when complete")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="More logging (-v tools, -vv debug)")
    parser.add_argument("-q", "--quiet", action="store_true", help="Errors only")
    parser.add_argument("--mcp-config", action="append", default=[], metavar="PATH",
                        help='MCP servers from a JSON file {"mcpServers": {...}} (repeatable; env AI_REX_MCP_CONFIG, '
                             'a list separated by the path separator)')
    parser.add_argument("--mcp", action="append", default=[], metavar="NAME=COMMAND",
                        help='Inline MCP server, e.g. --mcp "demo=python server.py --flag" or NAME=https://host/mcp (repeatable)')
    parser.add_argument("--no-mcp", action="store_true", help="Ignore all MCP servers (also those from env or --config)")
    parser.add_argument("--mcp-allow", action="append", default=[], metavar="PATTERN",
                        help="Allow MCP tools without asking: SERVER, SERVER__TOOL or a glob such as 'demo__read_*' (repeatable)")
    from ai_runtime_rex.core.mcp.cliconfig import add_cli_flags as add_mcp_flags
    add_mcp_flags(parser)  # --mcp-trust-readonly
    parser.add_argument("--shell", choices=("auto", "bash", "powershell", "cmd"), default=None,
                        help="Shell for the bash tool and ! commands (default auto: Git Bash on Windows, else "
                             "PowerShell, else cmd; bash elsewhere; env AI_REX_SHELL, config tools.shell)")
    parser.add_argument("--allow-buffer", default=None, metavar="N",
                        help="Size of the allow buffer in paths (0 = off, max 1000000; env AI_REX_ALLOW_BUFFER, "
                             "config permissions.allow_buffer_size, default 1024)")
    parser.add_argument("--allow-buffer-list", action="store_true",
                        help="Print the allow buffer of the workspace (paths, kind, last used) and exit")
    parser.add_argument("--unsafe-local-tools", "--local-tools", action="store_true", dest="unsafe_local_tools", help="DEV ONLY: run tools locally without Docker guard")
    parser.add_argument("prompt", nargs="*", help="Prompt to send to the agent (if omitted, starts interactive mode)")
    return parser


def main() -> int:
    # cp1252/cp1251 consoles crash on box-drawing glyphs; never fail on output encoding.
    from runtimelib.cli.jsonl_bridge import force_utf8_stdio
    force_utf8_stdio()
    argv = sys.argv[1:]
    protocol_mode = bool(argv) and (argv[0] == "acp" or any(a in ("--jsonl", "--ui-jsonl", "--acp") for a in argv))
    set_quiet(protocol_mode)  # deprecation notes only in REPL / one-shot mode
    env_only = not (protocol_mode or any(a in ("--config", "--env-file") for a in argv)
                    or (argv and argv[0] in ("acp", "skills", "profile")))
    if not env_only and not (argv and argv[0] in ("skills", "profile")):
        migrate_legacy_config()  # one-time copy ~/.config/runtime -> ~/.config/ai-rex (old dir is kept)
    # `ai-rex acp ...` is the form used in editor configs. Keep the old flat CLI
    # intact; this only takes over when "acp" is the very first argument.
    if argv and argv[0] == "acp":
        from runtimelib.acp import main as acp_main
        return acp_main(argv[1:])

    # `ai-rex skills [show <name>]` lists the SKILL.md skills; a normal prompt otherwise.
    if argv and argv[0] == "skills" and (len(argv) == 1 or argv[1] == "show"):
        from runtimelib.skills.cli import run_skills
        return run_skills(argv[1:], get_config())

    # `ai-rex profile [show]` lists the instruction layers (active ones marked) or prints the composed text.
    if argv and argv[0] == "profile" and (len(argv) == 1 or argv[1] == "show"):
        from ai_runtime_rex.core.profiles.cli import run_profile
        return run_profile(argv[1:], get_config())

    parser = create_parser()
    args = parser.parse_args(argv)
    if args.cwd:
        try:
            os.chdir(args.cwd)
        except OSError as exc:
            print(f"ai-rex: cannot use --cwd: {exc}", file=sys.stderr)
            return 2

    # Load config. Plain REPL / one-shot use the environment only (no file read or written, see
    # runtime.envconfig); --config/--env-file and the bridge modes (--jsonl, --acp) keep the file config.
    if args.config or args.env_file:
        config = Config.load(args.config, args.env_file)
    elif env_only:
        from ai_runtime_rex.runtime.envconfig import env_config
        try:
            config = env_config(model=args.model, port=args.port, base_url=args.base_url,
                                provider=args.provider, api_key_env=args.api_key_env, timeout=args.timeout)
        except ValueError as exc:
            print(f"ai-rex: {exc}", file=sys.stderr)
            return 2
    else:
        config = get_config()

    if args.model:
        config.set("liteLLM.model", args.model)
    if (args.port or args.base_url) and not env_only:
        config.set("liteLLM.base_url", args.base_url or f"http://127.0.0.1:{args.port}")
    if args.profile:
        config.set("agent.profile", ",".join(args.profile))
    if args.max_tokens:
        config.set("liteLLM.max_tokens", args.max_tokens)  # the native adapters' output limit, also in the REPL
    if env_only:
        apply_permission_store(config)
    try:
        apply_allow_buffer(config, args.allow_buffer, env_only=env_only)
    except ValueError as exc:
        print(f"ai-rex: {exc}", file=sys.stderr)
        return 2
    if args.allow_buffer_list:
        return print_allow_buffer(config)
    config.set("cli.json", bool(args.json))
    config.set("cli.no_stream", bool(args.no_stream))
    config.set("cli.verbose", bool(args.verbose))
    config.set("cli.quiet", bool(args.quiet))
    try:
        from ai_runtime_rex.core.mcp.cliconfig import McpConfigError, apply as apply_mcp
        apply_mcp(config, args.mcp_config, args.mcp, args.no_mcp, args.mcp_allow,
                  warn=lambda m: print(m, file=sys.stderr),
                  trust_readonly=bool(getattr(args, "mcp_trust_readonly", False)))
    except McpConfigError as exc:
        print(f"ai-rex: {exc}", file=sys.stderr)
        return 2
    if args.shell:  # flag > env AI_REX_SHELL > config file
        config.set("tools.shell", args.shell)
    elif os.environ.get("AI_REX_SHELL"):
        config.set("tools.shell", os.environ["AI_REX_SHELL"])
    tool_note = apply_tool_mode(config, flag=bool(args.unsafe_local_tools), env_only=env_only)
    if args.unsafe_local_tools and not (args.jsonl or args.acp):
        print("WARNING: --unsafe-local-tools enabled. Tools run on host without Docker guard.", file=sys.stderr)

    if args.acp:
        from runtimelib.acp import run_acp
        return run_acp(
            config,
            model=args.model,
            local_tools=args.unsafe_local_tools,
            config_path=args.config,
            env_file=args.env_file,
        )

    if args.jsonl:
        from runtimelib.cli import run_jsonl
        return run_jsonl(config)

    one_shot = (args.input_text is not None or args.input_file is not None
                or (not args.prompt and not _isatty(sys.stdin)))
    if one_shot:
        from runtime.oneshot import Options, run_oneshot
        asker = None if args.non_interactive else one_shot_asker(bool(args.json))
        opts = Options(text=args.input_text, input_file=args.input_file, output=args.output, append=args.append,
                       system=args.system, as_json=bool(args.json), buffered=bool(args.no_stream),
                       max_tokens=args.max_tokens, temperature=args.temperature, no_tools=args.no_tools, asker=asker,
                       verbosity=-1 if args.quiet else args.verbose, log_file=args.log_file,
                       log_format=args.log_format, session=args.session, continue_last=args.continue_last)
        return run_oneshot(config, opts, llm_factory=_llm_factory(config))

    # Register commands
    registry = CommandRegistry()
    register_core_commands(registry, config)

    from runtime.commands import register_runtime_commands
    register_runtime_commands(registry, config)

    if args.prompt:
        prompt = " ".join(args.prompt)
        if is_shell_line(prompt):
            result = run_shell(shell_command(prompt), timeout=config.get("cli.shell_timeout", 120), config=config)
            if args.json:
                print(json.dumps({
                    "ok": result.ok,
                    "shell": True,
                    "command": result.command,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit": result.exit_code,
                }, ensure_ascii=False))
            else:
                print_shell_result(result)
            return result.exit_code
        global _ASKER
        _ASKER = one_shot_asker(bool(args.json))
        return registry.execute("chat", {"prompt": prompt})

    return run_interactive(registry, config, tool_note=tool_note)


def apply_tool_mode(config: Config, flag: bool, env_only: bool, docker: Optional[bool] = None) -> Optional[str]:
    """Decide where tools run; returns a one-line note when the Docker-less fallback was taken.

    ``--local-tools`` -> local. An explicit ``cli.unsafe_local_tools`` (env ``AI_REX_CLI__UNSAFE_LOCAL_TOOLS``) or
    ``permissions.mode`` is kept. Otherwise, in the env-only modes (plain REPL, one-shot) without a ``docker``
    command, tools run locally behind the permission gate: reads are allowed, every write or command asks
    (and is denied when nobody can answer). Without this fallback every tool call failed on the missing guard.
    """
    if flag:
        config.set("cli.unsafe_local_tools", True)
        return None
    explicit = config.get("cli.unsafe_local_tools")
    if explicit is not None:
        config.set("cli.unsafe_local_tools", explicit is True or str(explicit).lower() in ("1", "true", "yes", "on"))
        return None
    config.set("cli.unsafe_local_tools", False)
    if not env_only or config.get("permissions.mode"):
        return None
    if docker is None:
        import shutil
        docker = shutil.which("docker") is not None
    if docker:
        return None
    config.set("cli.unsafe_local_tools", True)
    return "tools: local (Docker not found) - reads allowed, every write or command asks first"


def apply_permission_store(config: Config, environ: Any = None) -> None:
    """Env-only mode writes nothing outside the workspace: "allow always" answers last for the session, unless
    ``AI_REX_PERMISSIONS_FILE`` names a file that keeps them (an explicit ``permissions.persist`` is kept)."""
    env = os.environ if environ is None else environ
    path = env.get("AI_REX_PERMISSIONS_FILE")
    if path:
        config.set("permissions.store_file", path)
        config.set("permissions.persist", True)
    elif config.get("permissions.persist") is None:
        config.set("permissions.persist", False)


def apply_allow_buffer(config: Config, flag: Any = None, env_only: bool = True, environ: Any = None) -> None:
    """Set ``permissions.allow_buffer_size``: flag > ``AI_REX_ALLOW_BUFFER`` > config > 1024 (env-only mode).

    Outside env-only mode (``--config``, bridges) the buffer is on only when the flag, the variable or the config
    file sets it. Raises ValueError for a value outside 0..1000000.
    """
    from ai_runtime_rex.core.permissions.allow_buffer import DEFAULT_SIZE, ENV_SIZE, parse_size
    env = os.environ if environ is None else environ
    if flag is not None:
        config.set("permissions.allow_buffer_size", parse_size(flag))
    elif env.get(ENV_SIZE):
        try:
            config.set("permissions.allow_buffer_size", parse_size(env[ENV_SIZE]))
        except ValueError as exc:
            raise ValueError(f"{ENV_SIZE}: {exc}") from None
    elif config.get("permissions.allow_buffer_size") is not None:
        config.set("permissions.allow_buffer_size", parse_size(config.get("permissions.allow_buffer_size")))
    elif env_only:
        config.set("permissions.allow_buffer_size", DEFAULT_SIZE)


def current_allow_buffer(config: Config) -> Any:
    """The allow buffer of the running REPL loop, else the workspace's buffer from the config (or None)."""
    loop = _REPL_LOOP
    rules = getattr(getattr(loop, "gate", None), "rules", None)
    if getattr(rules, "buffer", None) is not None:
        return rules.buffer
    raw = config.get("permissions.allow_buffer_size")
    if raw is None:
        return None
    from ai_runtime_rex.core.permissions.allow_buffer import get_buffer, parse_size
    return get_buffer(str(Path.cwd()), parse_size(raw))


def print_allow_buffer(config: Config) -> int:
    """``--allow-buffer-list``: print the workspace's allow buffer and exit."""
    from ai_runtime_rex.core.permissions.allow_buffer import format_entries
    buf = current_allow_buffer(config)
    if buf is None:
        print("allow buffer: not configured (set --allow-buffer N or AI_REX_ALLOW_BUFFER)")
        return 0
    for warning in buf.warnings:
        print(warning, file=sys.stderr)
    for line in format_entries(buf):
        print(line)
    return 0


def run_allow_command(config: Config, text: str) -> int:
    """``/allow list | remove <path> | clear | size [N]`` with the raw argument text (Windows paths keep their
    backslashes)."""
    from ai_runtime_rex.core.permissions.allow_buffer import allow_command
    code, lines = allow_command(current_allow_buffer(config), text,
                                set_size=lambda n: config.set("permissions.allow_buffer_size", n))
    for line in lines:
        print(line)
    return code


def _llm_factory(config: Config) -> Any:
    """None for the OpenAI-compatible default (the loop builds its own client), else the provider adapter."""
    from ai_runtime_rex.runtime.providers import make_llm
    return lambda: make_llm(config)


# Bidi controls and zero-width characters can reorder or hide text on screen.
_INVISIBLE = (set(range(0x200B, 0x2010)) | set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))
              | {0xFEFF, 0x2028, 0x2029, 0x061C, 0x180E})


def _printable(text: Any) -> str:
    """``text`` with control characters (except newline and tab) shown as escapes.

    C0/C1 controls become ``\\xNN``; bidi controls and zero-width characters
    (U+200B-200F, U+202A-202E, U+2066-2069, U+FEFF), line/paragraph
    separators (U+2028, U+2029), U+061C and U+180E become ``\\uXXXX``.
    """
    out = []
    for ch in str(text):
        code = ord(ch)
        if code in _INVISIBLE:
            out.append(f"\\u{code:04x}")
        elif ch in "\n\t" or not (code < 32 or 0x7F <= code <= 0x9F):
            out.append(ch)
        elif ch == "\r":
            out.append("\\r")
        else:
            out.append(f"\\x{code:02x}")
    return "".join(out)


class TerminalAsker:
    """REPL asker: shows the pending tool call on stderr and reads y / a / n.

    Anything but an explicit yes (empty line, EOF, Ctrl+C) is a deny. Ctrl+C
    also stops the running request (``on_interrupt``). One-shot runs use it only
    when stdin and stderr are a terminal (see :func:`one_shot_asker`).
    """

    def __init__(self, input_fn: Any = None, out: Any = None, on_interrupt: Any = None) -> None:
        self._out = out if out is not None else sys.stderr
        # input() would print the question on stdout, which may be the answer file of a one-shot run.
        self._input = input_fn or self._read_line
        self._on_interrupt = on_interrupt

    def _read_line(self, prompt: str) -> str:
        self._out.write(prompt)
        self._out.flush()
        line = sys.stdin.readline()
        if not line:
            raise EOFError
        return line.rstrip("\r\n")

    def _read(self, prompt: str) -> str:
        """Read the answer; Ctrl+C raises KeyboardInterrupt even under the loop's SIGINT handler."""
        import signal
        import threading
        if threading.current_thread() is not threading.main_thread():
            return self._input(prompt)
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            return self._input(prompt)
        finally:
            signal.signal(signal.SIGINT, previous)

    def ask(self, request: Any) -> str:
        # Everything shown comes from the model or a tool: escape control
        # characters so ANSI sequences cannot rewrite or hide the prompt.
        print(f"\nPermission needed: {_printable(request.tool)}: {_printable(request.summary)}", file=self._out)
        if request.detail:
            print(_printable(request.detail), file=self._out)
        if request.reason:
            print(f"({_printable(request.reason)})", file=self._out)
        remember = getattr(request, "remember", "")
        folder = getattr(request, "remember_folder", "")
        if remember:
            print(f"('always' is remembered in the allow buffer: {_printable(remember)})", file=self._out)
        choices = "Allow? [y] once  [a] always  "
        if folder:
            where = "the whole workspace" if folder == "." else _printable(folder) + "/"
            choices += f"[f] always for {where}  "
        try:
            answer = self._read(choices + "[N] deny: ").strip().lower()
        except EOFError:
            print("", file=self._out)
            return "deny"
        except KeyboardInterrupt:
            print("", file=self._out)
            if callable(self._on_interrupt):
                try:
                    self._on_interrupt()
                except Exception:
                    pass
            return "deny"
        if answer in ("y", "yes"):
            return "allow_once"
        if answer in ("a", "always"):
            return "allow_always"
        if folder and answer in ("f", "folder"):
            return "allow_always_folder"
        return "deny"


_ASKER: Any = None  # set by run_interactive / one_shot_asker; None = deny


def _isatty(stream: Any) -> bool:
    try:
        return bool(stream is not None and stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def one_shot_asker(as_json: bool, stdin: Any = None, stderr: Any = None) -> Any:
    """Asker for ``ai-rex "prompt"``: the terminal when someone can answer, else None (deny).

    Someone can answer when stdin and stderr are both a terminal and the
    output is not ``--json`` (a script reads that).
    """
    stdin = sys.stdin if stdin is None else stdin
    stderr = sys.stderr if stderr is None else stderr
    if as_json or not (_isatty(stdin) and _isatty(stderr)):
        return None
    return TerminalAsker(on_interrupt=_interrupt_repl_loop)


def _interrupt_repl_loop() -> None:
    """Stop the request of the current REPL loop (used by the asker on Ctrl+C)."""
    loop = _REPL_LOOP
    if loop is not None and hasattr(loop, "interrupt"):
        loop.interrupt()
_LAST_CONTEXT: dict[str, Any] = {}
_DEFAULT_WINDOW_HINTED: set[str] = set()


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000 and n % 100_000 == 0:
        return f"{n / 1_000_000:g}M"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


def _on_context(status: dict[str, Any], quiet: bool = False) -> None:
    """Remember the newest context status; print the warning and the 32k hint once."""
    _LAST_CONTEXT.clear()
    _LAST_CONTEXT.update(status)
    if quiet:
        return
    model = status.get("model", "")
    if status.get("source") == "default" and model not in _DEFAULT_WINDOW_HINTED:
        _DEFAULT_WINDOW_HINTED.add(model)
        print(
            f"Context size of model {model} is unknown (default {_fmt_tokens(status['limit'])}); "
            f"set models.{model}.context_window in the configuration.",
            file=sys.stderr,
        )
    if status.get("warning"):
        print(
            f"Context {status['percent']}% of {_fmt_tokens(status['limit'])} (model {model}). "
            "If the work matters, switch to a model with a larger context: /model",
            file=sys.stderr,
        )


_LAUNCH_NAMES = ("ai-rex", "ai-rex.cmd", "ai-rex.exe", "ai-rex.bat", "ai-rex.sh")
LAUNCH_HINT = ("You are already inside ai-rex; this line was not sent to the model. To change the HTTP timeout "
               "use /timeout N, to change the model /model NAME; other flags need a restart. To send the line "
               "anyway, start it with a space or with /say.")
CONTINUE_NOTE = "the conversation is kept, type to continue"


_FLAG_ARITY: dict[str, int] = {}


def _flag_arity() -> dict[str, int]:
    """ai-rex option string -> number of values it takes (0 for switches)."""
    if not _FLAG_ARITY:
        for action in create_parser()._actions:
            takes = 0 if action.nargs == 0 else 1
            for opt in action.option_strings:
                _FLAG_ARITY[opt] = takes
    return _FLAG_ARITY


def _only_flags(words: list[str]) -> bool:
    """True when ``words`` are nothing but known ai-rex flags and their values (``--timeout 600 -m haiku``)."""
    arity = _flag_arity()
    i = 0
    while i < len(words):
        word = words[i]
        name = word.split("=", 1)[0]
        if name not in arity:
            return False
        i += 1 if "=" in word or arity[name] == 0 else 2
    return bool(words) and i == len(words)


def forced_text(line: str) -> Optional[str]:
    """The text of a ``/say TEXT`` line (sent to the model as it is), else None."""
    text = line.strip()
    if text == "/say" or text.startswith(("/say ", "/say\t")):
        return text[4:].strip()
    return None


def looks_like_launch_line(line: str) -> bool:
    """True for a REPL line that is an ai-rex command line (``...\\ai-rex.cmd --timeout 600``) or consists only
    of known ai-rex flags (``--timeout 600``). A line typed with a leading space is always sent."""
    if line[:1] in (" ", "\t"):
        return False
    text = line.strip()
    words = text.split()
    if text.startswith("-"):
        return _only_flags(words)
    first = words[0].strip("\"'") if words else ""
    name = first.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name not in _LAUNCH_NAMES:
        return False
    # "ai-rex keeps crashing, why?" is a question; a path, an extension, flags or nothing else is a command line.
    return name != "ai-rex" or "/" in first.replace("\\", "/") or len(words) == 1 or words[1].startswith("-")


def step_error_message(exc: BaseException, config: Any) -> str:
    """One line for a failed model step; a timeout says how to raise the limit."""
    from kikolib.http import HttpTimeout
    text = str(exc) or exc.__class__.__name__
    if isinstance(exc, (HttpTimeout, TimeoutError)) or "timed out" in text.lower():
        seconds = config.get("liteLLM.timeout") or 600
        return (f"model step timed out after {float(seconds):g} s (use /timeout N, or start with --timeout N / "
                f"AI_REX_TIMEOUT); {CONTINUE_NOTE}")
    return f"{text} ({CONTINUE_NOTE})"


EMPTY_TURN_NOTE = ("the model ended the turn without an answer (no text after the tool steps); "
                   f"{CONTINUE_NOTE}, e.g. 'continue'")


def register_core_commands(registry: CommandRegistry, config: Config) -> None:
    """Register core slash commands."""

    @registry.register("timeout", "Show or set the HTTP timeout per model step in seconds (/timeout 600)")
    def cmd_timeout(args: dict[str, Any]) -> int:
        value = args.get("_0") or args.get("seconds")
        current = config.get("liteLLM.timeout") or 600
        if not value:
            print(f"timeout: {float(current):g} s")
            return 0
        try:
            seconds = float(value)
            if not math.isfinite(seconds) or seconds <= 0:
                raise ValueError
        except (TypeError, ValueError):
            print("Usage: /timeout N   (seconds, > 0)")
            return 1
        config.set("liteLLM.timeout", seconds)
        loop = _REPL_LOOP
        if loop is not None:
            # A new client picks the value up; the conversation stays.
            from runtimelib.llm import LiteLLMClient
            loop.llm = _llm_factory(config)() or LiteLLMClient.from_config(config)
        print(f"timeout: {seconds:g} s")
        return 0

    @registry.register("allow", "Allow buffer: /allow list | remove <path> | clear | size [N]")
    def cmd_allow(args: dict[str, Any]) -> int:
        text = " ".join(str(args[k]) for k in sorted(k for k in args if k.startswith("_")))
        return run_allow_command(config, text)

    @registry.register("help", "Show help for commands")
    def cmd_help(args: dict[str, Any]) -> int:
        if args:
            cmd = args.get("_0", "") or args.get("command", "")
            if cmd in registry._help:
                print(f"/{cmd} — {registry._help[cmd]}")
            else:
                print(f"Unknown command: /{cmd}")
            return 0

        print("Available commands:")
        for name in registry.list_commands():
            print(f"  /{name:<20} — {registry._help[name]}")
        return 0

    @registry.register("config", "Show or change the configuration")
    def cmd_config(args: dict[str, Any]) -> int:
        import json
        if not args:
            print("Current configuration:")
            print(json.dumps(config.to_safe_dict(), indent=2, ensure_ascii=False))
            return 0

        from runtimelib.config.config import mask_value
        verb = args.get("_0")
        if verb == "show" or "show" in args:
            key = args.get("_1") if verb == "show" else args["show"]
            if not key:
                print("Usage: /config show KEY")
                return 1
            value = mask_value(key, config.get(key))
            print(f"{key} = {json.dumps(value, ensure_ascii=False)}")
            return 0

        if verb == "set":
            # /config set KEY=VALUE [KEY=VALUE ...]; values are already parsed (numbers, true/false, JSON-ish)
            pairs = {k: v for k, v in args.items() if not k.startswith("_")}
            if not pairs:
                print("Usage: /config set KEY=VALUE")
                return 1
            for key, value in pairs.items():
                config.set(key, value)
                print(f"Set {key} = {json.dumps(mask_value(key, value), ensure_ascii=False)} (this session only)")
            return 0

        print("Usage: /config [show KEY | set KEY=VALUE]")
        return 1

    @registry.register("models", "List the models of the gateway")
    def cmd_models(args: dict[str, Any]) -> int:
        from runtimelib.llm import LiteLLMClient
        client = LiteLLMClient.from_config(config)
        try:
            models = client.list_models()
        except Exception as e:
            print(f"Error: {e}")
            return 1
        if not models:
            print("No models available")
            return 0
        current = config.get("liteLLM.model", "")
        for m in models:
            mid = m.get("id", "")
            mark = "*" if mid == current else " "
            print(f"{mark} {mid}")
        return 0

    @registry.register("model", "Show or change the active model")
    def cmd_model(args: dict[str, Any]) -> int:
        name = args.get("_0", "") or args.get("name", "")
        if not name:
            print(config.get("liteLLM.model", "(not set)"))
            return 0
        config.set("liteLLM.model", name)
        global _REPL_LOOP
        if _REPL_LOOP is not None and hasattr(_REPL_LOOP, "llm"):
            _REPL_LOOP.llm.default_model = name
        print(f"Model: {name}")
        if _REPL_LOOP is not None and hasattr(_REPL_LOOP, "report_context"):
            # The new model has its own window: recompute the status at once.
            status = _REPL_LOOP.report_context()
            print(f"Context {status['percent']}% of {_fmt_tokens(status['limit'])} ({status['source']})")
        return 0

    @registry.register("clear", "Clear the screen")
    def cmd_clear(args: dict[str, Any]) -> int:
        import sys
        if sys.stdout.isatty():
            print("\033[2J\033[H", end="")
        return 0

    @registry.register("verbose", "Toggle verbose output")
    def cmd_verbose(args: dict[str, Any]) -> int:
        cur = bool(config.get("cli.verbose", False))
        new = not cur
        if "_0" in args:
            v = str(args["_0"]).lower()
            new = v in ("1", "true", "on", "yes")
        config.set("cli.verbose", new)
        print(f"verbose: {'on' if new else 'off'}")
        return 0

    @registry.register("local", "Toggle local tools (on the host, without the Docker guard)")
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

    @registry.register("reset", "New session/context (clears the current conversation)")
    def cmd_reset(args: dict[str, Any]) -> int:
        # Keep the loop: dropping it would leak its MCP server processes.
        if _REPL_LOOP is not None:
            _REPL_LOOP.reset_context()
        print("Context cleared.")
        return 0

    @registry.register("context", "Show the size of the current context")
    def cmd_context(args: dict[str, Any]) -> int:
        loop = _REPL_LOOP
        if loop is None:
            print("No active context.")
            return 0
        usage = loop.context_usage()
        pct = usage["used"] * 100 // max(1, usage["window"])
        print(
            f"messages: {usage['messages']}  tokens: ~{usage['used']} (overhead ~{usage['overhead']})  "
            f"window: {usage['window']}  auto-compact at: {usage['threshold']} ({pct}% used)  "
            f"turn: {usage['turn']}  model: {config.get('liteLLM.model')}  "
            f"timeout: {float(config.get('liteLLM.timeout') or 600):g} s"
        )
        print(shell_banner(config))
        return 0

    @registry.register("mcp", "List the MCP servers and their tools")
    def cmd_mcp(args: dict[str, Any]) -> int:
        for line in mcp_status_lines(config, _REPL_LOOP):
            print(line)
        return 0

    @registry.register("shell", "Run a local shell command (same as ! <command>)")
    def cmd_shell(args: dict[str, Any]) -> int:
        command = args.get("command", "") or " ".join(
            str(v) for k, v in sorted(args.items()) if k.startswith("_")
        )
        if not command:
            print("Usage: /shell <command> or ! <command>")
            return 1
        result = run_shell(shell_command("!" + command), timeout=config.get("cli.shell_timeout", 120), config=config)
        return print_shell_result(result)

    @registry.register("doctor", "Check the system state")
    def cmd_doctor(args: dict[str, Any]) -> int:
        print("🏥 Doctor Check")
        print("─" * 40)

        # Check config
        print(f"✓ Config: {config.get('liteLLM.base_url', 'not set')}")

        # Check LiteLLM
        from runtimelib.llm import LiteLLMClient
        try:
            client = LiteLLMClient.from_config(config)
            models = client.list_models()
            print(f"✓ LiteLLM: {len(models)} models available")
        except Exception as e:
            print(f"✗ LiteLLM: {e}")

        # Check Docker
        import subprocess
        try:
            result = subprocess.run(["docker", "version"], capture_output=True, timeout=5)
            if result.returncode == 0:
                print("✓ Docker: available")
            else:
                print("✗ Docker: not running")
        except Exception:
            print("✗ Docker: not installed")

        # Check kiko.db
        kiko_db = Path.home() / ".kiko" / "kiko.db"
        if kiko_db.exists():
            print(f"✓ kiko.db: {kiko_db}")
        else:
            print(f"✗ kiko.db: not found ({kiko_db})")

        return 0

    @registry.register("chat", "Send a message to the agent")
    def cmd_chat(args: dict[str, Any]) -> int:
        prompt = args.get("prompt", "") or args.get("_0", "")
        if not prompt:
            print("Usage: /chat <prompt> or /chat prompt=...")
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

        global _REPL_LOOP
        if _REPL_LOOP is None:
            _REPL_LOOP = create_agent_loop(
                config,
                llm_client=_llm_factory(config)(),  # None = OpenAI-compatible; else the anthropic/gemini adapter
                on_token=on_token,
                on_tool_start=on_tool_start,
                on_tool_end=on_tool_end,
                asker=_ASKER,
            )
            for loaded in getattr(_REPL_LOOP, "instruction_paths", None) or []:
                print(f"instructions: {loaded}", file=sys.stderr)
        loop = _REPL_LOOP
        loop.on_token = on_token
        loop.on_tool_start = on_tool_start
        loop.on_tool_end = on_tool_end
        loop.on_context = lambda status: _on_context(status, quiet=as_json)
        _LAST_CONTEXT.clear()

        try:
            response = loop.run(prompt)
        except KeyboardInterrupt:
            if as_json:
                print(json.dumps({"ok": False, "error": "interrupted", "events": events}, ensure_ascii=False))
            else:
                renderer.error("interrupted")
            return 130
        except Exception as e:
            if as_json:
                print(json.dumps({"ok": False, "error": str(e), "events": events}, ensure_ascii=False))
            else:
                renderer.error(step_error_message(e, config))
            return 1

        if as_json:
            out = {"ok": True, "model": config.get("liteLLM.model"), "response": response or "".join(tokens), "events": events}
            if _LAST_CONTEXT:
                out["context"] = dict(_LAST_CONTEXT)
            print(json.dumps(out, ensure_ascii=False))
        else:
            if no_stream:
                # renderer wasn't used for tokens; print collected/final
                text = response or "".join(tokens)
                if text:
                    print(text)
            else:
                renderer.final(response)
            if not (response or "").strip():
                renderer.warn(EMPTY_TURN_NOTE)  # never a silent stop
        return 0

    @registry.register("exit", "Exit ai-rex", aliases=["quit", "q"])
    def cmd_exit(args: dict[str, Any]) -> int:
        print("Bye.")
        sys.exit(0)

    @registry.register("skills", "Manage skills")
    def cmd_skills(args: dict[str, Any]) -> int:
        from runtimelib.skills.cli import run_skills
        return run_skills([str(args[k]) for k in sorted(args) if k.startswith("_")], config)

    @registry.register("session", "Manage sessions")
    def cmd_session(args: dict[str, Any]) -> int:
        print("Sessions: not implemented yet")
        return 0


def mcp_status_lines(config: Config, loop: Any) -> list[str]:
    """Lines for ``/mcp``: configured servers, and per connected server its tools (before the first message the
    servers are not started yet)."""
    servers = config.get("mcp.servers", {})
    names = [n for n, r in servers.items() if not (isinstance(r, dict) and r.get("enabled") is False)]         if isinstance(servers, dict) else []
    if not names:
        return ["No MCP servers configured (use --mcp-config PATH, --mcp NAME=COMMAND or AI_REX_MCP_CONFIG)."]
    tools = getattr(loop, "tools", None)
    if loop is None or getattr(loop, "_mcp_manager", None) is None or tools is None:
        return [f"MCP servers configured: {', '.join(names)} (they start with the first message)"]
    lines = []
    for name in names:
        mine = [t for t in tools.values() if getattr(t, "server", None) == name]
        if getattr(loop._mcp_manager, "get_client", lambda n: None)(name) is None:
            lines.append(f"{name}: not connected (failed to start, see the message at startup)")
            continue
        lines.append(f"{name}: connected, {len(mine)} tool(s)")
        for t in mine:
            mark = " [read-only]" if getattr(t, "read_only", False) else ""
            lines.append(f"  {t.name}{mark} - {str(t.description).splitlines()[0][:80] if t.description else ''}")
    return lines


def shell_banner(config: Config) -> str:
    """One line: which shell runs the bash tool and ! commands."""
    from ai_runtime_rex.core.tools.bash import resolve_shell, shell_choice
    choice = shell_choice(config)
    shell = resolve_shell(choice)
    if shell is None:
        return f"shell: {choice} - not found"
    return f"shell: {shell.label} ({shell.path})" + ("" if choice == "auto" else f"  [--shell {choice}]")


def run_interactive(registry: CommandRegistry, config: Config, tool_note: Optional[str] = None) -> int:
    """Run interactive REPL with readline support."""
    from runtimelib.cli import ansi

    global _ASKER
    _ASKER = TerminalAsker(on_interrupt=_interrupt_repl_loop)
    history_file = Path.home() / ".config" / "ai-rex" / "history"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history = HistoryManager(history_file)
    interface = ReadlineInterface(history, completer=lambda text, state: completer(text, state, registry, config))

    model = config.get("liteLLM.model", "?")
    base = config.get("liteLLM.base_url", "?")
    print(ansi.bold("ai-rex") + ansi.gray(f"  model={model}  endpoint={base}"))
    if tool_note:
        print(ansi.gray(tool_note))
    print(ansi.gray(shell_banner(config)))
    print(ansi.gray("Commands: /help /model /models /timeout /context /reset /shell /mcp /allow /doctor /exit  |  ! <command> runs locally  |  Ctrl+C interrupts, Ctrl+D exits"))
    print(ansi.gray("─" * min(80, ansi.term_width())))

    while True:
        try:
            prompt = ansi.cyan("ai-rex") + ansi.gray(" › ")
            raw = interface.read(prompt)
            line = raw.strip()
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            continue

        if not line:
            continue

        said = forced_text(line)
        if said is not None:
            if said:
                try:
                    registry.execute("chat", {"prompt": said})
                except KeyboardInterrupt:
                    print()
                except Exception as e:
                    print(ansi.red(f"Error: {e}"))
            continue

        if looks_like_launch_line(raw):
            print(ansi.yellow(LAUNCH_HINT))
            continue

        if is_shell_line(line):
            try:
                result = run_shell(shell_command(line), timeout=config.get("cli.shell_timeout", 120), config=config)
                print_shell_result(result)
            except Exception as e:
                print(ansi.red(f"Error: {e}"))
            continue

        if line.startswith("/"):
            parts = line[1:].split(maxsplit=1)
            cmd = parts[0]
            arg_str = parts[1] if len(parts) > 1 else ""
            if cmd == "allow":  # raw text: the generic parser would eat the backslashes of Windows paths
                try:
                    run_allow_command(config, arg_str)
                except Exception as e:
                    print(ansi.red(f"Error: {e}"))
                continue
            try:
                args = ArgumentParser.parse(arg_str)
                registry.execute(cmd, args)
            except KeyboardInterrupt:
                print()
            except Exception as e:
                print(ansi.red(f"Error: {e}"))
        else:
            try:
                registry.execute("chat", {"prompt": line})
            except KeyboardInterrupt:
                print()
            except Exception as e:
                print(ansi.red(f"Error: {e}"))

    return 0


_MODEL_CACHE: list[str] = []
_REPL_LOOP = None


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