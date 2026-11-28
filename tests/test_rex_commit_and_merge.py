# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov
# AI models: claude-sonnet-5-5
"""REX.commit_and_merge must hand the sandbox manager the agent id, not the handle."""

from pathlib import Path
from types import SimpleNamespace

from runtimelib.guard.sandbox import SandboxManager, WorktreeHandle
from runtimelib.rex import REX


def test_commit_and_merge_passes_agent_id(tmp_path):
    seen = []
    mgr = SimpleNamespace(commit_and_merge=lambda agent_id, msg: seen.append((agent_id, msg)) or {"merged": True})
    rex = SimpleNamespace(sandbox_manager=mgr)
    handle = WorktreeHandle(agent_id="coder-1", path=Path(tmp_path), branch="coder-1-branch")
    assert REX.commit_and_merge(rex, handle, "msg") == {"merged": True}
    assert seen == [("coder-1", "msg")]


def test_commit_and_merge_with_real_manager_does_not_raise_type_error(tmp_path):
    mgr = SandboxManager(tmp_path, config=object())
    rex = SimpleNamespace(sandbox_manager=mgr)
    handle = WorktreeHandle(agent_id="coder-1", path=tmp_path, branch="coder-1-branch")
    try:
        REX.commit_and_merge(rex, handle, "msg")
    except TypeError as exc:  # the bug: a WorktreeHandle used as a path component
        raise AssertionError(exc)
    except Exception:
        pass  # no git worktree here: any git failure is fine
