# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License
"""The portable install script: one folder, runnable launcher, safe uninstall."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def _uninstall_cmd_text():
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        import install_ai_rex
    finally:
        sys.path.pop(0)
    return install_ai_rex.UNINSTALL_CMD.replace("\n", "\r\n")


def _wait_gone(path, seconds=20):
    import time
    end = time.monotonic() + seconds
    while path.exists() and time.monotonic() < end:
        time.sleep(0.2)
    return not path.exists()


RUN_UNINSTALL = ["cmd", "/d", "/c", ".\\uninstall.cmd"]  # cwd = the install folder

# Folder names that broke the old script: ")" closed its parenthesised block, "&" split commands,
# "%" was expanded again by the child cmd.
ODD_NAMES = ["a (x86) b", "a&b", "100%PATH%x", "x&echo INJECTED&rem x", "c^d!e"]


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe only")
@pytest.mark.parametrize("name", ODD_NAMES)
def test_uninstall_cmd_handles_odd_paths(tmp_path, name):
    dest = tmp_path / name
    dest.mkdir()
    (dest / ".ai-rex-install").write_text("x")
    (dest / "lib").mkdir()
    (dest / "uninstall.cmd").write_bytes(_uninstall_cmd_text().encode())
    r = subprocess.run(RUN_UNINSTALL, cwd=dest, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "INJECTED" not in r.stdout.replace(name, "")
    assert _wait_gone(dest), (r.stdout, r.stderr)
    assert tmp_path.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe only")
@pytest.mark.parametrize("name", ["plain", "a (x86) b", "a&b"])
def test_uninstall_cmd_refuses_without_marker(tmp_path, name):
    dest = tmp_path / name
    dest.mkdir()
    (dest / "keep.txt").write_text("x")
    (dest / "uninstall.cmd").write_bytes(_uninstall_cmd_text().encode())
    r = subprocess.run(RUN_UNINSTALL, cwd=dest, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "refusing" in r.stdout, (r.stdout, r.stderr)
    import time
    time.sleep(2)
    assert (dest / "keep.txt").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="the shell is recorded on Windows only")
def test_install_records_shell_and_install_cmd_redoes_it(tmp_path):
    import json
    dest = tmp_path / "ai-rex"
    r = _run(str(dest), "--shell", "cmd")
    assert r.returncode == 0, r.stderr
    tools = json.loads((dest / "ai-rex.json").read_text(encoding="utf-8"))["tools"]
    assert tools["shell"] == "cmd" and tools["shell_path"].lower().endswith("cmd.exe")
    (dest / "state").mkdir()
    (dest / "state" / "keep.txt").write_text("x", encoding="utf-8")
    assert _run(str(dest), "--shell", "powershell").returncode == 0  # update keeps state/ and the file
    assert (dest / "state" / "keep.txt").exists()
    assert json.loads((dest / "ai-rex.json").read_text(encoding="utf-8"))["tools"]["shell"] == "powershell"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    r = subprocess.run(["cmd", "/d", "/c", str(dest / "install.cmd"), "--shell", "cmd"], capture_output=True,
                       text=True, env=env, timeout=60, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((dest / "ai-rex.json").read_text(encoding="utf-8"))["tools"]["shell"] == "cmd"
