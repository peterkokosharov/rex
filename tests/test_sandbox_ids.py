# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov
# AI models: claude-sonnet-5-5
"""SandboxManager: ids are path components (no traversal) and the singleton is created once."""

import threading
import time

import pytest

from runtimelib.guard import sandbox as sb

BAD = ["../evil", "a/b", "a\\b", "..", ".", "", "/abs", "x\x00y", "a b", "-rf"]


@pytest.fixture
def mgr(tmp_path):
    return sb.SandboxManager(tmp_path, config=object())


@pytest.mark.parametrize("bad", BAD)
def test_create_worktree_rejects_bad_id(mgr, bad):
    with pytest.raises(ValueError):
        mgr.create_worktree(bad)


@pytest.mark.parametrize("bad", BAD)
def test_mounts_reject_bad_ids(mgr, tmp_path, bad):
    with pytest.raises(ValueError):
        mgr.get_container_mounts(bad, "sess1")
    with pytest.raises(ValueError):
        mgr.get_container_mounts("agent1", bad)
    assert not (tmp_path.parent / "evil").exists()


@pytest.mark.parametrize("bad", BAD)
def test_commit_and_merge_rejects_bad_id(mgr, bad):
    with pytest.raises(ValueError):
        mgr.commit_and_merge(bad, "m")


def test_good_ids_still_work(mgr, tmp_path):
    mounts = mgr.get_container_mounts("coder-1a2b3c4d", "0f3e-session_1")
    assert len(mounts) == 3


def test_singleton_created_once_under_threads(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "_sandbox_manager", None)
    created = []

    class Slow(sb.SandboxManager):
        def __init__(self, *a, **k):
            time.sleep(0.05)
            created.append(1)
            super().__init__(*a, **k)

    monkeypatch.setattr(sb, "SandboxManager", Slow)
    got = []
    threads = [threading.Thread(target=lambda: got.append(sb.get_sandbox_manager(object(), tmp_path)))
               for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(created) == 1
    assert len({id(g) for g in got}) == 1
