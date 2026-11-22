#!/usr/bin/env python3

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Tests for the explicit ``!`` shell escape in the rex CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from runtimelib.cli import is_shell_line, run_shell, shell_command

REPO_ROOT = Path(__file__).parent.parent


def test_detect_shell_line():
    assert is_shell_line("! pwd")
    assert is_shell_line("  ! git status")
    assert not is_shell_line("please run pwd")
    assert not is_shell_line("/shell pwd")


def test_strip_bang_and_normalize_bash():
    assert shell_command("  ! git status  ") == "git status"
    assert shell_command("! /bin/bash pwd") == "/bin/bash -c pwd"
    assert shell_command("! /bin/bash -c 'pwd'") == "/bin/bash -c 'pwd'"
    assert shell_command("! bash script.sh") == "bash script.sh"


def test_run_shell_output_and_exit_code(tmp_path):
    ok = run_shell("printf 'hello\\n'", cwd=tmp_path)
    assert ok.ok
    assert ok.stdout == "hello\n"
    assert ok.exit_code == 0

    failed = run_shell("printf 'oops\\n' >&2; exit 7", cwd=tmp_path)
    assert not failed.ok
    assert failed.stderr.startswith("oops")
    assert failed.exit_code == 7

    cwd = run_shell("pwd", cwd=tmp_path)
    assert cwd.stdout.strip() == str(tmp_path)


def test_one_shot_cli_runs_shell_without_llm_config():
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("RUNTIME_"):
            env.pop(key)
    result = subprocess.run(
        [sys.executable, "-m", "runtime", "! printf 'bash-ok\\n'"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "bash-ok" in result.stdout


def test_one_shot_cli_with_bin_bash_prefix():
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("RUNTIME_"):
            env.pop(key)
    result = subprocess.run(
        [sys.executable, "-m", "runtime", "! /bin/bash printf 'via-bash\\n'"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "via-bash" in result.stdout
