# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""Skill tools: load_skill(name) and read_skill_file(name, relpath)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..skills import Skill, discover_skills, read_skill_file
from .base import Tool, ToolSchema

LOAD_DESCRIPTION = (
    "Load a skill: returns the instructions from its SKILL.md plus the list of extra files in the skill "
    "folder. When a task matches a skill's description in the skills index, call load_skill first and "
    "follow it."
)
READ_DESCRIPTION = (
    "Read one text file from inside a skill folder (a path from the file list returned by load_skill)."
)


class _SkillTool(Tool):
    def __init__(self, name: str, description: str, parameters: dict[str, Any],
                 config: Any = None, skills: list[Skill] | None = None) -> None:
        super().__init__(name, description, ToolSchema(name=name, description=description, parameters=parameters))
        self._skills = skills if skills is not None else discover_skills(Path.cwd(), config)

    def _find(self, name: Any) -> Skill | None:
        return next((s for s in self._skills if s.name == name), None)

    def _unknown(self, name: Any) -> dict[str, Any]:
        known = ", ".join(s.name for s in self._skills) or "none"
        return {"ok": False, "error": f"unknown skill {name!r}; available: {known}"}


class LoadSkillTool(_SkillTool):
    def __init__(self, config: Any = None, skills: list[Skill] | None = None) -> None:
        super().__init__(
            "load_skill", LOAD_DESCRIPTION,
            {"type": "object",
             "properties": {"name": {"type": "string", "description": "Skill name from the skills index"}},
             "required": ["name"]},
            config, skills)

    def execute(self, **kwargs: Any) -> Any:
        skill = self._find(kwargs.get("name"))
        if skill is None:
            return self._unknown(kwargs.get("name"))
        return {"ok": True, "name": skill.name, "instructions": skill.body, "files": list(skill.files)}


class ReadSkillFileTool(_SkillTool):
    def __init__(self, config: Any = None, skills: list[Skill] | None = None) -> None:
        super().__init__(
            "read_skill_file", READ_DESCRIPTION,
            {"type": "object",
             "properties": {"name": {"type": "string", "description": "Skill name"},
                            "relpath": {"type": "string", "description": "Relative path inside the skill folder"}},
             "required": ["name", "relpath"]},
            config, skills)

    def execute(self, **kwargs: Any) -> Any:
        skill = self._find(kwargs.get("name"))
        if skill is None:
            return self._unknown(kwargs.get("name"))
        try:
            return {"ok": True, "content": read_skill_file(skill, kwargs.get("relpath"))}
        except (PermissionError, ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}


def create_skill_tools(config: Any = None) -> list[Tool]:
    skills = discover_skills(Path.cwd(), config)
    return [LoadSkillTool(config, skills), ReadSkillFileTool(config, skills)]
