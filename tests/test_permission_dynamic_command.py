# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov
# AI models: claude-sonnet-5-5
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


def test_guard_mode_without_deny_rules_keeps_no_whitelist():
    rules = PermissionRules({})
    assert rules.evaluate("bash", {"command": "echo $(date)"}, mode="guard").decision == R.ALLOW
