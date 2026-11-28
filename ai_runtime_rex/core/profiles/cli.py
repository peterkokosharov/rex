# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-5-5
# Licensed under MIT License

"""`ai-rex profile` (layer tree, active layers marked) and `ai-rex profile show` (the composed instruction)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, TextIO

from . import all_layers, build_profile


def run_profile(argv: list[str], config: Any = None, workspace: Path | None = None,
                out: TextIO | None = None, err: TextIO | None = None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    ws = workspace or Path.cwd()
    agents_md = ""
    try:
        from ..agent.instructions import load_instructions
        from ..trust import trust_workspace
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()):
            ins = load_instructions(ws, trusted=trust_workspace(config))
        agents_md = "\n".join((ins.project, ins.user))
    except Exception:  # noqa: BLE001 - the listing works without AGENTS.md
        pass
    profile = build_profile(ws, config, agents_md=agents_md)
    for w in profile.warnings:
        print(f"warning: {w}", file=err)
    if not argv:
        active = set(profile.paths)
        print(f"Active: {profile.label()}  (selection: {profile.requested}, from {profile.origin})", file=out)
        for path, layer in sorted(all_layers(ws, config).items()):
            mark = "*" if path in active else " "
            indent = "  " * layer.depth
            name = path.rsplit("/", 1)[-1] if path else "(base)"
            detect = f"  [detect: {', '.join(layer.detect)}]" if layer.detect else ""
            print(f"{mark} {indent}{name}  - {layer.description}{detect}", file=out)
            for src in layer.sources:
                print(f"    {indent}{src}", file=out)
        return 0
    if argv == ["show"]:
        print(profile.text(), file=out)
        return 0
    print("usage: ai-rex profile [show]   (choose layers with AI_REX_PROFILE or agent.profile)", file=err)
    return 2
