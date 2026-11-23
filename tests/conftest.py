# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""Isolate tests from the developer's own AGENTS.md files, config and environment."""

import os

import pytest


@pytest.fixture(autouse=True)
def _no_standing_instructions(monkeypatch, tmp_path):
    """Run from an empty cwd and home with no user config, no RUNTIME_* variables, no warning history."""
    home = tmp_path / "_home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(tmp_path)
    for key in [k for k in os.environ if k.startswith("RUNTIME_")]:
        monkeypatch.delenv(key)
    import runtimelib.config.config as cfg
    from runtimelib.agent import instructions
    monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", home / ".config" / "runtime" / "config.yaml")
    monkeypatch.setattr(cfg, "DEFAULT_ENV_PATH", home / ".config" / "runtime" / ".env")
    instructions._WARNED_MISSING.clear()
