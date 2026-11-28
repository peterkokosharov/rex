# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License

"""Gate tests: rules, hooks, asker and the loop. Fake tools, no network."""

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from runtimelib.agent.gate import DenyAsker, PermissionGate, PermissionRequest
from runtimelib.agent.loop import AgentLoop
from runtimelib.config import Config
from runtimelib.hooks.commands import CommandHookRunner, parse_hooks
from runtimelib.llm import ToolCall
from runtimelib.permissions.rules import PermissionRules
from runtimelib.tools.base import Tool, ToolSchema


def _tool_msg(text):
    """Plain-text tool message -> {"success", "result", "error"} (the loop's "[result of ...]" header line first,
    failures then start with "ERROR: ")."""
    if text.startswith("[result of "):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.startswith("ERROR: "):
        return {"success": False, "result": None, "error": text[len("ERROR: "):]}
    return {"success": True, "result": text, "error": None}



class FakeTool(Tool):
    def __init__(self, name: str) -> None:
        super().__init__(name, "fake", ToolSchema(name, "fake", {"type": "object", "properties": {}}))
        self.calls: list[dict] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True}


class ScriptedAsker:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.requests: list[PermissionRequest] = []

    def ask(self, request):
        self.requests.append(request)
        return self.answers.pop(0) if self.answers else "deny"


class _Session:
    session_id = "s1"

    def append(self, *a, **k):
        pass


class GateBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def script(self, name: str, body: str) -> list[str]:
        path = self.dir / name
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return [sys.executable, str(path)]

    def gate(self, mode="guard", asker=None, perms=None, pre=None, post=None, on_error="deny"):
        rules = PermissionRules(perms or {}, str(self.dir), store_dir=self.dir / "store", persist=False)
        hooks = None
        if pre or post:
            hooks = CommandHookRunner(
                pre_tool=parse_hooks(pre or []), post_tool=parse_hooks(post or []),
                on_error=on_error, cwd=str(self.dir), mode=mode,
            )
        return PermissionGate(rules, hooks, asker, mode)


class ModeTable(GateBase):
    def test_modes(self):
        bash = {"command": "make"}
        read = {"operation": "read", "path": "a.txt"}
        for mode, bash_ok in (("guard", True), ("sandbox", False), ("local", False)):
            g = self.gate(mode)
            self.assertTrue(g.check("fileops", read).allowed, mode)
            self.assertEqual(g.check("bash", bash).allowed, bash_ok, mode)  # DenyAsker answers ask
        self.assertFalse(self.gate("guard").check("webfetch", {"url": "http://x.org"}).allowed)
        self.assertFalse(self.gate("guard").check("mcp__s__t", {}).allowed)

    def test_guard_default_can_be_ask(self):
        g = self.gate("guard", perms={"defaults": {"guard": "ask"}})
        self.assertFalse(g.check("bash", {"command": "make"}).allowed)

    def test_allow_rule_skips_ask_in_local(self):
        g = self.gate("local", perms={"allow": ["bash(make*)"]})
        self.assertTrue(g.check("bash", {"command": "make all"}).allowed)


class Precedence(GateBase):
    def test_deny_rule_beats_hook_allow_and_user(self):
        asker = ScriptedAsker("allow_once")
        pre = [{"command": self.script("allow.py", 'print(\'{"decision":"allow"}\')')}]
        g = self.gate("local", asker, perms={"deny": ["bash(rm *)"]}, pre=pre)
        d = g.check("bash", {"command": "rm x"})
        self.assertFalse(d.allowed)
        self.assertIn("rule bash(rm *)", d.reason)
        self.assertEqual(asker.requests, [])

    def test_hook_deny_beats_allow_rule(self):
        pre = [{"command": self.script("d.py", 'import sys; sys.stderr.write("nope"); sys.exit(2)')}]
        g = self.gate("local", perms={"allow": ["bash"]}, pre=pre)
        d = g.check("bash", {"command": "ls"})
        self.assertFalse(d.allowed)
        self.assertIn("nope", d.reason)

    def test_hook_allow_skips_ask(self):
        asker = ScriptedAsker("deny")
        pre = [{"command": self.script("a.py", 'print(\'{"decision":"allow"}\')')}]
        g = self.gate("local", asker, pre=pre)
        self.assertTrue(g.check("bash", {"command": "ls"}).allowed)
        self.assertEqual(asker.requests, [])

    def test_hook_ask_forces_ask_on_allowed_call(self):
        asker = ScriptedAsker("allow_once")
        pre = [{"command": self.script("k.py", 'print(\'{"decision":"ask"}\')')}]
        g = self.gate("guard", asker, pre=pre)
        self.assertTrue(g.check("bash", {"command": "ls"}).allowed)
        self.assertEqual(len(asker.requests), 1)

    def test_hook_crash_denies_unless_ignored(self):
        pre = [{"command": self.script("c.py", "import sys; sys.exit(1)")}]
        self.assertFalse(self.gate("guard", pre=pre).check("bash", {"command": "ls"}).allowed)
        self.assertTrue(self.gate("guard", pre=pre, on_error="ignore").check("bash", {"command": "ls"}).allowed)

    def test_internal_args_stripped_for_hooks_and_asker(self):
        out = self.dir / "payload.json"
        pre = [{"command": self.script("p.py", f"import sys; open({str(out)!r}, 'w').write(sys.stdin.read())")}]
        asker = ScriptedAsker("allow_once")
        g = self.gate("local", asker, pre=pre)
        g.check("bash", {"command": "ls", "_role": "CODER", "_agent_id": "x"})
        payload = json.loads(out.read_text())
        self.assertEqual(payload["args"], {"command": "ls"})
        self.assertEqual(asker.requests[0].args, {"command": "ls"})


class Asking(GateBase):
    def test_allow_always_remembered(self):
        asker = ScriptedAsker("allow_always")
        g = self.gate("local", asker)
        self.assertTrue(g.check("bash", {"command": "make"}).allowed)
        self.assertTrue(g.check("bash", {"command": "make"}).allowed)
        self.assertEqual(len(asker.requests), 1)

    def test_deny_answer_and_unknown_answer(self):
        g = self.gate("local", ScriptedAsker("deny", "banana"))
        self.assertFalse(g.check("bash", {"command": "a"}).allowed)
        self.assertFalse(g.check("bash", {"command": "b"}).allowed)

    def test_asker_exception_denies(self):
        class Boom:
            def ask(self, request):
                raise RuntimeError("x")
        self.assertFalse(self.gate("local", Boom()).check("bash", {"command": "a"}).allowed)

    def test_request_has_summary_and_detail(self):
        asker = ScriptedAsker("deny", "deny")
        g = self.gate("local", asker)
        g.check("bash", {"command": "echo hi\nls"})
        g.check("fileops", {"operation": "write", "path": "a.txt", "content": "hello"})
        r1, r2 = asker.requests
        self.assertEqual((r1.kind, r1.summary, r1.detail), ("exec", "bash: echo hi", "echo hi\nls"))
        self.assertEqual((r2.kind, r2.summary, r2.detail), ("write", "fileops write a.txt", "hello"))

    def test_bash_request_shows_env_and_cwd(self):
        asker = ScriptedAsker("deny")
        g = self.gate("guard", asker)
        d = g.check("bash", {"command": "make", "cwd": "sub", "env": {"LD_PRELOAD": "/tmp/x.so"}})
        self.assertFalse(d.allowed)  # env makes the guard-allowed call ask
        r = asker.requests[0]
        self.assertEqual(r.summary, "bash: make (cwd: sub) (env: LD_PRELOAD)")
        self.assertEqual(r.detail, "make\n\ncwd: sub\n\nenv:\n  LD_PRELOAD=/tmp/x.so")
        self.assertIn("environment", r.reason)

    def test_tool_describe_used_and_failure_falls_back(self):
        class Described(FakeTool):
            def describe(self, args):
                return "edit a.py", "DIFF"

        class Broken(FakeTool):
            def describe(self, args):
                raise ValueError("x")
        asker = ScriptedAsker("deny", "deny")
        g = self.gate("local", asker)
        g.check("edit", {"path": "a.py"}, Described("edit"))
        g.check("edit", {"path": "a.py"}, Broken("edit"))
        self.assertEqual((asker.requests[0].summary, asker.requests[0].detail), ("edit a.py", "DIFF"))
        self.assertTrue(asker.requests[1].summary.startswith("edit:"))

    def test_deny_asker_default(self):
        self.assertIsInstance(self.gate("local").asker, DenyAsker)


class LoopGate(GateBase):
    CFG = {"liteLLM": {"base_url": "http://x", "api_key": "k", "model": "m"}}

    def make_loop(self, tool, gate=None, asker=None, local=False):
        data = dict(self.CFG)
        if local:
            data["cli"] = {"unsafe_local_tools": True}
        return AgentLoop(Config(data), llm_client=object(), tools=[tool], gate=gate, asker=asker,
                         session_manager=_Session())

    def run_call(self, loop, name, args):
        tc = ToolCall(id="c1", type="function", function={"name": name, "arguments": json.dumps(args)})
        ended = []
        loop.on_tool_end = ended.append
        loop._execute_tool_calls([tc])
        return ended[0], _tool_msg(loop.state.messages[-1].content)

    def test_denied_call_never_executes(self):
        tool = FakeTool("bash")
        loop = self.make_loop(tool, self.gate("local"))
        res, msg = self.run_call(loop, "bash", {"command": "ls"})
        self.assertEqual(tool.calls, [])
        self.assertFalse(res.success)
        self.assertFalse(msg["success"])

    def test_allowed_call_executes_with_internal_args(self):
        tool = FakeTool("bash")
        loop = self.make_loop(tool, self.gate("guard"))
        res, _ = self.run_call(loop, "bash", {"command": "ls"})
        self.assertTrue(res.success)
        self.assertEqual(tool.calls[0]["_role"], "CODER")

    def test_asked_and_allowed_executes(self):
        tool = FakeTool("bash")
        loop = self.make_loop(tool, self.gate("local", ScriptedAsker("allow_once")))
        res, _ = self.run_call(loop, "bash", {"command": "ls"})
        self.assertTrue(res.success)
        self.assertEqual(len(tool.calls), 1)

    def test_post_hook_note_appended(self):
        tool = FakeTool("bash")
        post = [{"command": self.script("n.py", "print('formatted')")}]
        loop = self.make_loop(tool, self.gate("guard", post=post))
        res, _ = self.run_call(loop, "bash", {"command": "ls"})
        self.assertEqual(res.result["hook_note"], "formatted")
        self.assertTrue(res.result["ok"])

    def test_default_loop_denies_asks(self):
        tool = FakeTool("bash")
        loop = self.make_loop(tool, local=True)
        self.assertEqual(loop.gate.mode, "local")
        res, _ = self.run_call(loop, "bash", {"command": "ls"})
        self.assertFalse(res.success)
        self.assertEqual(tool.calls, [])

    def test_loop_asker_param(self):
        tool = FakeTool("bash")
        loop = self.make_loop(tool, asker=ScriptedAsker("allow_once"), local=True)
        res, _ = self.run_call(loop, "bash", {"command": "ls"})
        self.assertTrue(res.success)

    def _slow_hook(self, decision: str) -> tuple[list[dict], Path]:
        marker = self.dir / "hook-started"
        script = self.script("slow.py", f"""
            import json, sys, time
            sys.stdin.read()
            open({str(marker)!r}, 'w').close()
            time.sleep(1.0)
            print(json.dumps({{"decision": {decision!r}}}))
        """)
        return [{"command": script, "timeout": 10}], marker

    def _stop_during_hook(self, loop, marker, calls):
        import threading
        import time
        t = threading.Thread(target=loop._execute_tool_calls, args=(calls,))
        t.start()
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(marker.exists())
        loop.interrupt()
        t.join(15)
        self.assertFalse(t.is_alive())

    def test_stop_during_slow_pre_hook_cancels_allowed_call(self):
        from runtimelib.agent.context import CANCELLED_RESULT
        tool = FakeTool("bash")
        pre, marker = self._slow_hook("allow")
        loop = self.make_loop(tool, self.gate("guard", pre=pre))
        calls = [ToolCall(id=f"c{i}", type="function",
                          function={"name": "bash", "arguments": '{"command": "ls"}'}) for i in (1, 2)]
        self._stop_during_hook(loop, marker, calls)
        self.assertEqual(tool.calls, [])  # the gate said yes, but the stop wins
        self.assertTrue(loop.state.interrupted)
        tail = loop.state.messages[-2:]
        self.assertEqual([m.tool_call_id for m in tail], ["c1", "c2"])
        self.assertTrue(all(m.content == CANCELLED_RESULT for m in tail))

    def test_stop_during_slow_pre_hook_denies_question_without_asking(self):
        import io
        from runtimelib.cli.jsonl_bridge import JsonlBridge
        tool = FakeTool("bash")
        pre, marker = self._slow_hook("ask")
        out = io.StringIO()
        bridge = JsonlBridge(Config(dict(self.CFG)), loop_factory=lambda b: None, stream=io.StringIO(""), out=out)
        loop = self.make_loop(tool, self.gate("guard", asker=bridge.asker, pre=pre))
        bridge.loop = loop
        calls = [ToolCall(id="c1", type="function", function={"name": "bash", "arguments": '{"command": "ls"}'})]
        self._stop_during_hook(loop, marker, calls)
        self.assertEqual(tool.calls, [])
        self.assertNotIn('"permission"', out.getvalue())  # denied at once, never asked

    def test_mode_follows_local_toggle_on_live_loop(self):
        # /local on in the REPL flips the config key; the gate must follow at once.
        tool = FakeTool("bash")
        loop = self.make_loop(tool)
        self.assertEqual(loop.gate.mode, "guard")
        res, _ = self.run_call(loop, "bash", {"command": "ls"})
        self.assertTrue(res.success)  # guard default: allow
        loop.config.set("cli.unsafe_local_tools", True)
        self.assertEqual(loop.gate.mode, "local")
        res, _ = self.run_call(loop, "bash", {"command": "ls"})
        self.assertFalse(res.success)  # local: ask -> DenyAsker
        self.assertEqual(len(tool.calls), 1)
        loop.config.set("cli.unsafe_local_tools", False)
        self.assertEqual(loop.gate.mode, "guard")

    def test_hook_payload_gets_current_mode(self):
        out = self.dir / "modes.txt"
        script = self.script("m.py", f"""
            import json, sys
            with open({str(out)!r}, 'a') as f:
                f.write(json.load(sys.stdin)['mode'] + '\\n')
        """)
        cfg = Config({**self.CFG, "hooks": {"pre_tool": [{"command": script}]}})
        gate = PermissionGate.from_config(cfg, workspace=str(self.dir), persist=False)
        gate.check("fileops", {"operation": "read", "path": "a"})
        cfg.set("cli.unsafe_local_tools", True)
        gate.check("fileops", {"operation": "read", "path": "a"})
        self.assertEqual(out.read_text().split(), ["guard", "local"])

    def test_pinned_mode_detaches_config(self):
        cfg = Config(dict(self.CFG))
        gate = PermissionGate.from_config(cfg, workspace=str(self.dir), persist=False)
        gate.mode = "sandbox"
        cfg.set("cli.unsafe_local_tools", True)
        self.assertEqual(gate.mode, "sandbox")


if __name__ == "__main__":
    unittest.main()
