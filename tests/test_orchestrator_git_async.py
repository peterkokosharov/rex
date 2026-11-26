# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nemotron-super
# Licensed under MIT License

"""Tests that git subprocess calls no longer block the asyncio event loop."""

from __future__ import annotations

import ast
import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from orchestrator import Orchestrator


def _make_repo(tmp: Path) -> None:
    """Initialize a git repo with user config and an empty initial commit."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "Initial"], cwd=tmp, check=True, capture_output=True)


def test_git_helper_runs_off_the_loop_on_a_real_repo() -> None:
    """_git runs git commands off the event loop on a real repo."""

    async def run() -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            _make_repo(tmp)
            orch = Orchestrator(project_root=tmp)
            await orch.initialize()  # exercises _git via rev-parse --git-dir
            result = await orch._git("rev-parse", "HEAD", cwd=tmp, text=True)
            sha = result.stdout.strip()
            assert len(sha) == 40
            int(sha, 16)  # must be hex
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    asyncio.run(run())


def test_no_blocking_subprocess_run_inside_async_methods() -> None:
    """No async def in orchestrator.py calls subprocess.run directly."""

    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "orchestrator" / "orchestrator.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    async_methods = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    ]
    assert async_methods, "expected at least one async def"

    for node in async_methods:
        segment = ast.get_source_segment(src, node) or ""
        assert "subprocess.run(" not in segment, (
            f"blocking subprocess.run inside async def {node.name}"
        )
