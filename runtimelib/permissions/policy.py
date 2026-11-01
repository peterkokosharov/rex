# SPDX-License-Identifier: MIT
"""Default permission policy implementation."""

from __future__ import annotations

from typing import Any

from .base import PermissionPolicy, PermissionResult, PermissionRule


class DefaultPermissionPolicy(PermissionPolicy):
    """Default role-based permission policy."""

    # Role-based permissions: tool -> list of PermissionRule
    ROLE_PERMISSIONS: dict[str, list[PermissionRule]] = {
        "ORCHESTRATOR": [
            PermissionRule(tool="*", operation=None, result=PermissionResult.ALLOW),
        ],
        "ARCHITECT": [
            PermissionRule(tool="fileops", operation=None, result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="pytest *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="cargo test *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="npm test *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="build *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="lint *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="format *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="curl *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="wget *", result=PermissionResult.ALLOW),
            PermissionRule(tool="git", operation=None, result=PermissionResult.ALLOW),
            PermissionRule(tool="websearch", operation=None, result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="docker *", result=PermissionResult.ASK, reason="Docker operations require approval"),
            PermissionRule(tool="bash", operation="sudo *", result=PermissionResult.ASK, reason="Sudo operations require approval"),
            PermissionRule(tool="bash", operation="rm -rf *", result=PermissionResult.DENY, reason="Destructive command"),
        ],
        "CODER": [
            PermissionRule(tool="fileops", operation=None, result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="pytest *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="cargo test *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="npm test *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="build *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="lint *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="format *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="pip install *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="cargo build *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="npm run build *", result=PermissionResult.ALLOW),
            PermissionRule(tool="git", operation="commit *", result=PermissionResult.ALLOW),
            PermissionRule(tool="git", operation="add *", result=PermissionResult.ALLOW),
            PermissionRule(tool="git", operation="diff *", result=PermissionResult.ALLOW),
            PermissionRule(tool="git", operation="status *", result=PermissionResult.ALLOW),
            PermissionRule(tool="git", operation="log *", result=PermissionResult.ALLOW),
            PermissionRule(tool="git", operation="branch *", result=PermissionResult.ALLOW),
            PermissionRule(tool="git", operation="checkout *", result=PermissionResult.ALLOW),
            PermissionRule(tool="websearch", operation=None, result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="npm publish *", result=PermissionResult.DENY, reason="Publishing requires approval"),
            PermissionRule(tool="bash", operation="rm -rf *", result=PermissionResult.DENY, reason="Destructive command"),
            PermissionRule(tool="bash", operation="sudo *", result=PermissionResult.DENY, reason="Sudo not allowed"),
        ],
        "TESTER": [
            PermissionRule(tool="fileops", operation="read *", result=PermissionResult.ALLOW),
            PermissionRule(tool="fileops", operation="list *", result=PermissionResult.ALLOW),
            PermissionRule(tool="fileops", operation="glob *", result=PermissionResult.ALLOW),
            PermissionRule(tool="fileops", operation="grep *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="pytest *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="cargo test *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="npm test *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="go test *", result=PermissionResult.ALLOW),
            PermissionRule(tool="git", operation="diff *", result=PermissionResult.ALLOW),
            PermissionRule(tool="git", operation="status *", result=PermissionResult.ALLOW),
            PermissionRule(tool="git", operation="log *", result=PermissionResult.ALLOW),
            PermissionRule(tool="fileops", operation="write *", result=PermissionResult.DENY, reason="Tester cannot write files"),
            PermissionRule(tool="fileops", operation="edit *", result=PermissionResult.DENY, reason="Tester cannot edit files"),
            PermissionRule(tool="git", operation="commit *", result=PermissionResult.DENY, reason="Tester cannot commit"),
            PermissionRule(tool="git", operation="push *", result=PermissionResult.DENY, reason="Tester cannot push"),
        ],
        "REVIEWER": [
            PermissionRule(tool="fileops", operation="read *", result=PermissionResult.ALLOW),
            PermissionRule(tool="fileops", operation="list *", result=PermissionResult.ALLOW),
            PermissionRule(tool="fileops", operation="glob *", result=PermissionResult.ALLOW),
            PermissionRule(tool="fileops", operation="grep *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="pytest *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="lint *", result=PermissionResult.ALLOW),
            PermissionRule(tool="git", operation="diff *", result=PermissionResult.ALLOW),
            PermissionRule(tool="git", operation="log *", result=PermissionResult.ALLOW),
            PermissionRule(tool="git", operation="status *", result=PermissionResult.ALLOW),
            PermissionRule(tool="websearch", operation=None, result=PermissionResult.ALLOW),
            PermissionRule(tool="fileops", operation="write *", result=PermissionResult.DENY, reason="Reviewer cannot write files"),
            PermissionRule(tool="git", operation="commit *", result=PermissionResult.DENY, reason="Reviewer cannot commit"),
        ],
        "RESEARCHER": [
            PermissionRule(tool="fileops", operation="read *", result=PermissionResult.ALLOW),
            PermissionRule(tool="fileops", operation="list *", result=PermissionResult.ALLOW),
            PermissionRule(tool="fileops", operation="glob *", result=PermissionResult.ALLOW),
            PermissionRule(tool="fileops", operation="grep *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="curl *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="wget *", result=PermissionResult.ALLOW),
            PermissionRule(tool="websearch", operation=None, result=PermissionResult.ALLOW),
            PermissionRule(tool="fileops", operation="write *", result=PermissionResult.DENY, reason="Researcher cannot write files"),
            PermissionRule(tool="bash", operation="pytest *", result=PermissionResult.DENY, reason="Researcher cannot run tests"),
        ],
        "DEBUGGER": [
            PermissionRule(tool="fileops", operation=None, result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="pytest *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="cargo test *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="npm test *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="build *", result=PermissionResult.ALLOW),
            PermissionRule(tool="bash", operation="lint *", result=PermissionResult.ALLOW),
            PermissionRule(tool="git", operation=None, result=PermissionResult.ALLOW),
            PermissionRule(tool="websearch", operation=None, result=PermissionResult.ALLOW),
        ],
    }

    # Default fallback for unknown roles
    DEFAULT_RULES = [
        PermissionRule(tool="fileops", operation="read *", result=PermissionResult.ALLOW),
        PermissionRule(tool="fileops", operation="list *", result=PermissionResult.ALLOW),
        PermissionRule(tool="bash", operation="pytest *", result=PermissionResult.ALLOW),
        PermissionRule(tool="git", operation="status *", result=PermissionResult.ALLOW),
        PermissionRule(tool="*", operation=None, result=PermissionResult.DENY),
    ]

    def __init__(self, custom_rules: dict[str, list[PermissionRule]] | None = None):
        """Initialize with optional custom rules."""
        self.custom_rules = custom_rules or {}

    def check(self, tool: str, operation: str, args: dict[str, Any]) -> PermissionResult:
        """Check permission for a tool call."""
        # Determine role from args or default
        role = args.get("_role", "CODER")

        # Get rules for role
        rules = self.ROLE_PERMISSIONS.get(role, self.DEFAULT_RULES)
        if role in self.custom_rules:
            rules = self.custom_rules[role]

        # Check rules in order (DENY > ASK > ALLOW priority)
        # First pass: check for explicit DENY
        for rule in rules:
            if rule.matches(tool, operation, args) and rule.result == PermissionResult.DENY:
                return PermissionResult.DENY

        # Second pass: check for ASK
        for rule in rules:
            if rule.matches(tool, operation, args) and rule.result == PermissionResult.ASK:
                return PermissionResult.ASK

        # Third pass: check for ALLOW
        for rule in rules:
            if rule.matches(tool, operation, args) and rule.result == PermissionResult.ALLOW:
                return PermissionResult.ALLOW

        # Default deny
        return PermissionResult.DENY

    def get_allowed_tools(self, role: str) -> list[str]:
        """Get list of tools that have at least one ALLOW rule for the role."""
        rules = self.ROLE_PERMISSIONS.get(role, self.DEFAULT_RULES)
        allowed = set()
        for rule in rules:
            if rule.result == PermissionResult.ALLOW:
                allowed.add(rule.tool)
        return sorted(allowed)