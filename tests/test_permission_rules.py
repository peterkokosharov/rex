# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License

"""Tests for the configurable permission rule engine."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtimelib.permissions.base import PermissionResult as R
from runtimelib.permissions.rules import (
    PermissionRules,
    RuleParseError,
    normalize_path,
    parse_rule,
    split_bash,
    store_path,
)

WS = "/work/proj"

CONFIG = {
    "allow": [
        "bash(pytest *)", "bash(git status*)", "bash(ls*)", "fileops(read:**)",
        "edit(src/**)", "webfetch(domain:docs.python.org)",
    ],
    "ask": ["bash(git push*)"],
    "deny": ["bash(rm -rf *)", "edit(**/.env)", "fileops(write:**/.env)", "mcp__github__delete_*"],
}


def rules(cfg=None, **kw):
    kw.setdefault("persist", False)
    return PermissionRules(CONFIG if cfg is None else cfg, WS, **kw)


def bash(cmd):
    return {"command": cmd}


class ParseTests(unittest.TestCase):
    def test_parse_forms(self):
        r = parse_rule("bash(pytest *)", "allow")
        self.assertEqual((r.tool, r.spec, r.decision), ("bash", "pytest *", R.ALLOW))
        r = parse_rule("mcp__github__delete_*", R.DENY)
        self.assertEqual((r.tool, r.spec), ("mcp__github__delete_*", None))
        self.assertEqual(parse_rule("bash(echo (a))", "ask").spec, "echo (a)")

    def test_parse_errors(self):
        for bad in ("", "  ", "bash()", "(x)"):
            with self.assertRaises(RuleParseError, msg=bad):
                parse_rule(bad, "allow")
        with self.assertRaises(RuleParseError):
            parse_rule("bash", "maybe")

    def test_bad_config_rules_are_skipped(self):
        pr = rules({"allow": ["bash()", "bash(ls*)"]})
        self.assertEqual(len(pr.allow), 1)
        self.assertEqual(len(pr.errors), 1)


class PrecedenceTests(unittest.TestCase):
    def ev(self, tool, args, mode="guard", cfg=None):
        return rules(cfg).evaluate(tool, args, mode)

    def test_table(self):
        cases = [
            ("bash", bash("pytest -q"), "local", R.ALLOW),
            ("bash", bash("pytest"), "local", R.ALLOW),          # trailing " *" matches bare
            ("bash", bash("rm -rf build"), "guard", R.DENY),
            ("bash", bash("git push origin x"), "guard", R.ASK),
            ("bash", bash("git status"), "local", R.ALLOW),
            ("bash", bash("make"), "guard", R.ALLOW),            # guard default allow
            ("bash", bash("make"), "sandbox", R.ASK),
            ("bash", bash("make"), "local", R.ASK),
            ("fileops", {"operation": "read", "path": "a/b.py"}, "local", R.ALLOW),
            ("fileops", {"operation": "write", "path": ".env"}, "guard", R.DENY),
            ("fileops", {"operation": "write", "path": "sub/.env"}, "guard", R.DENY),
            ("fileops", {"operation": "write", "path": "x.py"}, "local", R.ASK),
            ("edit", {"path": "src/a/b.py"}, "local", R.ALLOW),
            ("edit", {"path": "tests/a.py"}, "local", R.ASK),
            ("edit", {"path": "src/.env"}, "local", R.DENY),     # deny beats allow
            ("webfetch", {"url": "https://docs.python.org/3/"}, "local", R.ALLOW),
            ("webfetch", {"url": "https://a.docs.python.org/x"}, "guard", R.ALLOW),
            ("webfetch", {"url": "https://evildocs.python.org.example.com/"}, "guard", R.ASK),
            ("webfetch", {"url": "https://example.com/"}, "guard", R.ASK),
            ("mcp__github__delete_repo", {}, "guard", R.DENY),
            ("mcp__github__list", {}, "guard", R.ASK),
            ("git", {"operation": "status"}, "local", R.ALLOW),  # read kind
            ("git", {"operation": "commit", "message": "m"}, "local", R.ASK),
            ("git", {"operation": "commit", "message": "m"}, "guard", R.ALLOW),
        ]
        for tool, args, mode, want in cases:
            with self.subTest(tool=tool, args=args, mode=mode):
                self.assertEqual(self.ev(tool, args, mode).decision, want)

    def test_deny_beats_allow_and_ask_beats_allow(self):
        cfg = {"allow": ["bash"], "ask": ["bash(git *)"], "deny": ["bash(git push*)"]}
        self.assertEqual(self.ev("bash", bash("git push"), "guard", cfg).decision, R.DENY)
        self.assertEqual(self.ev("bash", bash("git log"), "guard", cfg).decision, R.ASK)
        self.assertEqual(self.ev("bash", bash("ls"), "local", cfg).decision, R.ALLOW)

    def test_local_default_ignores_config(self):
        cfg = {"defaults": {"guard": "allow", "sandbox": "allow", "local": "allow"}}
        self.assertEqual(self.ev("bash", bash("x"), "local", cfg).decision, R.ASK)
        self.assertEqual(self.ev("bash", bash("x"), "sandbox", cfg).decision, R.ALLOW)
        self.assertEqual(self.ev("bash", bash("x"), "weird", cfg).decision, R.ASK)

    def test_guard_default_configurable(self):
        cfg = {"defaults": {"guard": "ask"}}
        self.assertEqual(self.ev("bash", bash("x"), "guard", cfg).decision, R.ASK)
        self.assertEqual(self.ev("bash", bash("x"), "guard", {}).decision, R.ALLOW)

    def test_local_allow_rule_skips_ask(self):
        v = self.ev("bash", bash("pytest -q"), "local")
        self.assertEqual((v.decision, v.source, v.rule), (R.ALLOW, "allow", "bash(pytest *)"))

    def test_read_tools_allowed_everywhere(self):
        for mode in ("guard", "sandbox", "local"):
            self.assertEqual(self.ev("load_skill", {}, mode, {}).decision, R.ALLOW)
            self.assertEqual(self.ev("fileops", {"operation": "grep", "path": "."}, mode, {}).decision, R.ALLOW)

    def test_network_and_mcp_ask_by_default_in_every_mode(self):
        for mode in ("guard", "sandbox", "local"):
            self.assertEqual(self.ev("webfetch", {"url": "https://x.org"}, mode, {}).decision, R.ASK)
            self.assertEqual(self.ev("mcp__s__t", {}, mode, {}).decision, R.ASK)

    def test_tool_glob(self):
        cfg = {"allow": ["mcp__fs__*"]}
        self.assertEqual(self.ev("mcp__fs__read", {}, "local", cfg).decision, R.ALLOW)
        self.assertEqual(self.ev("mcp__other__read", {}, "local", cfg).decision, R.ASK)


class BashTests(unittest.TestCase):
    def test_split(self):
        self.assertEqual(split_bash("a && b || c; d | e\nf")[0], ["a", "b", "c", "d", "e", "f"])
        self.assertEqual(split_bash('echo "a && b" && x')[0], ['echo "a && b"', "x"])
        self.assertEqual(split_bash("echo 'a;b'")[0], ["echo 'a;b'"])
        self.assertEqual(split_bash("a & b")[0], ["a", "b"])

    def test_substitution_flags(self):
        for cmd in ("echo $(id)", "echo `id`", "cat <(ls)", "tee >(wc)", 'echo "$(id)"'):
            self.assertTrue(split_bash(cmd)[1], cmd)
        for cmd in ("echo '$(id)'", "echo hi", 'echo "a"'):
            self.assertFalse(split_bash(cmd)[1], cmd)

    def test_compound_allow_needs_every_part(self):
        pr = rules()
        self.assertEqual(pr.evaluate("bash", bash("pytest -q && ls"), "local").decision, R.ALLOW)
        self.assertEqual(pr.evaluate("bash", bash("pytest -q && make"), "local").decision, R.ASK)
        self.assertEqual(pr.evaluate("bash", bash("pytest -q && make"), "guard").decision, R.ALLOW)

    def test_compound_deny_and_ask_any_part(self):
        pr = rules()
        for sep in (" && ", " || ", "; ", " | ", "\n"):
            self.assertEqual(pr.evaluate("bash", bash(f"ls{sep}rm -rf /"), "guard").decision, R.DENY, sep)
            self.assertEqual(pr.evaluate("bash", bash(f"ls{sep}git push"), "guard").decision, R.ASK, sep)

    def test_substitution_never_auto_allowed(self):
        pr = rules()
        v = pr.evaluate("bash", bash("ls $(pwd)"), "guard")
        self.assertEqual((v.decision, v.source), (R.ASK, "unsafe"))
        self.assertEqual(pr.evaluate("bash", bash("pytest `id`"), "local").decision, R.ASK)
        self.assertEqual(pr.evaluate("bash", bash("pytest '$(id)'"), "local").decision, R.ALLOW)

    def test_deny_sees_inside_substitution(self):
        pr = rules()
        self.assertEqual(pr.evaluate("bash", bash("echo $(rm -rf x)"), "guard").decision, R.DENY)
        self.assertEqual(pr.evaluate("bash", bash("echo `rm -rf x`"), "guard").decision, R.DENY)
        self.assertEqual(pr.evaluate("bash", bash("cat <(rm -rf x)"), "guard").decision, R.DENY)

    def test_deny_bypass_probes(self):
        pr = rules()
        probes = [
            "FOO=1 rm -rf /",
            'bash -c "rm -rf /"',
            "sh -lc 'rm -rf /'",
            "sudo rm -rf /",
            "sudo -u root rm -rf /",
            "/bin/rm -rf /",
            "rm  -rf /",
            "rm\t-rf /",
            "eval rm -rf /",
            "echo x | xargs rm -rf",
            "echo x | xargs -n 1 rm -rf",
            'pytest "$(rm -rf /)"',
            "pytest '$(rm -rf /)'",
            "env -i A=1 nohup time command /usr/bin/rm -rf /",
            "doas rm -rf /",
            "exec rm -rf /",
            "builtin eval 'rm -rf /'",
            'bash -c "bash -c \\"rm -rf /\\""',
            "find . -exec rm -rf {} \\;",
            "find . -name x -execdir rm -rf {} +",
            'rm "-rf" /',
            "echo `rm -rf /`",
            "echo $(echo $(rm -rf /))",
        ]
        for cmd in probes:
            with self.subTest(cmd=cmd):
                self.assertEqual(pr.evaluate("bash", bash(cmd), "guard").decision, R.DENY)

    def test_ask_rule_sees_normalised_parts(self):
        pr = rules()
        for cmd in ("GIT_TRACE=1 git push", "sudo git push origin", "/usr/bin/git push"):
            self.assertEqual(pr.evaluate("bash", bash(cmd), "guard").decision, R.ASK, cmd)

    def test_allow_does_not_see_through_wrappers(self):
        pr = rules()
        self.assertEqual(pr.evaluate("bash", bash("sudo pytest -q"), "local").decision, R.ASK)
        self.assertEqual(pr.evaluate("bash", bash("FOO=1 pytest -q"), "local").decision, R.ASK)

    def test_too_deep_nesting_never_auto_allowed(self):
        cmd = "make"
        for _ in range(10):
            cmd = "sh -c " + __import__("shlex").quote(cmd)
        v = rules().evaluate("bash", bash(cmd), "guard")
        self.assertEqual((v.decision, v.source), (R.ASK, "unsafe"))

    def test_quoted_separator_not_split(self):
        pr = rules()
        self.assertEqual(pr.evaluate("bash", bash('ls "a; rm -rf x"'), "guard").decision, R.ALLOW)

    def test_empty_command_uses_default(self):
        self.assertEqual(rules().evaluate("bash", bash(""), "local").decision, R.ASK)

    def test_env_never_auto_allowed(self):
        pr = rules()
        call = {"command": "pytest -q", "env": {"PYTHONPATH": "/evil"}}
        v = pr.evaluate("bash", call, "local")
        self.assertEqual((v.decision, v.source), (R.ASK, "unsafe"))
        self.assertIn("environment", v.reason)
        self.assertEqual(pr.evaluate("bash", call, "guard").decision, R.ASK)
        self.assertEqual(pr.evaluate("bash", {"command": "pytest -q", "env": {}}, "local").decision, R.ALLOW)
        deny = {"command": "rm -rf /", "env": {"A": "1"}}
        self.assertEqual(pr.evaluate("bash", deny, "guard").decision, R.DENY)


class PathTests(unittest.TestCase):
    def test_normalize(self):
        cases = [
            ("a/b.py", "a/b.py"),
            ("./a//b.py", "a/b.py"),
            ("a/../b.py", "b.py"),
            ("/work/proj/src/x.py", "src/x.py"),
            ("/work/proj", "."),
            ("../other/x", "/work/other/x"),
            ("src/../../x", "/work/x"),
            ("/etc/passwd", "/etc/passwd"),
            ("src\\a\\b.py", "src/a/b.py"),
        ]
        for given, want in cases:
            with self.subTest(given=given):
                self.assertEqual(normalize_path(given, WS), want)

    def test_windows_workspace_and_paths(self):
        ws = "C:\\Users\\me\\proj"
        self.assertEqual(normalize_path("C:\\Users\\me\\proj\\src\\a.py", ws), "src/a.py")
        self.assertEqual(normalize_path("src\\a.py", ws), "src/a.py")
        self.assertEqual(normalize_path("..\\x.py", ws), "C:/Users/me/x.py")

    def test_dotdot_cannot_escape_edit_allow(self):
        pr = rules()
        self.assertEqual(pr.evaluate("edit", {"path": "src/../secret.txt"}, "local").decision, R.ASK)
        self.assertEqual(pr.evaluate("edit", {"path": "src/../.env"}, "local").decision, R.DENY)
        self.assertEqual(pr.evaluate("edit", {"path": "/work/proj/src/ok.py"}, "local").decision, R.ALLOW)

    def test_windows_separator_in_rule_and_call(self):
        pr = rules({"allow": ["edit(src\\**)"]})
        self.assertEqual(pr.evaluate("edit", {"path": "src\\a\\b.py"}, "local").decision, R.ALLOW)

    def test_single_star_does_not_cross_dirs(self):
        pr = rules({"allow": ["edit(src/*.py)"]})
        self.assertEqual(pr.evaluate("edit", {"path": "src/a.py"}, "local").decision, R.ALLOW)
        self.assertEqual(pr.evaluate("edit", {"path": "src/x/a.py"}, "local").decision, R.ASK)

    def test_deny_env_outside_workspace(self):
        pr = rules()
        self.assertEqual(pr.evaluate("edit", {"path": "/home/x/.env"}, "guard").decision, R.DENY)

    def test_fileops_without_op(self):
        pr = rules({"deny": ["fileops(secrets/**)"]})
        self.assertEqual(pr.evaluate("fileops", {"operation": "read", "path": "secrets/k"}, "guard").decision, R.DENY)


class AllowAlwaysTests(unittest.TestCase):
    def test_exact_rule_and_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            pr = PermissionRules(CONFIG, WS, store_dir=d)
            self.assertEqual(pr.evaluate("bash", bash("make test"), "local").decision, R.ASK)
            added = pr.add_allow_always("bash", bash("make test"))
            self.assertEqual(added, ["bash(make test)"])
            self.assertEqual(pr.evaluate("bash", bash("make test"), "local").decision, R.ALLOW)
            self.assertEqual(pr.evaluate("bash", bash("make all"), "local").decision, R.ASK)
            self.assertTrue(store_path(WS, d).is_file())
            again = PermissionRules(CONFIG, WS, store_dir=d)
            self.assertEqual(again.evaluate("bash", bash("make test"), "local").decision, R.ALLOW)
            other = PermissionRules(CONFIG, "/work/other", store_dir=d)
            self.assertEqual(other.evaluate("bash", bash("make test"), "local").decision, R.ASK)

    def test_glob_chars_escaped(self):
        pr = rules()
        pr.add_allow_always("bash", bash("rm a*b"))
        self.assertEqual(pr.evaluate("bash", bash("rm a*b"), "local").decision, R.ALLOW)
        self.assertEqual(pr.evaluate("bash", bash("rm axb"), "local").decision, R.ASK)
        pr.add_allow_always("bash", bash("echo a*"))
        self.assertEqual(pr.evaluate("bash", bash("echo a*"), "local").decision, R.ALLOW)
        self.assertEqual(pr.evaluate("bash", bash("echo abc"), "local").decision, R.ASK)

    def test_compound_adds_one_rule_per_part(self):
        pr = rules()
        added = pr.add_allow_always("bash", bash("make a && make b"))
        self.assertEqual(added, ["bash(make a)", "bash(make b)"])
        self.assertEqual(pr.evaluate("bash", bash("make b"), "local").decision, R.ALLOW)

    def test_deny_still_beats_allow_always(self):
        pr = rules()
        pr.add_allow_always("edit", {"path": "x/.env"})
        self.assertEqual(pr.evaluate("edit", {"path": "x/.env"}, "local").decision, R.DENY)

    def test_other_kinds(self):
        pr = rules()
        self.assertEqual(pr.add_allow_always("webfetch", {"url": "https://example.com/a"}),
                         ["webfetch(domain:example.com)"])
        self.assertEqual(pr.evaluate("webfetch", {"url": "https://example.com/b"}, "local").decision, R.ALLOW)
        self.assertEqual(pr.add_allow_always("mcp__s__t", {}), ["mcp__s__t"])
        self.assertEqual(pr.evaluate("mcp__s__t", {}, "local").decision, R.ALLOW)
        pr.add_allow_always("fileops", {"operation": "write", "path": "a/[x].txt"})
        self.assertEqual(pr.evaluate("fileops", {"operation": "write", "path": "a/[x].txt"}, "local").decision, R.ALLOW)
        self.assertEqual(pr.evaluate("fileops", {"operation": "write", "path": "a/x.txt"}, "local").decision, R.ASK)

    def test_store_survives_garbage(self):
        with tempfile.TemporaryDirectory() as d:
            p = store_path(WS, d)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('allow:\n  - "bash(ok)"\n  - "bad\n  - "bash()"\njunk\n', encoding="utf-8")
            pr = PermissionRules({}, WS, store_dir=d)
            self.assertEqual(pr.evaluate("bash", bash("ok"), "local").decision, R.ALLOW)
            self.assertTrue(pr.errors)


if __name__ == "__main__":
    unittest.main()
