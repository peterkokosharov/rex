# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""Isolate tests from the developer's own AGENTS.md files."""

import pytest


@pytest.fixture(autouse=True)
def _no_standing_instructions(monkeypatch, tmp_path):
    """Run from an empty cwd and home so no real AGENTS.md leaks into prompts."""
    home = tmp_path / "_home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(tmp_path)
