# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License
"""The portable install script: one folder, runnable launcher, safe uninstall."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_ai_rex.py"


def _run(*args):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, timeout=120)


def test_install_run_uninstall(tmp_path):
    dest = tmp_path / "apps" / "ai-rex"
    r = _run(str(dest))
    assert r.returncode == 0, r.stderr
    launcher = dest / ("ai-rex.cmd" if sys.platform == "win32" else "ai-rex")
    assert str(launcher) in r.stdout and launcher.exists()
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    v = subprocess.run([str(launcher), "--version"], capture_output=True, text=True, env=env, timeout=60)
    assert v.stdout.startswith("ai-rex "), v.stderr
    assert _run(str(dest)).returncode == 0  # update in place
    assert _run("--uninstall", str(dest)).returncode == 0
    assert not dest.exists()


def test_refuses_foreign_folders(tmp_path):
    (tmp_path / "keep.txt").write_text("x")
    assert _run(str(tmp_path)).returncode != 0
    assert _run("--uninstall", str(tmp_path)).returncode != 0
    assert (tmp_path / "keep.txt").exists()


def test_uninstall_scripts_check_the_marker(tmp_path):
    dest = tmp_path / "x"
    assert _run(str(dest)).returncode == 0
    assert ".ai-rex-install" in (dest / "uninstall.cmd").read_text()
    assert ".ai-rex-install" in (dest / "uninstall.sh").read_text()
    assert b"\r\n" not in (dest / "ai-rex").read_bytes() and b"\r\n" in (dest / "ai-rex.cmd").read_bytes()


def test_install_onto_a_file_is_refused(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    r = _run(str(f))
    assert r.returncode != 0 and "Traceback" not in r.stderr and f.read_text() == "x"


def test_gitattributes_keep_launcher_line_ends():
    import shutil
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        import pytest
        pytest.skip("no git checkout")
    out = subprocess.run(["git", "check-attr", "eol", "ai-rex", "deploy.sh", "ai-rex.cmd"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    assert "ai-rex: eol: lf" in out and "deploy.sh: eol: lf" in out and "ai-rex.cmd: eol: crlf" in out
