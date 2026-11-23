# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""Tests for the render_diagram tool. A fake merman-cli (a python script) stands in for merman."""

from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from runtimelib.tools import diagram
from runtimelib.tools.diagram import DiagramTool, build_command, find_merman, sanitize_name

FAKE = '''import sys
a = sys.argv[1:]
src = a[1]
out = a[a.index("-o") + 1]
fmt = a[a.index("-f") + 1]
text = open(src, encoding="utf-8").read()
if "FAIL" in text:
    sys.stderr.write("parse error " * 500)
    sys.exit(2)
if "SLOW" in text:
    import time
    time.sleep(30)
open(out, "w", encoding="utf-8").write("<fmt:%s args:%s>" % (fmt, " ".join(a)))
'''


def make_fake(directory: Path) -> Path:
    """A fake merman-cli: a launcher that runs fake_merman.py with this interpreter."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "fake_merman.py").write_text(FAKE, encoding="utf-8")
    if os.name == "nt":
        exe = directory / "merman-cli.cmd"
        exe.write_text(f'@"{sys.executable}" "%~dp0fake_merman.py" %*\r\n', encoding="utf-8")
    else:
        exe = directory / "merman-cli"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$(dirname "$0")/fake_merman.py" "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def fake_binary(monkeypatch, tmp_path):
    exe = make_fake(tmp_path / "bin")
    monkeypatch.setattr(diagram, "find_merman", lambda: str(exe))
    return exe


def test_sanitize_name():
    assert sanitize_name("My Flow/../x!") == "My-Flow-x"
    assert sanitize_name("..\\..\\evil") == "evil"
    assert sanitize_name("") == ""
    assert sanitize_name(None) == ""
    assert sanitize_name("a" * 200) == "a" * 60


def test_render_ok_svg_and_markdown(ws, fake_binary):
    r = DiagramTool().execute(source="graph TD; A-->B", name="Login Flow")
    assert r["ok"] is True
    assert r["path"] == "diagrams/Login-Flow.svg"
    assert r["markdown"] == "![Login-Flow](diagrams/Login-Flow.svg)"
    out = (ws / "diagrams" / "Login-Flow.svg").read_text(encoding="utf-8")
    assert "<fmt:svg" in out and "-q" in out


def test_png_and_hash_name(ws, fake_binary):
    r = DiagramTool().execute(source="graph TD; A-->B", format="png")
    assert r["ok"] and r["path"].startswith("diagrams/") and r["path"].endswith(".png")
    assert len(Path(r["path"]).stem) == 12
    again = DiagramTool().execute(source="graph TD; A-->B", format="png")
    assert again["path"] == r["path"]


def test_bad_format_and_empty_source(ws, fake_binary):
    assert DiagramTool().execute(source="x", format="gif")["ok"] is False
    assert DiagramTool().execute(source="  ")["ok"] is False


def test_path_confinement(ws, fake_binary, tmp_path):
    r = DiagramTool().execute(source="graph TD; A-->B", name="../../escape")
    assert r["ok"] and r["path"] == "diagrams/escape.svg"
    assert not (tmp_path / "escape.svg").exists()
    assert (ws / "diagrams" / "escape.svg").exists()


def test_diagrams_dir_symlink_escape_rejected(ws, fake_binary, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, ws / "diagrams", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    r = DiagramTool().execute(source="graph TD; A-->B", name="x")
    assert r["ok"] is False and "outside" in r["error"]
    assert not list(outside.iterdir())


def test_find_merman_order(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    exe_name = "merman-cli.exe" if os.name == "nt" else "merman-cli"
    (home / exe_name).write_text("x")
    monkeypatch.setenv("MERMAN_HOME", str(home))
    monkeypatch.setattr(diagram, "safe_which", lambda n: "/usr/bin/merman-cli")
    assert find_merman() == str(home / exe_name)
    monkeypatch.setenv("MERMAN_HOME", str(tmp_path / "nowhere"))
    assert find_merman() == "/usr/bin/merman-cli"
    monkeypatch.delenv("MERMAN_HOME")
    assert find_merman() == "/usr/bin/merman-cli"
    monkeypatch.setattr(diagram, "safe_which", lambda n: None)
    assert find_merman() is None


def test_not_found_message(ws, monkeypatch):
    monkeypatch.setattr(diagram, "find_merman", lambda: None)
    r = DiagramTool().execute(source="graph TD; A-->B")
    assert r["ok"] is False and "MERMAN_HOME" in r["error"]


def test_build_command():
    assert build_command("m", "in.mmd", "out.svg", "png") == ["m", "render", "in.mmd", "-o", "out.svg", "-f", "png", "-q"]


def test_render_error_truncated(ws, fake_binary):
    r = DiagramTool().execute(source="FAIL")
    assert r["ok"] is False
    assert "parse error" in r["error"] and "exit 2" in r["error"]
    assert len(r["error"]) < diagram.MAX_ERROR_CHARS + 100


def test_timeout(ws, fake_binary, monkeypatch):
    monkeypatch.setattr(diagram, "TIMEOUT_SECONDS", 1)
    t0 = time.monotonic()
    r = DiagramTool().execute(source="SLOW")
    assert r["ok"] is False and "timed out" in r["error"]
    assert time.monotonic() - t0 < 10


def test_symlinked_target_rejected(ws, fake_binary, tmp_path):
    (ws / "src").mkdir()
    victim = ws / "src" / "victim.txt"
    victim.write_text("keep")
    (ws / "diagrams").mkdir()
    try:
        os.symlink(victim, ws / "diagrams" / "x.svg")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    r = DiagramTool().execute(source="graph TD; A-->B", name="x")
    assert r["ok"] is False and "symlink" in r["error"]
    assert victim.read_text() == "keep"


def test_symlinked_diagrams_dir_inside_workspace_rejected(ws, fake_binary):
    (ws / "src").mkdir()
    try:
        os.symlink(ws / "src", ws / "diagrams", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    r = DiagramTool().execute(source="graph TD; A-->B", name="x")
    assert r["ok"] is False and "symlink" in r["error"]
    assert not list((ws / "src").iterdir())


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_fifo_target_rejected_without_hanging(ws, fake_binary):
    (ws / "diagrams").mkdir()
    os.mkfifo(ws / "diagrams" / "x.svg")
    t0 = time.monotonic()
    r = DiagramTool().execute(source="graph TD; A-->B", name="x")
    assert r["ok"] is False
    assert time.monotonic() - t0 < 10


@pytest.mark.parametrize("bad", ["CON", "nul", "com1", "LPT9"])
def test_reserved_names_prefixed(ws, fake_binary, bad):
    r = DiagramTool().execute(source="graph TD; A-->B", name=bad)
    assert r["ok"] and r["path"] == f"diagrams/d-{bad}.svg"


def test_unresolved_rel_path(ws, fake_binary):
    r = DiagramTool().execute(source="graph TD; A-->B", name="ok")
    assert r["path"] == "diagrams/ok.svg"


def test_merman_home_without_binary(ws, monkeypatch, tmp_path):
    monkeypatch.setenv("MERMAN_HOME", str(tmp_path))
    monkeypatch.setattr(diagram, "safe_which", lambda n: None)
    r = DiagramTool().execute(source="graph TD; A-->B")
    assert f"MERMAN_HOME={tmp_path} has no merman-cli" in r["error"]


def test_empty_merman_home_reported(ws, monkeypatch):
    monkeypatch.setenv("MERMAN_HOME", "")
    monkeypatch.setattr(diagram, "safe_which", lambda n: None)
    r = DiagramTool().execute(source="graph TD; A-->B")
    assert "MERMAN_HOME is set but empty" in r["error"]


def test_find_merman_ignores_binary_in_cwd(ws, monkeypatch, tmp_path):
    monkeypatch.delenv("MERMAN_HOME", raising=False)
    make_fake(ws)  # planted merman-cli in the workspace (= cwd)
    far = tmp_path / "elsewhere"
    far.mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", str(ws), str(far)]))
    assert find_merman() is None
    exe = make_fake(far / "bin")
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", str(ws), str(exe.parent)]))
    assert Path(find_merman()).parent == exe.parent


def test_registered_and_described():
    from runtimelib.permissions.policy import DefaultPermissionPolicy
    t = DiagramTool()
    assert t.name == "render_diagram"
    assert "Markdown image" in t.description and t.description.count(".") == 1  # one sentence
    assert DefaultPermissionPolicy().check("render_diagram", "", {"_role": "CODER"}).name == "ALLOW"


@pytest.mark.skipif(not os.environ.get("MERMAN_HOME"), reason="MERMAN_HOME not set")
def test_real_merman(ws):
    r = DiagramTool().execute(source="graph TD; A-->B", name="real")
    assert r["ok"], r
    assert (ws / "diagrams" / "real.svg").stat().st_size > 0
