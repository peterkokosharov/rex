# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5
# Licensed under MIT License
"""``ai-rex`` one-shot mode: prompt from -i / -f / stdin, answer to stdout or a file, logs to stderr.

Exit codes: 0 ok, 1 model/runtime error, 2 usage error, 3 a tool call was denied by the permission rules,
130 interrupted.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from runtimelib.agent import create_agent_loop

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_DENIED, EXIT_INTERRUPTED = 0, 1, 2, 3, 130
LOG = logging.getLogger("ai-rex")


@dataclass
class Options:
    text: Optional[str] = None            # -i
    input_file: Optional[str] = None      # -f ('-' = stdin)
    output: str = "-"                     # -o ('-' = stdout)
    append: bool = False
    system: Optional[str] = None          # --system TEXT | @file
    as_json: bool = False
    buffered: bool = False                # --no-stream: print only when complete
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    no_tools: bool = False
    asker: Any = None
    verbosity: int = 0                    # -v count (-q = -1)
    log_file: Optional[str] = None
    log_format: str = "text"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname.lower(),
                           "msg": record.getMessage()}, ensure_ascii=False)


def setup_logging(verbosity: int, log_file: Optional[str], log_format: str, stderr: Any = None) -> None:
    """Logs go to stderr (never stdout) unless --log-file is given."""
    level = logging.ERROR if verbosity < 0 else (logging.WARNING, logging.INFO, logging.DEBUG)[min(verbosity, 2)]
    if log_file:
        handler: logging.Handler = logging.FileHandler(log_file, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stderr if stderr is None else stderr)
    fmt = _JsonFormatter() if log_format == "json" else logging.Formatter("ai-rex: %(levelname)s: %(message)s")
    handler.setFormatter(fmt)
    LOG.handlers[:] = [handler]
    LOG.setLevel(level)
    LOG.propagate = False


class _Tuned:
    """Wraps the LLM client to set temperature / max_tokens (the loop passes neither)."""

    def __init__(self, llm: Any, temperature: Optional[float], max_tokens: Optional[int]) -> None:
        self._llm, self._t, self._m = llm, temperature, max_tokens

    def chat_completion(self, *args: Any, **kw: Any) -> Any:
        if self._t is not None:
            kw["temperature"] = self._t
        if self._m is not None:
            kw["max_tokens"] = self._m
        return self._llm.chat_completion(*args, **kw)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)


def read_text_arg(value: str) -> str:
    """``@path`` reads that file, anything else is literal text."""
    if value.startswith("@") and len(value) > 1:
        return Path(value[1:]).read_text(encoding="utf-8-sig")
    return value


def read_stream(stdin: Any = None) -> str:
    stdin = sys.stdin if stdin is None else stdin
    try:
        return stdin.read()
    except (OSError, ValueError):
        return ""


def piped_stdin(stdin: Any = None) -> str:
    """Stdin content when it is a pipe/file, '' for a terminal."""
    stdin = sys.stdin if stdin is None else stdin
    try:
        if stdin is None or stdin.isatty():
            return ""
    except (OSError, ValueError):
        return ""
    return read_stream(stdin)


def build_prompt(text: Optional[str], attached: str) -> str:
    attached = attached.strip("\n")
    if text and attached:
        return f"{text}\n\n{attached}"
    return text or attached


def run_oneshot(config: Any, opts: Options, *, llm_factory: Any = None, stdin: Any = None, stdout: Any = None, stderr: Any = None) -> int:
    stdout = sys.stdout if stdout is None else stdout
    setup_logging(opts.verbosity, opts.log_file, opts.log_format, stderr)
    try:
        if opts.input_file == "-":
            attached = read_stream(stdin)
        elif opts.input_file:
            attached = Path(opts.input_file).read_text(encoding="utf-8-sig")
        else:
            attached = piped_stdin(stdin)  # alone it is the prompt, with -i it is attached
        system = read_text_arg(opts.system) if opts.system else None
    except OSError as exc:
        LOG.error("cannot read input: %s", exc)
        return EXIT_USAGE
    prompt = build_prompt(opts.text, attached)
    if not prompt:
        LOG.error("nothing to do: give -i TEXT, -f FILE, or pipe text on stdin")
        return EXIT_USAGE

    sink: Any = stdout
    if opts.output != "-":
        try:
            sink = open(opts.output, "a" if opts.append else "w", encoding="utf-8", newline="")
        except OSError as exc:
            LOG.error("cannot open output file: %s", exc)
            return EXIT_USAGE
    live = not opts.buffered and not opts.as_json
    written: list[str] = []
    denied: list[str] = []
    tools_used: list[str] = []

    def on_token(tok: str) -> None:
        if tok:
            written.append(tok)
            if live:
                sink.write(tok)
                sink.flush()

    def on_tool_start(name: str, args: dict) -> None:
        tools_used.append(name)
        LOG.info("tool: %s", name)
        LOG.debug("tool args: %s", {k: v for k, v in args.items() if not k.startswith("_")})

    def on_tool_end(result: Any) -> None:
        err = getattr(result, "error", None)
        if err and str(err).startswith("Permission denied"):
            denied.append(str(err))
            LOG.warning("denied: %s", err)

    started = time.monotonic()
    code = EXIT_OK
    response = ""
    error: Optional[str] = None
    try:
        kw: dict[str, Any] = {"tool_names": []} if opts.no_tools else {}
        llm = llm_factory() if llm_factory else None
        if opts.temperature is not None or opts.max_tokens is not None:
            from runtimelib.llm import LiteLLMClient
            llm = _Tuned(llm or LiteLLMClient.from_config(config), opts.temperature, opts.max_tokens)
        LOG.debug("model=%s endpoint=%s", config.get("liteLLM.model"), config.get("liteLLM.base_url"))
        loop = create_agent_loop(config, llm_client=llm, on_token=on_token, on_tool_start=on_tool_start,
                                 on_tool_end=on_tool_end, asker=opts.asker, **kw)
        if system:
            loop.system_prompt += "\n\n" + system
            loop.system_prompt_no_tools += "\n\n" + system
        response = loop.run(prompt) or ""
    except KeyboardInterrupt:
        code = EXIT_INTERRUPTED
    except Exception as exc:  # noqa: BLE001 - one line for the user, not a traceback
        error = str(exc)
        LOG.error("%s", exc)
        code = EXIT_ERROR
    text = "".join(written) or response
    try:
        if opts.as_json:
            sink.write(json.dumps({"ok": code == EXIT_OK, "model": config.get("liteLLM.model"), "response": text,
                                   "tools": tools_used, "denied": denied, "error": error,
                                   "seconds": round(time.monotonic() - started, 3)}, ensure_ascii=False) + "\n")
        elif code != EXIT_ERROR or text:
            if not live:
                sink.write(text)
            elif not written and response:
                sink.write(response)
            if text and not text.endswith("\n"):
                sink.write("\n")
        sink.flush()
    finally:
        if sink is not stdout:
            sink.close()
    if code == EXIT_OK and denied:
        code = EXIT_DENIED
    return code
