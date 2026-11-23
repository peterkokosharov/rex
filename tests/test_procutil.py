# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""Tests for procutil.safe_which."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from runtimelib import procutil


def _plant(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_planted_binary_in_cwd_is_not_found(tmp_path, monkeypatch):
    _plant(tmp_path, "x.exe")
    _plant(tmp_path, "x")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", str(tmp_path)]))
    assert procutil.safe_which("x") is None


def test_finds_binary_on_real_path_entry(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    exe = _plant(tmp_path / "bin", "x.exe" if os.name == "nt" else "x")
    monkeypatch.chdir(work)
    monkeypatch.setenv("PATH", os.pathsep.join([".", str(exe.parent)]))
    found = procutil.safe_which("x")
    assert found is not None and Path(found).parent == exe.parent


def test_exclude_directory_is_skipped(tmp_path, monkeypatch):
    elsewhere = tmp_path / "ws"
    _plant(elsewhere, "x.exe" if os.name == "nt" else "x")
    monkeypatch.setenv("PATH", str(elsewhere))
    assert procutil.safe_which("x") is not None
    assert procutil.safe_which("x", exclude=(str(elsewhere),)) is None


def test_name_with_directory_part_is_rejected(tmp_path, monkeypatch):
    exe = _plant(tmp_path, "x.exe" if os.name == "nt" else "x")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert procutil.safe_which(str(exe)) is None


def test_environ_is_not_mutated(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    procutil.safe_which("nothing-here")
    assert "NoDefaultCurrentDirectoryInExePath" not in os.environ
