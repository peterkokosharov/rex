# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License

"""MCP servers from the ``ai-rex`` command line, the environment and ``mcpServers`` JSON files.

Order (later wins per server name): servers already in the config, ``AI_REX_MCP_CONFIG`` files (a list separated by
``os.pathsep``), ``--mcp-config`` files, ``--mcp NAME=COMMAND`` inline servers. ``--no-mcp`` drops all of them.
The result is written to ``mcp.servers`` (the section ``load_mcp_tools`` reads); ``--mcp-allow`` adds allow rules.

File format (the one Claude Code and other clients use)::

    {"mcpServers": {"name": {"command": "prog", "args": ["a"], "env": {"K": "v"}},
                    "web":  {"type": "http", "url": "https://host/mcp", "headers": {"Authorization": "..."}}}}

``${VAR}`` and ``${VAR:-default}`` inside strings are expanded from the environment (an unset ``${VAR}`` without a
default becomes "" with one warning on stderr). Nothing runs through a shell.

Server names: letters, digits, '-' and '_', but no '__' and no '_' at either end, so ``mcp__<server>__<tool>`` splits
one way only (an allow rule for server ``x`` must never cover a server ``x_``).

``--mcp-trust-readonly`` sets ``mcp.trust_readonly`` (default false): only then may the agent loop run tools that a
server itself annotates ``readOnlyHint`` without asking.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ENV_CONFIG = "AI_REX_MCP_CONFIG"
ENV_STARTUP = "AI_REX_MCP_STARTUP_TIMEOUT"
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_QUOTES = "\"'"


class McpConfigError(ValueError):
    """A bad --mcp / --mcp-config argument (one line, for the user)."""


def _stderr_warn(message: str) -> None:
    print(message, file=sys.stderr)


def _expand(value: Any, environ: Mapping[str, str], unset: set[str] | None = None) -> Any:
    """Expand ${VAR} / ${VAR:-default}; names of unset variables without a default go into ``unset``."""
    if isinstance(value, str):
        def one(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            if name in environ:
                return environ[name]
            if default is None and unset is not None:
                unset.add(name)
            return default if default is not None else ""
        return _VAR.sub(one, value)
    if isinstance(value, list):
        return [_expand(v, environ, unset) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v, environ, unset) for k, v in value.items()}
    return value


def valid_server_name(name: str) -> bool:
    """Letters, digits, '-', '_'; no '__' and no '_' at either end (``mcp__<server>__<tool>`` must split once)."""
    return (isinstance(name, str) and bool(_NAME.match(name)) and "__" not in name
            and not name.startswith("_") and not name.endswith("_"))


def add_cli_flags(parser: Any) -> None:
    """Add the flags this module owns beyond the existing --mcp* ones (for runtime.__main__)."""
    parser.add_argument("--mcp-trust-readonly", action="store_true", dest="mcp_trust_readonly",
                        help="Run MCP tools that their server annotates readOnlyHint without asking "
                             "(the server's own claim; off by default)")


def split_command(text: str) -> list[str]:
    """Split a command line into words without a shell (quotes and, on POSIX, backslashes are honoured)."""
    try:
        words = shlex.split(text, posix=(os.name != "nt"))
    except ValueError as exc:
        raise McpConfigError(f"cannot parse command {text!r}: {exc}") from exc
    if os.name == "nt":  # non-POSIX mode keeps the quotes: strip one matching pair per word
        words = [w[1:-1] if len(w) >= 2 and w[0] == w[-1] and w[0] in _QUOTES else w for w in words]
    return words


def parse_inline(spec: str) -> tuple[str, dict[str, Any]]:
    """``NAME=COMMAND ARGS...`` or ``NAME=https://host/mcp`` -> (name, raw server dict)."""
    name, sep, rest = spec.partition("=")
    name, rest = name.strip(), rest.strip()
    if not sep or not name or not rest:
        raise McpConfigError(f"--mcp expects NAME=COMMAND [ARGS...], got {spec!r}")
    if not valid_server_name(name):
        raise McpConfigError(f"--mcp: bad server name {name!r} (letters, digits, '-' and single '_' inside only)")
    if re.match(r"^https?://\S+$", rest, re.IGNORECASE):
        return name, {"type": "http", "url": rest}
    words = split_command(rest)
    if not words:
        raise McpConfigError(f"--mcp {name}: empty command")
    return name, {"command": words}


def parse_file(path: str | os.PathLike[str], environ: Mapping[str, str] | None = None,
               warn: Any = None, warned: set[str] | None = None) -> dict[str, dict[str, Any]]:
    """Read a ``{"mcpServers": {...}}`` file into ``mcp.servers`` entries.

    An unset ``${VAR}`` without a default is reported once per name (``warn``, else stderr; ``warned`` holds the
    names already reported across files).
    """
    env = os.environ if environ is None else environ
    warn = warn or _stderr_warn
    warned = set() if warned is None else warned
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise McpConfigError(f"cannot read MCP config {path}: {exc.strerror or exc}") from exc
    except ValueError as exc:  # JSONDecodeError and bad encoding
        raise McpConfigError(f"MCP config {path} is not valid JSON: {exc}") from exc
    servers = data.get("mcpServers", data.get("servers")) if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise McpConfigError(f'MCP config {path} has no "mcpServers" object')
    out: dict[str, dict[str, Any]] = {}
    for name, raw in servers.items():
        if not isinstance(raw, dict):
            raise McpConfigError(f"MCP config {path}: server {name!r} is not an object")
        if not valid_server_name(str(name)):
            raise McpConfigError(f"MCP config {path}: bad server name {name!r} "
                                 "(letters, digits, '-' and single '_' inside only)")
        unset: set[str] = set()
        raw = _expand(raw, env, unset)
        for var in sorted(unset - warned):
            warned.add(var)
            warn(f"ai-rex: MCP config {path}: server {name!r}: ${{{var}}} is not set; using an empty value")
        entry: dict[str, Any] = {}
        kind = str(raw.get("type") or raw.get("transport") or "").lower()
        if kind in ("sse", "websocket", "ws"):
            raise McpConfigError(f"MCP config {path}: server {name!r}: transport {kind!r} is not supported "
                                 "(stdio and streamable http only)")
        if raw.get("url") or kind in ("http", "streamable-http", "streamable_http"):
            entry["url"] = raw.get("url")
            entry["type"] = "http"
            if isinstance(raw.get("headers"), dict):
                entry["headers"] = raw["headers"]
        else:
            if kind not in ("", "stdio"):
                raise McpConfigError(f"MCP config {path}: server {name!r}: unknown type {kind!r}")
            command = raw.get("command")
            args = raw.get("args") or []
            if isinstance(command, str) and command:
                words = [command]
            elif isinstance(command, list) and command:
                words = [str(c) for c in command]
            else:
                raise McpConfigError(f"MCP config {path}: server {name!r} has no command or url")
            if not isinstance(args, list):
                raise McpConfigError(f"MCP config {path}: server {name!r}: args must be a list")
            entry["command"] = words + [str(a) for a in args]
            if isinstance(raw.get("env"), dict):
                entry["env"] = raw["env"]
        if raw.get("disabled") is True or raw.get("enabled") is False:
            entry["enabled"] = False
        for src in ("timeout", "startup_timeout", "startupTimeout"):
            if raw.get(src) is not None:
                entry["startup_timeout" if src.startswith("startup") else "timeout"] = raw[src]
        out[str(name)] = entry
    return out


def env_paths(environ: Mapping[str, str] | None = None) -> list[str]:
    raw = (os.environ if environ is None else environ).get(ENV_CONFIG, "")
    return [p for p in raw.split(os.pathsep) if p.strip()]


def allow_rule(pattern: str) -> str:
    """``server__tool``, ``server`` or ``mcp__server__tool*`` -> a rule on the full tool name."""
    p = pattern.strip()
    if not p:
        raise McpConfigError("--mcp-allow: empty pattern")
    if not p.startswith("mcp__"):
        p = "mcp__" + p
    server, sep, _tool = p[len("mcp__"):].partition("__")
    if not sep and not p.endswith("*"):
        p += "__*"  # a bare server name allows all of its tools
    # The server part must be a valid name (a glob only when it is the whole rest, e.g. mcp__*): with names
    # that cannot start or end with '_', mcp__x__* never matches a tool of another server such as "x_".
    if not (valid_server_name(server) or server == "*" or (server.endswith("*") and valid_server_name(server[:-1]))):
        raise McpConfigError(f"--mcp-allow: bad server name in {pattern!r} "
                             "(letters, digits, '-' and single '_' inside only)")
    return p


def apply(config: Any, mcp_configs: Sequence[str] = (), inline: Sequence[str] = (), no_mcp: bool = False,
          allow: Sequence[str] = (), environ: Mapping[str, str] | None = None,
          warn: Any = None, trust_readonly: bool = False) -> list[str]:
    """Write the effective MCP servers into ``config``; returns the server names that will be started.

    Raises :class:`McpConfigError` for a bad flag. Unreadable files named by the environment only warn.
    """
    env = os.environ if environ is None else environ
    config.set("mcp.trust_readonly", bool(trust_readonly) or config.get("mcp.trust_readonly", False) is True)
    if no_mcp:
        config.set("mcp.servers", {})
        return []
    warned: set[str] = set()
    existing = config.get("mcp.servers", {})
    servers: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    for path in env_paths(env):
        try:
            servers.update(parse_file(path, env, warn, warned))
        except McpConfigError as exc:
            if warn:
                warn(f"ai-rex: {ENV_CONFIG}: {exc}")
    for path in mcp_configs:
        servers.update(parse_file(path, env, warn, warned))
    for spec in inline:
        name, raw = parse_inline(spec)
        servers[name] = raw
    startup = env.get(ENV_STARTUP)
    if startup:
        try:
            value = float(startup)
        except ValueError:
            value = 0.0
        if value > 0:
            for raw in servers.values():
                if isinstance(raw, dict):
                    raw.setdefault("startup_timeout", value)
    if servers:
        config.set("mcp.servers", servers)
    rules = [allow_rule(a) for a in allow]
    if rules:
        current = config.get("permissions.allow", [])
        config.set("permissions.allow", list(current) + rules if isinstance(current, list) else rules)
    return [n for n, r in servers.items() if not (isinstance(r, dict) and r.get("enabled") is False)]
