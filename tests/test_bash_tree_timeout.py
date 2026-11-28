# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-opus-5-5
# Licensed under MIT License
"""The bash tool's timeout ends the whole process tree, not only the shell."""
from __future__ import annotations

import sys
import time

import pytest

from runtimelib.config import Config
from runtimelib.tools.bash import BashTool, find_bash

LOCAL = Config({"cli": {"unsafe_local_tools": True}})

pytestmark = pytest.mark.skipif(find_bash() is None, reason="no bash")


def test_timeout_returns_on_time_with_background_children(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    started = time.monotonic()
    out = BashTool(LOCAL).execute(command="echo before; sleep 15 & sleep 15", description="t", timeout=2)
    elapsed = time.monotonic() - started
    assert out["exit_code"] == 124 and "Timeout after 2s" in out["stderr"]
    assert elapsed < 10, elapsed
    assert "before" in out["stdout"]


def test_timeout_kills_background_children(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = BashTool(LOCAL).execute(command="(sleep 4; echo late > late.txt) & sleep 15", description="t",
                                  timeout=1)
    assert out["exit_code"] == 124
    time.sleep(6)
    assert not (tmp_path / "late.txt").exists(), "an orphaned child kept running after the timeout"


def test_normal_command_still_works(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = BashTool(LOCAL).execute(command="echo out; echo err >&2; exit 3", description="t", timeout=20)
    assert {k: out[k] for k in ("exit_code", "stdout", "stderr")} == {"exit_code": 3, "stdout": "out\n",
                                                                        "stderr": "err\n"}
    assert out["shell"]


def test_detached_background_job_survives_a_normal_finish(tmp_path, monkeypatch):
    """Only a timeout ends the tree: a server started with its output redirected keeps running."""
    monkeypatch.chdir(tmp_path)
    started = time.monotonic()
    out = BashTool(LOCAL).execute(command="(sleep 2; echo x > alive.txt) >/dev/null 2>&1 &", description="t",
                                  timeout=20)
    assert out["exit_code"] == 0 and time.monotonic() - started < 10
    deadline = time.monotonic() + 15
    while not (tmp_path / "alive.txt").exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert (tmp_path / "alive.txt").exists()


def _py() -> str:
    return sys.executable.replace("\\", "/")


def test_launched_gui_like_child_does_not_block_the_tool(tmp_path, monkeypatch):
    """A launcher (cmd /c start msedge ..., xdg-open, open) leaves a long-lived child that inherited the
    output pipes. The tool must return when the command itself is done, and must not kill that child."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "child.py").write_text(
        "import pathlib, time\ntime.sleep(4)\npathlib.Path('alive2.txt').write_text('x')\ntime.sleep(20)\n")
    (tmp_path / "launcher.py").write_text(
        "import subprocess, sys\nsubprocess.Popen([sys.executable, 'child.py'], close_fds=False)\n")
    cmd = f'"{_py()}" launcher.py; echo launched'
    started = time.monotonic()
    out = BashTool(LOCAL).execute(command=cmd, description="t", timeout=60)
    elapsed = time.monotonic() - started
    assert out["exit_code"] == 0 and "launched" in out["stdout"], out
    assert elapsed < 3.5, elapsed
    deadline = time.monotonic() + 15
    while not (tmp_path / "alive2.txt").exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert (tmp_path / "alive2.txt").exists(), "the launched child was killed"
