# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Argument parser for slash commands."""

from __future__ import annotations

import shlex
from typing import Any


class ArgumentParser:
    """Parse slash command arguments."""

    @staticmethod
    def parse(args_str: str) -> dict[str, Any]:
        """Parse command arguments string into dict.

        Supports:
        - key=value
        - key="quoted value"
        - key='single quoted'
        - positional arguments (as _0, _1, ...)
        - flags (--flag or -f becomes flag=true)
        """
        result: dict[str, Any] = {}
        positional = []

        if not args_str.strip():
            return result

        try:
            tokens = shlex.split(args_str)
        except ValueError:
            # Fallback for unclosed quotes
            tokens = args_str.split()

        for token in tokens:
            if token.startswith("--"):
                # Long flag
                flag = token[2:]
                if "=" in flag:
                    key, value = flag.split("=", 1)
                    result[key] = ArgumentParser._parse_value(value)
                else:
                    result[flag] = True
            elif token.startswith("-") and len(token) > 1:
                # Short flag(s)
                for ch in token[1:]:
                    result[ch] = True
            elif "=" in token:
                # key=value
                key, value = token.split("=", 1)
                result[key] = ArgumentParser._parse_value(value)
            else:
                positional.append(token)

        # Add positional args with numeric keys
        for i, val in enumerate(positional):
            result[f"_{i}"] = val

        return result

    @staticmethod
    def _parse_value(value: str) -> Any:
        """Parse a value string to appropriate type."""
        # Boolean
        if value.lower() in ("true", "false"):
            return value.lower() == "true"
        # Number
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            pass
        # JSON (dict/list)
        if (value.startswith("{") and value.endswith("}")) or \
           (value.startswith("[") and value.endswith("]")):
            import json
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                pass
        return value

    @staticmethod
    def format_help(command: str, args: dict[str, Any]) -> str:
        """Format command usage for help."""
        lines = [f"/{command}"]
        for key, value in args.items():
            if key.startswith("_"):
                lines.append(f"  <{value}>")
            elif value is True:
                lines.append(f"  --{key}")
            else:
                lines.append(f"  {key}={value}")
        return "\n".join(lines)