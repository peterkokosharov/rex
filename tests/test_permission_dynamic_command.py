# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov
# AI models: claude-sonnet-5-5, claude-opus-5-5
"""A dynamic command word must not slip past a bash deny rule in guard mode."""

import pytest

from runtimelib.permissions.base import PermissionResult as R
from runtimelib.permissions.rules import PermissionRules

DYNAMIC = [
    "$(echo rm) -rf /",
    "`echo rm` -rf /",
    "eval $(echo rm) -rf /",
    "\\rm -rf /",
    '"$(echo rm)" -rf /',
    "echo rm | xargs -I{} {} -rf /",
    # wrappers, reserved words and grouping in front of the dynamic word
    'X="a b" $(echo rm) -rf /',
    "sudo $(echo rm) -rf /",
    "env $(echo rm) -rf /",
    "command $(echo rm) -rf /",
    "timeout 5 $(echo rm) -rf /",
    "nice $(echo rm) -rf /",
    "exec $(echo rm) -rf /",
    "time $(echo rm) -rf /",
    "! $(echo rm) -rf /",
    "( $(echo rm) -rf / )",
    "{ $(echo rm) -rf /; }",
    "if true; then $(echo rm) -rf /; fi",
    "for x in 1; do $(echo rm) -rf /; done",
    "bash -c '$(echo rm) -rf /'",
    "builtin $(echo rm) -rf /",
    "case x in x) $(echo rm) -rf / ;; esac",
    "case x in *) $(echo rm) -rf / ;; esac",
    # a glob or a leading redirection hides the command word as well
    "/bin/r? -rf /",
    "/usr/bin/r[m] -rf /",
    ">/tmp/x $(echo rm) -rf /",
    ">/tmp/x rm -rf /",
]


@pytest.mark.parametrize("mode", ["guard", "local", "sandbox"])
@pytest.mark.parametrize("cmd", DYNAMIC)
def test_dynamic_command_is_never_silently_allowed(mode, cmd):
    rules = PermissionRules({"deny": ["bash(rm *)"]})
    verdict = rules.evaluate("bash", {"command": cmd}, mode=mode)
    assert verdict.decision in (R.ASK, R.DENY)


def test_plain_commands_unchanged_in_guard_mode():
    rules = PermissionRules({"deny": ["bash(rm *)"]})
    assert rules.evaluate("bash", {"command": "rm -rf /"}, mode="guard").decision == R.DENY
    assert rules.evaluate("bash", {"command": "ls -la"}, mode="guard").decision == R.ALLOW


def test_ask_rule_is_protected_too():
    rules = PermissionRules({"ask": ["bash(git push *)"]})
    assert rules.evaluate("bash", {"command": "$(echo git) push origin"}, mode="guard").decision == R.ASK


@pytest.mark.parametrize("cmd", ["echo $(date)", "ls -la", "cd /tmp && ls", "git log --oneline -n 3",
                                 "X=1 make test", "grep -r 'a*b' src", "echo `date`"])
# Known false positive (ASK, never DENY): `case $1 in a) ...;; *) ...;; esac` - the subjects lose
# the case keyword, so `$1` / `*` look like command words. Accepted: ask is the safe side.
def test_ordinary_commands_stay_allowed_in_guard_mode(cmd):
    rules = PermissionRules({"deny": ["bash(rm *)"]})
    assert rules.evaluate("bash", {"command": cmd}, mode="guard").decision == R.ALLOW


def test_guard_mode_without_deny_rules_keeps_no_whitelist():
    rules = PermissionRules({})
    assert rules.evaluate("bash", {"command": "echo $(date)"}, mode="guard").decision == R.ALLOW
