# SPDX-License-Identifier: MIT
"""Skills system - standard format with hooks, auto_mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from ..kikolib import yaml_lite
from ..hooks import HookEvent, Hook, HookContext, get_hook_engine


class SkillTrigger(Enum):
    """How a skill can be triggered."""
    MANUAL = "manual"           # Explicit /skill command
    AUTO = "auto"               # Auto-mode based on context
    HOOK = "hook"               # Triggered by hook event
    SCHEDULE = "schedule"       # Cron-like schedule


@dataclass
class SkillHook:
    """Hook configuration for a skill."""
    event: HookEvent
    condition: str = ""  # Expression to evaluate
    priority: int = 0


@dataclass
class SkillEntrypoint:
    """Skill entrypoint configuration."""
    command: str  # Python module:function or shell command
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class SkillAutoMode:
    """Auto-mode configuration."""
    enabled: bool = False
    triggers: list[str] = field(default_factory=list)  # Keywords, patterns
    policy: str = "allow"  # "allow", "deny", "ask"
    conditions: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillSchema:
    """Skill definition schema."""
    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    license: str = "MIT"

    entrypoint: Optional[SkillEntrypoint] = None
    hooks: list[SkillHook] = field(default_factory=list)
    auto_mode: Optional[SkillAutoMode] = None
    triggers: SkillTrigger = SkillTrigger.MANUAL

    permissions: dict[str, Any] = field(default_factory=dict)  # Tool permissions
    dependencies: list[str] = field(default_factory=list)  # Other skill names
    config_schema: dict[str, Any] = field(default_factory=dict)  # JSON Schema for skill config


class Skill:
    """Loaded skill instance."""

    def __init__(self, schema: SkillSchema, config: dict[str, Any] | None = None) -> None:
        self.schema = schema
        self.config = config or {}
        self._hooks: list[Hook] = []

    def install_hooks(self, hook_engine) -> None:
        """Install skill hooks into hook engine."""
        for hook_def in self.schema.hooks:
            hook = SkillHookWrapper(self, hook_def)
            self._hooks.append(hook)
            hook_engine.registry.register(hook)

    def uninstall_hooks(self, hook_engine) -> None:
        """Remove skill hooks from hook engine."""
        for hook in self._hooks:
            hook_engine.registry.unregister(hook)
        self._hooks.clear()

    def execute(self, args: list[str], context: dict[str, Any]) -> Any:
        """Execute skill entrypoint."""
        if not self.schema.entrypoint:
            raise ValueError(f"Skill {self.schema.name} has no entrypoint")

        # Support python module:function or shell command
        entrypoint = self.schema.entrypoint
        if ":" in entrypoint.command:
            module_name, func_name = entrypoint.command.split(":", 1)
            module = __import__(module_name, fromlist=[func_name])
            func = getattr(module, func_name)
            return func(args, context, self.config)
        else:
            # Shell command
            import subprocess
            cmd = [entrypoint.command] + entrypoint.args + args
            env = {**os.environ, **entrypoint.env}
            result = subprocess.run(cmd, capture_output=True, text=True, env=env)
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }


class SkillHookWrapper(Hook):
    """Wrapper to make skill hooks work with hook engine."""

    def __init__(self, skill: Skill, hook_def: SkillHook) -> None:
        self.skill = skill
        self.hook_def = hook_def

    @property
    def name(self) -> str:
        return f"{self.skill.schema.name}:{self.hook_def.event.value}"

    @property
    def events(self) -> list[HookEvent]:
        return [self.hook_def.event]

    def run(self, context: HookContext) -> Any:
        # Evaluate condition if present
        if self.hook_def.condition:
            # Simple expression evaluation (could be extended)
            try:
                if not eval(self.hook_def.condition, {"context": context, "config": self.skill.config}):
                    return None
            except Exception:
                return None

        # Execute skill
        return self.skill.execute([], {
            "hook_event": self.hook_def.event.value,
            "context": context.__dict__,
        })


class SkillRegistry:
    """Registry for skills."""

    def __init__(self, skills_dir: Path | None = None) -> None:
        self.skills_dir = skills_dir or Path.home() / ".config" / "runtime" / "skills"
        self._skills: dict[str, Skill] = {}
        self._hook_engine = get_hook_engine()

    def load_skill(self, name: str, skill_dir: Path) -> Skill | None:
        """Load a skill from directory."""
        skill_file = skill_dir / "skill.yaml"
        if not skill_file.exists():
            return None

        try:
            data = yaml_lite.load_file(skill_file)
            schema = SkillSchema(**data)
            skill = Skill(schema, data.get("config", {}))
            self._skills[name] = skill
            return skill
        except Exception as e:
            print(f"[Skill Load Error] {name}: {e}", file=__import__("sys").stderr)
            return None

    def load_all(self) -> None:
        """Load all skills from skills directory."""
        if not self.skills_dir.exists():
            return
        for skill_dir in self.skills_dir.iterdir():
            if skill_dir.is_dir():
                self.load_skill(skill_dir.name, skill_dir)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_skills(self) -> list[str]:
        return sorted(self._skills.keys())

    def enable(self, name: str) -> bool:
        """Enable a skill (install hooks)."""
        skill = self._skills.get(name)
        if skill:
            skill.install_hooks(self._hook_engine)
            return True
        return False

    def disable(self, name: str) -> bool:
        """Disable a skill (remove hooks)."""
        skill = self._skills.get(name)
        if skill:
            skill.uninstall_hooks(self._hook_engine)
            return True
        return False

    def execute(self, name: str, args: list[str], context: dict[str, Any]) -> Any:
        """Execute a skill."""
        skill = self._skills.get(name)
        if not skill:
            raise ValueError(f"Skill not found: {name}")
        return skill.execute(args, context)


import os