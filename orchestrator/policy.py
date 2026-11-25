# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra
# Licensed under MIT License

"""Role policies for orchestrator — model class, max_steps, allowed tools, sandbox profile.

This replaces the hardcoded role configs that were in the runtime.
Policies are loaded from policy.yaml (or dict) and passed to the runtime via config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from pathlib import Path
import yaml


@dataclass
class SandboxProfile:
    """Sandbox configuration for a role."""
    network: str = "none"           # "none" | "bridge"
    memory: str = "2G"              # e.g., "1G", "2G", "4G"
    cpus: str = "1.0"               # e.g., "0.5", "1.0", "2.0"
    pids_limit: int = 100
    read_only_root: bool = True
    read_write_mounts: list[dict] = field(default_factory=list)
    read_only_overlays: list[dict] = field(default_factory=list)


@dataclass
class RolePolicy:
    """Complete policy for an agent role."""
    name: str
    model: str = "gemini/gemini-flash"
    model_class: str = "SONNET"      # OPUS, SONNET, HAIKU, FABLE, CODER, EYES, TUTOR, ROUTER
    max_steps: int = 25
    allowed_tools: list[str] = field(default_factory=lambda: ["fileops", "bash", "git", "websearch"])
    sandbox: SandboxProfile = field(default_factory=SandboxProfile)
    # Permission rules (allow/deny/ask) - merged with runtime permissions
    permissions: dict[str, list[str]] = field(default_factory=dict)

    def to_runtime_config(self) -> dict:
        """Convert to runtime config dict."""
        return {
            "model": self.model,
            "max_steps": self.max_steps,
            "allowed_tools": self.allowed_tools,
            "sandbox": {
                "network": self.sandbox.network,
                "memory": self.sandbox.memory,
                "cpus": self.sandbox.cpus,
                "pids_limit": self.sandbox.pids_limit,
                "read_only_root": self.sandbox.read_only_root,
                "read_write_mounts": self.sandbox.read_write_mounts,
                "read_only_overlays": self.sandbox.read_only_overlays,
            },
            "permissions": self.permissions,
        }


# Default role policies (can be overridden by policy.yaml)
DEFAULT_POLICIES: dict[str, RolePolicy] = {
    "ORCHESTRATOR": RolePolicy(
        name="ORCHESTRATOR",
        model="gemini/gemini-flash",
        model_class="OPUS",
        max_steps=60,
        allowed_tools=["fileops", "bash", "git", "websearch", "task"],
        sandbox=SandboxProfile(
            network="none",
            memory="2G",
            cpus="1.0",
        ),
        permissions={
            "allow": ["task:*", "fileops:*", "bash:*", "git:*", "websearch:*"],
            "deny": ["git:push"],
        },
    ),
    "CODER": RolePolicy(
        name="CODER",
        model="nvidia/nvidia/nemotron-3.5-super-vl-120b-a12b",
        model_class="SONNET",
        max_steps=25,
        allowed_tools=["fileops", "bash", "git", "websearch", "render_diagram"],
        sandbox=SandboxProfile(
            network="bridge",
            memory="2G",
            cpus="2.0",
        ),
        permissions={
            "allow": ["fileops:*", "bash:pytest *", "bash:cargo test *", "bash:go test *", "bash:npm test *", "git:commit", "git:diff", "git:status", "git:log"],
            "deny": ["git:push", "bash:rm -rf *", "bash:sudo *"],
        },
    ),
    "TESTER": RolePolicy(
        name="TESTER",
        model="gemini/gemini-flash-lite",
        model_class="HAIKU",
        max_steps=15,
        allowed_tools=["fileops", "bash", "git"],
        sandbox=SandboxProfile(
            network="bridge",
            memory="1G",
            cpus="1.0",
        ),
        permissions={
            "allow": ["fileops:read", "bash:pytest *", "bash:npm test *", "bash:go test *", "bash:cargo test *", "git:status", "git:diff"],
            "deny": ["fileops:write", "fileops:edit", "git:commit", "git:push"],
        },
    ),
    "REVIEWER": RolePolicy(
        name="REVIEWER",
        model="gemini/gemini-flash",
        model_class="OPUS",
        max_steps=20,
        allowed_tools=["fileops", "bash", "git", "websearch"],
        sandbox=SandboxProfile(
            network="none",
            memory="1G",
            cpus="1.0",
        ),
        permissions={
            "allow": ["fileops:read", "bash:grep *", "bash:cat *", "git:diff", "git:log", "git:status"],
            "deny": ["fileops:write", "fileops:edit", "bash:*", "git:*"],
        },
    ),
    "RESEARCHER": RolePolicy(
        name="RESEARCHER",
        model="gemini/gemini-flash",
        model_class="ROUTER",
        max_steps=30,
        allowed_tools=["fileops", "bash", "git", "websearch", "webfetch"],
        sandbox=SandboxProfile(
            network="bridge",
            memory="1G",
            cpus="1.0",
        ),
        permissions={
            "allow": ["fileops:*", "websearch:*", "webfetch:*", "git:status", "git:log"],
            "deny": ["fileops:write", "bash:*", "git:commit", "git:push"],
        },
    ),
    "DEBUGGER": RolePolicy(
        name="DEBUGGER",
        model="nvidia/nvidia/nemotron-3.5-super-vl-120b-a12b",
        model_class="EYES",
        max_steps=25,
        allowed_tools=["fileops", "bash", "git", "websearch", "render_diagram"],
        sandbox=SandboxProfile(
            network="bridge",
            memory="2G",
            cpus="2.0",
        ),
        permissions={
            "allow": ["fileops:*", "bash:*", "git:*", "websearch:*", "render_diagram:*"],
            "deny": ["git:push"],
        },
    ),
}


def get_role_policy(role: str) -> RolePolicy:
    """Get role policy by name (case-insensitive). Falls back to CODER."""
    return DEFAULT_POLICIES.get(role.upper(), DEFAULT_POLICIES["CODER"])


def load_policy_file(path: str | Path) -> dict[str, RolePolicy]:
    """Load role policies from YAML file. Returns dict merging with defaults."""
    path = Path(path)
    if not path.exists():
        return DEFAULT_POLICIES.copy()

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    policies = DEFAULT_POLICIES.copy()
    for role_name, role_data in data.get("roles", {}).items():
        sandbox_data = role_data.get("sandbox", {})
        sandbox = SandboxProfile(
            network=sandbox_data.get("network", "none"),
            memory=sandbox_data.get("memory", "2G"),
            cpus=sandbox_data.get("cpus", "1.0"),
            pids_limit=sandbox_data.get("pids_limit", 100),
            read_only_root=sandbox_data.get("read_only_root", True),
            read_write_mounts=sandbox_data.get("read_write_mounts", []),
            read_only_overlays=sandbox_data.get("read_only_overlays", []),
        )
        policies[role_name.upper()] = RolePolicy(
            name=role_name.upper(),
            model=role_data.get("model", "gemini/gemini-flash"),
            model_class=role_data.get("model_class", "SONNET"),
            max_steps=role_data.get("max_steps", 25),
            allowed_tools=role_data.get("allowed_tools", ["fileops", "bash", "git", "websearch"]),
            sandbox=sandbox,
            permissions=role_data.get("permissions", {}),
        )

    return policies