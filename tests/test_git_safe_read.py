# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-opus-5-5
# Licensed under MIT License
"""The auto-allowed git read operations never run commands from the repository's own config.

A repository (e.g. unpacked from an archive) can carry a .git/config with core.fsmonitor, filter
drivers, diff.external or textconv commands; git status / diff would run them.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from runtimelib.config import Config
from runtimelib.tools import git as git_mod
from runtimelib.tools.git import GitTool

LOCAL = Config({"cli": {"unsafe_local_tools": True}})

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def evil_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    marks = tmp_path / "marks"
    marks.mkdir()
    m = marks.as_posix()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        _git(repo, *args)
    (repo / "f.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "c")
    (repo / ".gitattributes").write_text("*.txt diff=evil filter=evil\n", encoding="utf-8")
    _git(repo, "config", "core.fsmonitor", f"touch {m}/fsmonitor; :")
    _git(repo, "config", "filter.evil.clean", f"touch {m}/clean; cat")
    _git(repo, "config", "filter.evil.process", f"touch {m}/process; :")
    _git(repo, "config", "diff.evil.textconv", f"touch {m}/textconv; cat")
    _git(repo, "config", "diff.external", f"touch {m}/external; :")
    _git(repo, "config", "log.showSignature", "true")
    _git(repo, "config", "gpg.program", f"touch {m}/gpg; :")
    (repo / "f.txt").write_text("a\nb\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    return repo, marks


def test_plain_git_runs_the_planted_commands(evil_repo):
    """The attack is real: plain `git diff` in this repo runs repo-configured commands."""
    repo, marks = evil_repo
    subprocess.run(["git", "diff"], cwd=repo, capture_output=True)
    assert sorted(p.name for p in marks.iterdir())


@pytest.mark.parametrize("op, kw", [
    ("status", {}),
    ("diff", {}),
    ("diff", {"files": ["f.txt"]}),
    ("diff", {"staged": True}),
    ("log", {"max_count": 3, "oneline": False}),
    ("log", {}),
    ("branch", {"all_branches": True}),
])
def test_read_ops_run_no_repo_commands(evil_repo, op, kw):
    repo, marks = evil_repo
    out = GitTool(LOCAL).execute(operation=op, **kw)
    assert out["exit_code"] == 0, out
    assert sorted(p.name for p in marks.iterdir()) == []
    if op == "diff" and not kw.get("staged"):
        assert "+b" in out["stdout"]
    if op == "status":
        assert "f.txt" in out["stdout"]


def test_user_wide_filters_still_apply(evil_repo, tmp_path, monkeypatch):
    """Only the repository's own (local) filter drivers are switched off; a global one is the user's."""
    repo, marks = evil_repo
    home_cfg = tmp_path / "global.gitconfig"
    home_cfg.write_text("[filter \"mine\"]\n\tclean = cat\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home_cfg))
    pairs = git_mod.repo_filter_overrides(shutil.which("git"), repo, git_mod.git_env(()))
    keys = {k for k, _ in pairs}
    assert "filter.evil.clean" in keys and "filter.evil.process" in keys
    assert not any(k.startswith("filter.mine.") for k in keys)


def test_git_has_a_timeout(evil_repo):
    with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["git"], 1)):
        out = GitTool(LOCAL).execute(operation="log")
    assert out["exit_code"] == 124 and "timed out" in out["stderr"]


def test_every_git_call_passes_a_timeout_and_no_fsmonitor(evil_repo):
    seen = []

    def fake_run(args, **kw):
        seen.append((args, kw))
        return subprocess.CompletedProcess(args, 0, "", "")

    with mock.patch("subprocess.run", fake_run):
        for op, kw in (("status", {}), ("add", {"files": ["f.txt"]}), ("commit", {"message": "m"}),
                       ("checkout", {"branch": "x"})):
            GitTool(LOCAL).execute(operation=op, **kw)
    for args, kw in seen:
        assert kw.get("timeout"), args
        env = kw["env"]
        n = int(env["GIT_CONFIG_COUNT"])
        cfg = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(n)}
        assert cfg.get("core.fsmonitor") == "false", args


def test_git_binary_in_the_workspace_is_not_used(evil_repo):
    repo, _ = evil_repo
    planted = repo / ("git.exe" if git_mod.os.name == "nt" else "git")
    planted.write_bytes(b"not git")
    seen = {}

    def fake_run(args, **kw):
        seen["exe"] = kw.get("executable")
        return subprocess.CompletedProcess(args, 0, "", "")

    with mock.patch("subprocess.run", fake_run):
        GitTool(LOCAL).execute(operation="log")
    assert seen["exe"] and Path(seen["exe"]).resolve() != planted.resolve()


def test_filter_override_is_what_stops_the_clean_filter(evil_repo, monkeypatch):
    """Control: without the per-repo filter overrides the planted filter driver would run on diff."""
    repo, marks = evil_repo
    monkeypatch.setattr(git_mod, "repo_filter_overrides", lambda *a, **k: [])
    GitTool(LOCAL).execute(operation="diff")
    assert {"clean", "process"} & {p.name for p in marks.iterdir()}
