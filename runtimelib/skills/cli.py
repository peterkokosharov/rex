# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""`rex skills` and `rex skills show <name>`."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, TextIO

from . import discover_skills


def run_skills(argv: list[str], config: Any = None, workspace: Path | None = None,
               out: TextIO | None = None, err: TextIO | None = None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    warnings: list[str] = []
    skills = discover_skills(workspace or Path.cwd(), config, warnings=warnings, include_untrusted=True)
    for w in warnings:
        print(f"warning: {w}", file=err)

    if not argv:
        if not skills:
            print("No skills found.", file=out)
        for s in skills:
            mark = ""
            if s.project:
                mark = " (project)" if s.trusted else " (project, not trusted - set skills.trust_workspace)"
            print(f"{s.name}\t{s.path}\t{s.description}{mark}", file=out)
        return 0
    if argv[0] == "show" and len(argv) == 2:
        skill = next((s for s in skills if s.name == argv[1]), None)
        if skill is None:
            print(f"unknown skill: {argv[1]}", file=err)
            return 1
        print(skill.body, file=out)
        return 0
    print("usage: rex skills [show <name>]", file=err)
    return 2
