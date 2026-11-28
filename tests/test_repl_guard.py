# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-5-5
# Licensed under MIT License

"""REPL: no silent stop after a failed or empty step, launch-command guard, /timeout."""

from __future__ import annotations

import io

import runtime.__main__ as cli
from kikolib.http import HttpTimeout  # importable once the engine put vendor/ on the path
from ai_runtime_rex.runtime.envconfig import env_config
from runtimelib.llm.client import ChatCompletionChunk


def test_launch_lines_are_recognised():
    assert cli.looks_like_launch_line(r"C:\Users\x\apps\ai-rex\ai-rex.cmd --timeout 600")
    assert cli.looks_like_launch_line('"C:\\apps\\ai-rex\\ai-rex.cmd" -m haiku')
    assert cli.looks_like_launch_line("ai-rex --timeout 600")
    assert cli.looks_like_launch_line("--timeout 600")
    assert not cli.looks_like_launch_line("explain what ai-rex does")
    assert not cli.looks_like_launch_line("look at src/ai-rex-notes.md")


def test_step_error_message_for_timeout_and_other_errors():
    cfg = env_config(port=1, timeout=60)
    msg = cli.step_error_message(HttpTimeout("read timed out"), cfg)
    assert "timed out after 60 s" in msg and "/timeout N" in msg and "conversation is kept" in msg
    assert cli.step_error_message(RuntimeError("HTTP 502"), cfg) == "HTTP 502 (the conversation is kept, type to continue)"


class _Llm:
    default_model = "m"

    def __init__(self, replies):
        self.replies = list(replies)

    def chat_completion(self, **kw):
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return iter([ChatCompletionChunk(choices=[{"delta": {"content": item}, "finish_reason": "stop"}])])


def _repl(monkeypatch, tmp_path, replies):
    monkeypatch.chdir(tmp_path)
    cfg = env_config(port=1, timeout=60)
    cfg.set("cli.unsafe_local_tools", True)
    registry = cli.CommandRegistry()
    cli.register_core_commands(registry, cfg)
    monkeypatch.setattr(cli, "_ASKER", None)
    from runtimelib.agent import create_agent_loop
    loop = create_agent_loop(cfg)
    loop.llm = _Llm(replies)
    monkeypatch.setattr(cli, "_REPL_LOOP", loop)
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    return registry, cfg, loop, out


def test_timeout_prints_one_clear_line_and_session_goes_on(monkeypatch, tmp_path):
    registry, _, loop, out = _repl(monkeypatch, tmp_path, [HttpTimeout("timed out"), "second answer"])
    assert registry.execute("chat", {"prompt": "first"}) == 1
    assert "timed out after 60 s" in out.getvalue() and "type to continue" in out.getvalue()
    assert registry.execute("chat", {"prompt": "again"}) == 0
    assert "second answer" in out.getvalue()
    users = [m.content for m in loop.state.messages if m.role == "user"]
    assert users[:1] == ["first"] and "again" in users  # the conversation was kept


def test_empty_turn_is_never_silent(monkeypatch, tmp_path):
    registry, _, _, out = _repl(monkeypatch, tmp_path, [""])
    registry.execute("chat", {"prompt": "hi"})  # first step empty -> error line, not silence
    assert out.getvalue().strip()


def test_empty_answer_after_tools_prints_a_note(monkeypatch, tmp_path):
    registry, _, loop, out = _repl(monkeypatch, tmp_path, [])

    def run(prompt):
        loop.on_token("Looking at files.")
        return ""
    monkeypatch.setattr(loop, "run", run)
    assert registry.execute("chat", {"prompt": "hi"}) == 0
    assert "without an answer" in out.getvalue()


def test_timeout_command_shows_and_sets(monkeypatch, tmp_path):
    registry, cfg, loop, out = _repl(monkeypatch, tmp_path, [])
    registry.execute("timeout", {})
    assert "timeout: 60 s" in out.getvalue()
    registry.execute("timeout", {"_0": "900"})
    assert cfg.get("liteLLM.timeout") == 900.0 and loop.llm.timeout == 900.0
    assert registry.execute("timeout", {"_0": "-1"}) == 1
