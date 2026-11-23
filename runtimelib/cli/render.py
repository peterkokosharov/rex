# SPDX-License-Identifier: MIT
"""Terminal renderer for agent output (stdlib only)."""

from __future__ import annotations

import json
import sys
import textwrap
from typing import Any

from . import ansi


class Renderer:
    def __init__(self, stream=None, verbose: bool = False):
        self.stream = stream or sys.stdout
        self.verbose = verbose
        self.saw_tokens = False
        self.at_line_start = True

    def _write(self, text: str) -> None:
        self.stream.write(text)
        self.stream.flush()
        self.at_line_start = text.endswith("\n")

    def _nl(self) -> None:
        if not self.at_line_start:
            self._write("\n")

    def info(self, msg: str) -> None:
        self._nl()
        self._write(ansi.gray(msg) + "\n")

    def error(self, msg: str) -> None:
        self._nl()
        self._write(ansi.red(f"error: {msg}") + "\n")

    def warn(self, msg: str) -> None:
        self._nl()
        self._write(ansi.yellow(f"warn: {msg}") + "\n")

    def on_token(self, token: str) -> None:
        if not token:
            return
        self.saw_tokens = True
        self._write(token)

    def on_tool_start(self, name: str, args: dict[str, Any]) -> None:
        self._nl()
        summary = self._tool_summary(name, args)
        self._write(ansi.cyan(f"• {name}") + ansi.gray(f" {summary}") + "\n")

    def on_tool_end(self, result: Any) -> None:
        # tool results can be noisy; show short status
        ok = getattr(result, "success", False)
        err = getattr(result, "error", None)
        if ok:
            if self.verbose:
                data = getattr(result, "result", None)
                try:
                    txt = json.dumps(data, ensure_ascii=False)
                except Exception:
                    txt = str(data)
                txt = txt[:500] + ("..." if len(txt) > 500 else "")
                self._write(ansi.gray(f"  └ ok: {txt}") + "\n")
        else:
            msg = str(err) if err else "failed"
            hint = ""
            low = msg.lower()
            if "permission denied while trying to connect to the docker api" in low:
                hint = " (hint: docker group/newgrp, or /local on for dev)"
            elif "unable to find image 'rex-guard:latest'" in low:
                hint = " (hint: build guard image, or /local on for dev)"
            elif "path traversal attempt" in low:
                hint = " (hint: guard path policy; use workspace path or /local on for dev)"
            msg = msg[:300] + ("..." if len(msg) > 300 else "")
            self._write(ansi.red(f"  └ {msg}{hint}") + "\n")

    def final(self, response: str | None) -> None:
        if response and not self.saw_tokens:
            self._nl()
            self._write(response.rstrip() + "\n")
        elif self.saw_tokens and not self.at_line_start:
            self._write("\n")

    def _tool_summary(self, name: str, args: dict[str, Any]) -> str:
        a = {k: v for k, v in args.items() if not k.startswith("_")}
        if name == "bash":
            cmd = str(a.get("command", ""))
            cmd = " ".join(cmd.split())
            return cmd[:120]
        if name == "fileops":
            op = a.get("operation", "")
            path = a.get("path", "")
            return f"{op} {path}".strip()[:120]
        try:
            s = json.dumps(a, ensure_ascii=False)
        except Exception:
            s = str(a)
        return s[:120]
