#!/usr/bin/env python3

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-sonnet-5-5, claude-opus-5-5
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
    assert bridge.loop.stopped in (1, 2)  # stop, and bye if it lands while the request runs
    assert [e["text"] for e in events if e["type"] == "final"] == ["interrupted"]


def test_bye_during_request_interrupts_and_closes_asker():
    import threading

    running = threading.Event()

    class SlowLoop(FakeLoop):
        def __init__(self, bridge):
            super().__init__(bridge)
            self.released = threading.Event()

        def interrupt(self):
            super().interrupt()
            self.released.set()

        def run(self, prompt):
            running.set()
            self.released.wait(5)
            return "interrupted" if self.released.is_set() else "timed out"

    stream, out = PipeStream(), WaitOut()
    bridge = JsonlBridge(_config(), loop_factory=lambda b: SlowLoop(b), stream=stream, out=out)
    thread = threading.Thread(target=bridge.run)
    thread.start()
    stream.feed({"type": "chat", "text": "long task"})
    assert running.wait(5)
    stream.feed({"type": "bye"})
    thread.join(10)
    assert not thread.is_alive()
    events = out.lines
    assert bridge.loop.stopped == 1
    assert [e["text"] for e in events if e["type"] == "final"] == ["interrupted"]
    request = PermissionRequest(id="perm-9", tool="bash", kind="exec", summary="s")
    assert bridge.asker.ask(request) == "deny"  # closed: denied at once
    assert not any(e["type"] == "permission" for e in events)


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


def test_force_utf8_stdio_reconfigures_streams(monkeypatch):
    from runtimelib.cli import force_utf8_stdio

    raw_out = io.BytesIO()
    raw_in = io.BytesIO("\u043f\u0440\u0438\u0432\u0435\u0442\n".encode("utf-8"))
    fake_out = io.TextIOWrapper(raw_out, encoding="cp1251", newline="\n")
    fake_in = io.TextIOWrapper(raw_in, encoding="cp1251")
    fake_err = io.TextIOWrapper(io.BytesIO(), encoding="cp1251")
    monkeypatch.setattr(sys, "stdout", fake_out)
    monkeypatch.setattr(sys, "stdin", fake_in)
    monkeypatch.setattr(sys, "stderr", fake_err)
    force_utf8_stdio()
    assert fake_out.encoding == fake_in.encoding == fake_err.encoding == "utf-8"
    assert fake_in.readline() == "\u043f\u0440\u0438\u0432\u0435\u0442\n"
    print("\u0434\u0430", file=fake_out, flush=True)
    assert raw_out.getvalue() == "\u0434\u0430\n".encode("utf-8")


def test_force_utf8_stdio_ignores_plain_streams(monkeypatch):
    from runtimelib.cli import force_utf8_stdio

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    force_utf8_stdio()  # StringIO has no reconfigure: must not raise


# ---------- permission asker ----------

import queue
import threading

from runtimelib.agent.gate import PermissionRequest, PermissionGate
from runtimelib.permissions.rules import PermissionRules


class PipeStream:
    """stdin stand-in: lines are fed from the test; None closes it."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()

    def feed(self, obj) -> None:
        self._q.put(json.dumps(obj) + "\n")

    def close(self) -> None:
        self._q.put(None)

    def __iter__(self):
        return self

    def __next__(self):
        item = self._q.get(timeout=5)
        if item is None:
            raise StopIteration
        return item


class WaitOut:
    """stdout stand-in that lets the test wait for a message type."""

    def __init__(self) -> None:
        self.lines: list[dict] = []
        self._cv = threading.Condition()

    def write(self, text: str) -> int:
        for line in text.splitlines():
            with self._cv:
                self.lines.append(json.loads(line))
                self._cv.notify_all()
        return len(text)

    def flush(self) -> None:
        pass

    def wait_for(self, kind: str) -> dict:
        with self._cv:
            assert self._cv.wait_for(lambda: any(e["type"] == kind for e in self.lines), timeout=5)
            return next(e for e in self.lines if e["type"] == kind)


class AskingLoop(FakeLoop):
    """Asks one question through the bridge asker (like the gate would)."""

    def run(self, prompt: str) -> str:
        request = PermissionRequest(
            id="perm-1", tool="bash", kind="exec", summary="run ls", detail="ls -la",
            args={"command": "ls -la"}, rule=None, reason="",
        )
        self.answer = self.bridge.asker.ask(request)
        return f"answer={self.answer}"


def _drive(replies, timeout_cfg=None, close_stdin=False, stop=False):
    stream, out = PipeStream(), WaitOut()
    cfg = _config()
    if timeout_cfg is not None:
        cfg.set("permissions.ask_timeout", timeout_cfg)
    bridge = JsonlBridge(cfg, loop_factory=lambda b: AskingLoop(b), stream=stream, out=out)
    thread = threading.Thread(target=bridge.run)
    thread.start()
    stream.feed({"type": "chat", "text": "go"})
    permission = out.wait_for("permission")
    for reply in replies:
        stream.feed(reply)
    if stop:
        stream.feed({"type": "stop"})
    if close_stdin:
        stream.close()
    else:
        out.wait_for("final")
        stream.feed({"type": "bye"})
        stream.close()
    thread.join(10)
    assert not thread.is_alive()
    return permission, bridge, out.lines


def test_permission_round_trip_allow_once():
    permission, bridge, events = _drive([{"type": "permission_reply", "id": "perm-1", "decision": "allow_once"}])
    assert permission == {
        "type": "permission", "id": "perm-1", "tool": "bash", "kind": "exec", "summary": "run ls",
        "detail": "ls -la", "args": {"command": "ls -la"}, "rule": None, "reason": "",
    }
    assert bridge.loop.answer == "allow_once"
    assert {"type": "final", "text": "answer=allow_once"} in events


def test_permission_allow_always_and_garbage_decision():
    _, bridge, _ = _drive([{"type": "permission_reply", "id": "perm-1", "decision": "allow_always"}])
    assert bridge.loop.answer == "allow_always"
    _, bridge, _ = _drive([{"type": "permission_reply", "id": "perm-1", "decision": "sure"}])
    assert bridge.loop.answer == "deny"


def test_permission_unknown_id_is_ignored():
    _, bridge, _ = _drive([
        {"type": "permission_reply", "id": "other", "decision": "allow_once"},
        {"type": "permission_reply", "id": "perm-1", "decision": "deny"},
    ])
    assert bridge.loop.answer == "deny"


def test_permission_timeout_denies():
    _, bridge, events = _drive([], timeout_cfg=0.2)
    assert bridge.loop.answer == "deny"
    assert {"type": "final", "text": "answer=deny"} in events


def test_permission_closed_stdin_denies():
    _, bridge, _ = _drive([], close_stdin=True)
    assert bridge.loop.answer == "deny"


def test_permission_stop_denies_and_interrupts():
    _, bridge, _ = _drive([], stop=True)
    assert bridge.loop.answer == "deny"
    assert bridge.loop.stopped == 1


def test_gate_with_jsonl_asker_denies_and_allows():
    """The real gate: a deny reply blocks the call, allow_once lets it through."""
    for decision, allowed in (("deny", False), ("allow_once", True)):
        stream, out = PipeStream(), WaitOut()
        bridge = JsonlBridge(_config(), loop_factory=lambda b: FakeLoop(b), stream=stream, out=out)
        rules = PermissionRules({}, str(REPO_ROOT), persist=False)
        gate = PermissionGate(rules, None, bridge.asker, "sandbox")
        result: dict = {}
        t = threading.Thread(target=lambda: result.update(d=gate.check("bash", {"command": "rm -rf x"})))
        t.start()
        permission = out.wait_for("permission")
        bridge.asker.reply(permission["id"], decision)
        t.join(5)
        assert result["d"].allowed is allowed
        assert result["d"].source == "user"


def test_asker_denies_at_once_after_interrupt():
    class InterruptedLoop(FakeLoop):
        def interrupt_requested(self):
            return True

    out = WaitOut()
    bridge = JsonlBridge(_config(), loop_factory=lambda b: InterruptedLoop(b), stream=PipeStream(), out=out)
    bridge._ensure_loop()
    request = PermissionRequest(id="perm-1", tool="bash", kind="exec", summary="s")
    assert bridge.asker.ask(request) == "deny"
    assert out.lines == []  # no permission event: nobody is asked after a stop
    assert bridge.asker._pending == {}


def test_terminal_asker_ctrl_c_denies_and_interrupts():
    from runtime.__main__ import TerminalAsker

    hits = []

    def fake_input(prompt):
        raise KeyboardInterrupt

    asker = TerminalAsker(input_fn=fake_input, out=io.StringIO(), on_interrupt=lambda: hits.append(1))
    request = PermissionRequest(id="perm-1", tool="bash", kind="exec", summary="run ls", detail="ls")
    assert asker.ask(request) == "deny"
    assert hits == [1]


def test_terminal_asker_answers():
    from runtime.__main__ import TerminalAsker

    request = PermissionRequest(id="perm-1", tool="bash", kind="exec", summary="run ls", detail="ls")

    def asker(answer):
        def fake_input(prompt):
            if isinstance(answer, BaseException):
                raise answer
            return answer
        return TerminalAsker(input_fn=fake_input, out=io.StringIO())

    assert asker("y").ask(request) == "allow_once"
    assert asker(" A ").ask(request) == "allow_always"
    assert asker("n").ask(request) == "deny"
    assert asker("").ask(request) == "deny"
    assert asker("whatever").ask(request) == "deny"
    assert asker(EOFError()).ask(request) == "deny"
    assert asker(KeyboardInterrupt()).ask(request) == "deny"
