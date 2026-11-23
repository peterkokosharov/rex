# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""Tests for AGENTS.md standing instructions."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from runtimelib.agent.instructions import (
    MAX_BYTES, PROJECT_NOTE, TRUNCATION_MARKER, load_instructions,
)


@pytest.fixture
def dirs(tmp_path):
    ws = tmp_path / "ws"
    ud = tmp_path / "user"
    ws.mkdir()
    ud.mkdir()
    return ws, ud


def test_missing_files(dirs):
    ws, ud = dirs
    r = load_instructions(ws, ud)
    assert r.paths == [] and r.section() == ""


def test_user_only(dirs):
    ws, ud = dirs
    (ud / "AGENTS.md").write_text("answer in Russian", encoding="utf-8")
    r = load_instructions(ws, ud)
    assert r.paths == [str(ud / "AGENTS.md")]
    assert "answer in Russian" in r.section()
    assert PROJECT_NOTE not in r.section()


def test_project_only_lowercase(dirs):
    ws, ud = dirs
    (ws / "agents.md").write_text("use tabs", encoding="utf-8")
    r = load_instructions(ws, ud)
    assert len(r.paths) == 1 and "use tabs" in r.section()
    assert PROJECT_NOTE in r.section()


def test_both_order(dirs):
    ws, ud = dirs
    (ud / "AGENTS.md").write_text("USERTEXT", encoding="utf-8")
    (ws / "AGENTS.md").write_text("PROJTEXT", encoding="utf-8")
    r = load_instructions(ws, ud)
    s = r.section()
    assert s.index("USERTEXT") < s.index(PROJECT_NOTE) < s.index("PROJTEXT")
    assert r.paths == [str(ud / "AGENTS.md"), str(ws / "AGENTS.md")]


def test_size_cap(dirs):
    ws, ud = dirs
    (ws / "AGENTS.md").write_bytes(b"x" * (MAX_BYTES + 500))
    r = load_instructions(ws, ud)
    assert TRUNCATION_MARKER.strip() in r.project
    assert len(r.project) <= MAX_BYTES + len(TRUNCATION_MARKER)


def test_bom_and_bad_utf8(dirs):
    ws, ud = dirs
    (ws / "AGENTS.md").write_bytes(b"\xef\xbb\xbfhello \xff")
    r = load_instructions(ws, ud)
    assert r.project.startswith("hello") and "\ufeff" not in r.project


def test_symlink_escape_refused(dirs, tmp_path):
    ws, ud = dirs
    outside = tmp_path / "secret.md"
    outside.write_text("SECRET", encoding="utf-8")
    try:
        os.symlink(outside, ws / "AGENTS.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    r = load_instructions(ws, ud)
    assert r.project == "" and r.paths == []
    assert r.warnings and "outside" in r.warnings[0]


def test_explicit_file(dirs, tmp_path):
    ws, ud = dirs
    f = tmp_path / "any" / "mine.txt"
    f.parent.mkdir()
    f.write_text("EXPLICIT", encoding="utf-8")
    (ud / "AGENTS.md").write_text("USERTEXT", encoding="utf-8")
    r = load_instructions(ws, ud, extra_file=str(f))
    assert r.user.index("USERTEXT") < r.user.index("EXPLICIT")
    assert str(f) in r.paths


def test_explicit_file_missing_warns_once(dirs, tmp_path):
    ws, ud = dirs
    missing = str(tmp_path / "nope.md")
    assert len(load_instructions(ws, ud, extra_file=missing).warnings) == 1
    assert load_instructions(ws, ud, extra_file=missing).warnings == []
