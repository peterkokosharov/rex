#!/usr/bin/env python3

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Tests for the JSON-lines stdio bridge used by the C++ Turbo Vision UI."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from runtimelib.cli import JsonlBridge
from runtimelib.config import Config

REPO_ROOT = Path(__file__).parent.parent


class FakeLoop:
    """Minimal agent loop: records prompts and streams two tokens per reply."""

    def __init__(self, bridge, reply: str = "answer") -> None:
        self.bridge = bridge
        self.reply = reply
        self.llm = SimpleNamespace(default_model="initial")
        self.messages: list[str] = []
        self.reset_calls = 0
        self.stopped = 0

    def interrupt(self) -> None:
        self.stopped += 1

    def run(self, prompt: str) -> str:
        self.messages.append(prompt)
        self.bridge.on_token(self.reply[:3])
        self.bridge.on_token(self.reply[3:])
        return self.reply

    def reset_context(self) -> None:
        self.messages = []
        self.reset_calls += 1


def _config() -> Config:
    return Config({
        "liteLLM": {
            "base_url": "http://dummy:4000",
            "api_key": "dummy",
            "model": "gemini-lite",
        }
    })


def _run(lines: str, config: Config | None = None, factory=None):
    out = io.StringIO()
    cfg = config or _config()
    bridge = JsonlBridge(
        cfg,
        loop_factory=factory or (lambda b: FakeLoop(b)),
        stream=io.StringIO(lines),
        out=out,
    )
    rc = bridge.run()
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    return rc, events, bridge, cfg


def test_ready_and_bye_are_framed():
    rc, events, _, _ = _run('{"type":"bye"}\n')
    assert rc == 0
    assert events[0] == {"type": "ready", "model": "gemini-lite"}
    assert events[-1] == {"type": "bye"}


def test_chat_streams_tokens_then_final():
    rc, events, _, _ = _run('{"type":"chat","text":"hi"}\n{"type":"bye"}\n')
    assert rc == 0
    assert [e["type"] for e in events] == [
        "ready", "token", "token", "final", "bye",
    ]
    assert "".join(e["text"] for e in events if e["type"] == "token") == "answer"
    assert events[3] == {"type": "final", "text": "answer"}


def test_context_survives_two_chat_requests():
    _, events, bridge, _ = _run(
        '{"type":"chat","text":"first"}\n'
        '{"type":"chat","text":"second"}\n'
        '{"type":"bye"}\n'
    )
    assert bridge.loop.messages == ["first", "second"]
    assert [e["text"] for e in events if e["type"] == "final"] == [
        "answer", "answer",
    ]


def test_reset_emits_reset_ok_and_clears_context():
    _, events, bridge, _ = _run(
        '{"type":"chat","text":"first"}\n'
        '{"type":"reset"}\n'
        '{"type":"bye"}\n'
    )
    assert bridge.loop.reset_calls == 1
    assert bridge.loop.messages == []
    assert {"type": "reset_ok"} in events


def test_model_and_command_switch_model():
    _, events, bridge, cfg = _run(
        '{"type":"model","model":"gpt-test"}\n'
        '{"type":"command","name":"model","value":"claude-test"}\n'
        '{"type":"bye"}\n'
    )
    assert cfg.get("liteLLM.model") == "claude-test"
    assert bridge.loop.llm.default_model == "claude-test"
    assert [e for e in events if e["type"] == "model_ok"] == [
        {"type": "model_ok", "model": "gpt-test"},
        {"type": "model_ok", "model": "claude-test"},
    ]


def test_loop_error_emits_error_and_keeps_bridge_alive():
    def broken(bridge):
        loop = FakeLoop(bridge)

        def run(prompt):
            raise RuntimeError("boom")

        loop.run = run
        return loop

    _, events, _, _ = _run(
        '{"type":"chat","text":"x"}\n'
        '{"type":"chat","text":"y"}\n'
        '{"type":"bye"}\n',
        factory=broken,
    )
    assert {"type": "error", "message": "boom"} in events
    assert events[-1] == {"type": "bye"}


def test_stop_reaches_running_request():
    _, events, bridge, _ = _run('{"type":"stop"}\n{"type":"bye"}\n')
    assert bridge.loop.stopped == 1
    assert events[-1] == {"type": "bye"}


def test_stop_interrupts_a_running_request():
    import threading

    class SlowLoop(FakeLoop):
        def __init__(self, bridge):
            super().__init__(bridge)
            self.released = threading.Event()

        def interrupt(self):
            super().interrupt()
            self.released.set()

        def run(self, prompt):
            self.messages.append(prompt)
            self.released.wait(2)
            return "interrupted" if self.released.is_set() else "timed out"

    _, events, bridge, _ = _run(
        '{"type":"chat","text":"long task"}\n'
        '{"type":"stop"}\n'
        '{"type":"bye"}\n',
        factory=lambda b: SlowLoop(b),
    )
    assert bridge.loop.messages == ["long task"]
    assert bridge.loop.stopped == 1
    assert [e["text"] for e in events if e["type"] == "final"] == ["interrupted"]


def test_malformed_line_emits_error():
    _, events, _, _ = _run('not-json\n{"type":"bye"}\n')
    assert any(e["type"] == "error" for e in events)
    assert events[-1] == {"type": "bye"}


def test_cli_jsonl_mode_stdout_is_pure_json():
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("RUNTIME_"):
            env.pop(key)
    env["RUNTIME_LITELLM__BASE_URL"] = "http://dummy:4000"
    env["RUNTIME_LITELLM__API_KEY"] = "dummy"
    result = subprocess.run(
        [sys.executable, "-m", "runtime", "--jsonl", "--local-tools"],
        cwd=REPO_ROOT, env=env,
        input='{"type":"model","model":"gemini-lite"}\n{"type":"bye"}\n',
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[0]["type"] == "ready"
    assert {"type": "model_ok", "model": "gemini-lite"} in events
    assert events[-1] == {"type": "bye"}
