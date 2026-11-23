# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""Tests for the hook command runner. Tiny Python hook scripts, no network."""

from __future__ import annotations

import json
import sys
import textwrap
import time
import unittest
from pathlib import Path
import tempfile

from runtimelib.hooks.commands import (
    CommandHookRunner,
    HookCommand,
    parse_hooks,
)


class HookTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def script(self, name: str, body: str) -> list[str]:
        path = self.dir / name
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return [sys.executable, str(path)]

    def runner(self, pre=(), post=(), **kw) -> CommandHookRunner:
        return CommandHookRunner(pre_tool=pre, post_tool=post, cwd=str(self.dir), **kw)


class PreHookTests(HookTestBase):
    def test_exit0_no_output_is_no_opinion(self):
        cmd = self.script("ok.py", "import sys; sys.stdin.read()")
        r = self.runner(pre=[HookCommand("*", tuple(cmd))]).run_pre("bash", {"command": "ls"})
        self.assertIsNone(r.decision)
        self.assertFalse(r.error)

    def test_exit2_denies_with_stderr_reason(self):
        cmd = self.script("d.py", "import sys; sys.stderr.write('nope, secret'); sys.exit(2)")
        r = self.runner(pre=[HookCommand("*", tuple(cmd))]).run_pre("bash")
        self.assertEqual(r.decision, "deny")
        self.assertEqual(r.reason, "nope, secret")
        self.assertFalse(r.error)

    def test_json_decisions(self):
        for dec in ("allow", "deny", "ask"):
            cmd = self.script(
                f"j_{dec}.py",
                f"import json; print(json.dumps({{'decision': '{dec}', 'reason': 'because'}}))",
            )
            r = self.runner(pre=[HookCommand("*", tuple(cmd))]).run_pre("edit")
            self.assertEqual(r.decision, dec)
            self.assertEqual(r.reason, "because")

    def test_json_without_decision_is_no_opinion(self):
        cmd = self.script("n.py", "print('{}')")
        r = self.runner(pre=[HookCommand("*", tuple(cmd))]).run_pre("edit")
        self.assertIsNone(r.decision)

    def test_other_exit_follows_on_error_deny_default(self):
        cmd = self.script("e1.py", "import sys; sys.stderr.write('boom'); sys.exit(1)")
        r = self.runner(pre=[HookCommand("*", tuple(cmd))]).run_pre("bash")
        self.assertEqual(r.decision, "deny")
        self.assertTrue(r.error)
        self.assertIn("exit 1", r.reason)
        self.assertIn("boom", r.reason)

    def test_other_exit_on_error_ignore(self):
        cmd = self.script("e1.py", "import sys; sys.exit(1)")
        r = self.runner(pre=[HookCommand("*", tuple(cmd))], on_error="ignore").run_pre("bash")
        self.assertIsNone(r.decision)

    def test_invalid_on_error_falls_back_to_deny(self):
        cmd = self.script("e1.py", "import sys; sys.exit(1)")
        r = self.runner(pre=[HookCommand("*", tuple(cmd))], on_error="whatever").run_pre("bash")
        self.assertEqual(r.decision, "deny")

    def test_timeout_deny_and_ignore(self):
        cmd = self.script("slow.py", "import time; time.sleep(30)")
        hook = HookCommand("*", tuple(cmd), timeout=0.5)
        start = time.monotonic()
        r = self.runner(pre=[hook]).run_pre("bash")
        self.assertLess(time.monotonic() - start, 15)
        self.assertEqual(r.decision, "deny")
        self.assertTrue(r.error)
        self.assertIn("timed out", r.reason)
        r2 = self.runner(pre=[hook], on_error="ignore").run_pre("bash")
        self.assertIsNone(r2.decision)

    def test_missing_executable_is_error(self):
        hook = HookCommand("*", ("definitely-not-a-real-binary-xyz",))
        r = self.runner(pre=[hook]).run_pre("bash")
        self.assertEqual(r.decision, "deny")
        self.assertTrue(r.error)

    def test_malformed_json_is_error(self):
        cmd = self.script("bad.py", "print('not json')")
        r = self.runner(pre=[HookCommand("*", tuple(cmd))]).run_pre("bash")
        self.assertEqual(r.decision, "deny")
        self.assertTrue(r.error)

    def test_unknown_decision_is_error(self):
        cmd = self.script("u.py", "print('{\"decision\": \"maybe\"}')")
        r = self.runner(pre=[HookCommand("*", tuple(cmd))]).run_pre("bash")
        self.assertTrue(r.error)

    def test_payload_on_stdin_and_cwd(self):
        out = self.dir / "seen.json"
        cmd = self.script("rec.py", f"""
            import json, os, sys
            data = json.load(sys.stdin)
            data["_cwd"] = os.getcwd()
            open({str(out)!r}, "w", encoding="utf-8").write(json.dumps(data))
        """)
        runner = self.runner(pre=[HookCommand("*", tuple(cmd))], session="s1", mode="local")
        runner.run_pre("bash", {"command": "ls", "n": 1})
        seen = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(seen["event"], "pre_tool")
        self.assertEqual(seen["tool"], "bash")
        self.assertEqual(seen["args"], {"command": "ls", "n": 1})
        self.assertEqual(seen["session"], "s1")
        self.assertEqual(seen["mode"], "local")
        self.assertEqual(Path(seen["_cwd"]).resolve(), self.dir.resolve())

    def test_match_filters_tools(self):
        cmd = self.script("d.py", "import sys; sys.exit(2)")
        runner = self.runner(pre=[HookCommand("bash|edit", tuple(cmd))])
        self.assertEqual(runner.run_pre("edit").decision, "deny")
        self.assertEqual(runner.run_pre("bash").decision, "deny")
        self.assertIsNone(runner.run_pre("fileops").decision)

    def test_match_glob(self):
        h = HookCommand("mcp__*", ("x",))
        self.assertTrue(h.matches("mcp__github__list"))
        self.assertFalse(h.matches("bash"))
        self.assertTrue(HookCommand("", ("x",)).matches("anything"))
        self.assertTrue(HookCommand("*", ("x",)).matches("anything"))

    def test_strictest_decision_wins(self):
        allow = self.script("a.py", "print('{\"decision\": \"allow\"}')")
        ask = self.script("k.py", "print('{\"decision\": \"ask\", \"reason\": \"hmm\"}')")
        deny = self.script("d.py", "import sys; sys.stderr.write('no'); sys.exit(2)")
        mk = lambda c: HookCommand("*", tuple(c))
        self.assertEqual(self.runner(pre=[mk(allow)]).run_pre("x").decision, "allow")
        r = self.runner(pre=[mk(allow), mk(ask)]).run_pre("x")
        self.assertEqual((r.decision, r.reason), ("ask", "hmm"))
        r = self.runner(pre=[mk(allow), mk(ask), mk(deny)]).run_pre("x")
        self.assertEqual((r.decision, r.reason), ("deny", "no"))

    def test_no_hooks_no_opinion(self):
        self.assertIsNone(self.runner().run_pre("bash").decision)


class PostHookTests(HookTestBase):
    def test_stdout_returned_as_note(self):
        out = self.dir / "post.json"
        cmd = self.script("p.py", f"""
            import json, sys
            d = json.load(sys.stdin)
            open({str(out)!r}, "w", encoding="utf-8").write(json.dumps(d))
            print("formatted")
        """)
        runner = self.runner(post=[HookCommand("edit", tuple(cmd))])
        note = runner.run_post("edit", {"path": "a.py"}, ok=False, error="bad", result_preview="xx")
        self.assertEqual(note, "formatted")
        seen = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(seen["event"], "post_tool")
        self.assertFalse(seen["ok"])
        self.assertEqual(seen["error"], "bad")
        self.assertEqual(seen["result_preview"], "xx")
        self.assertEqual(runner.run_post("bash"), "")  # no match

    def test_note_capped_at_2kb(self):
        cmd = self.script("big.py", "print('x' * 10000)")
        note = self.runner(post=[HookCommand("*", tuple(cmd))]).run_post("edit")
        self.assertLessEqual(len(note), 2048 + 3)
        self.assertTrue(note.endswith("..."))

    def test_failure_never_raises(self):
        crash = self.script("c.py", "import sys; sys.exit(3)")
        note = self.runner(post=[HookCommand("*", tuple(crash))]).run_post("edit")
        self.assertEqual(note, "")  # non-zero exit with no stdout: silent
        slow = self.script("s.py", "import time; time.sleep(30)")
        note = self.runner(post=[HookCommand("*", tuple(slow), timeout=0.5)]).run_post("edit")
        self.assertIn("timed out", note)
        missing = HookCommand("*", ("definitely-not-a-real-binary-xyz",))
        self.assertIn("cannot run", self.runner(post=[missing]).run_post("edit"))


class ConfigTests(unittest.TestCase):
    def test_from_config(self):
        runner = CommandHookRunner.from_config({
            "pre_tool": [{"match": "bash|edit", "command": ["python", "c.py"], "timeout": 5}],
            "post_tool": [{"command": ["ruff", "format"]}],
            "on_error": "ignore",
        }, cwd="/w", session="s", mode="guard")
        self.assertEqual(runner.on_error, "ignore")
        self.assertEqual(runner.pre_tool[0].timeout, 5.0)
        self.assertEqual(runner.pre_tool[0].command, ("python", "c.py"))
        self.assertEqual(runner.post_tool[0].match, "*")
        self.assertTrue(runner.has_hooks)

    def test_from_config_empty_or_bad(self):
        for cfg in (None, {}, "junk", {"pre_tool": "x", "on_error": 5}):
            runner = CommandHookRunner.from_config(cfg)
            self.assertFalse(runner.has_hooks)
            self.assertEqual(runner.on_error, "deny")

    def test_parse_skips_bad_entries(self):
        hooks = parse_hooks([
            "str", {"command": "not-a-list"}, {"command": []}, {"command": [1]},
            {"command": ["ok"], "timeout": "abc"}, {"command": ["ok2"], "timeout": -1},
        ])
        self.assertEqual([h.command for h in hooks], [("ok",), ("ok2",)])
        self.assertTrue(all(h.timeout == 10.0 for h in hooks))


if __name__ == "__main__":
    unittest.main()
