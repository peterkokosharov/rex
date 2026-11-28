# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov
# AI models: claude-sonnet-5-5, claude-opus-5-5
"""REX.commit_and_merge must hand the sandbox manager the agent id, not the handle."""

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtimelib.guard.sandbox import SandboxManager, WorktreeHandle
from runtimelib.rex import REX


def test_commit_and_merge_passes_agent_id(tmp_path):
    seen = []
    mgr = SimpleNamespace(commit_and_merge=lambda agent_id, msg: seen.append((agent_id, msg)) or {"merged": True})
    rex = SimpleNamespace(sandbox_manager=mgr)
    handle = WorktreeHandle(agent_id="coder-1", path=Path(tmp_path), branch="coder-1-branch")
    assert REX.commit_and_merge(rex, handle, "msg") == {"merged": True}
    assert seen == [("coder-1", "msg")]


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "-c", "commit.gpgsign=false", *args], cwd=cwd, check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_commit_and_merge_end_to_end_with_real_manager(tmp_path, monkeypatch):
    """The handle path through REX reaches main: a commit made in the agent worktree is merged.

    Fails with the old bug (a WorktreeHandle passed where the id belongs): the manager rejects it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README").write_text("base\n")
    (repo / ".gitignore").write_text(".worktrees/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                 "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "commit.gpgsign",
                 "GIT_CONFIG_VALUE_0": "false"}.items():
        monkeypatch.setenv(k, v)
    mgr = SandboxManager(repo, config={"cleanup_on_merge": False})
    handle = mgr.create_worktree("coder-1")
    (handle.path / "work.txt").write_text("done\n")
    rex = SimpleNamespace(sandbox_manager=mgr)
    result = REX.commit_and_merge(rex, handle, "agent work")
    assert result["merged"] is True and result["branch"] == "coder-1-branch"
    assert (repo / "work.txt").read_text() == "done\n"
