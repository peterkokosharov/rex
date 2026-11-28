# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-opus-5-5
# Licensed under MIT License

"""SandboxManager - git worktrees, Docker mounts, merge locks."""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import GuardClient, get_guard_client
from ..config import Config


# ASCII only, max 128 chars, no leading dot/dash, no trailing dot (Windows drops it: "abc." == "abc").
_SAFE_ID = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,126}[A-Za-z0-9_-])?")


def _check_id(value: str, what: str = "id") -> str:
    """An agent/session id becomes a directory name and a git branch: one safe path component."""
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value) or ".." in value:
        raise ValueError(f"invalid {what}: {value!r}")
    return value


@dataclass
class MountSpec:
    """Docker bind mount specification with absolute host paths."""
    source: str      # ABSOLUTE host path (required by Docker) - CONCRETE PATH, NOT GLOB
    target: str      # Container path
    read_only: bool = False
    type: str = "bind"


@dataclass
class WorktreeHandle:
    """Handle to a git worktree."""
    agent_id: str
    path: Path
    branch: str


try:  # POSIX
    import fcntl as _fcntl
    _msvcrt = None
except ImportError:  # Windows has no fcntl; msvcrt gives a byte-range lock
    _fcntl = None
    import msvcrt as _msvcrt


def _lock_nb(lock_file: Any) -> None:
    """Take an exclusive non-blocking lock; raise BlockingIOError if held."""
    if _fcntl is not None:
        _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        return
    lock_file.seek(0)
    try:
        _msvcrt.locking(lock_file.fileno(), _msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        raise BlockingIOError(str(exc)) from exc


def _unlock(lock_file: Any) -> None:
    if _fcntl is not None:
        _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_UN)
        return
    lock_file.seek(0)
    try:
        _msvcrt.locking(lock_file.fileno(), _msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


class FileMergeLock:
    """File-based merge lock using fcntl (single machine)."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file: Optional[Any] = None

    def acquire(self, timeout: float = 30.0):
        """Acquire cross-process merge lock using fcntl."""
        lock_file = open(self.lock_path, "w")
        start = time.monotonic()
        while True:
            try:
                _lock_nb(lock_file)
                self._lock_file = lock_file
                return _FileLockContext(lock_file)
            except BlockingIOError:
                if time.monotonic() - start > timeout:
                    lock_file.close()
                    raise TimeoutError("Could not acquire merge lock")
                time.sleep(0.1)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        if self._lock_file:
            _unlock(self._lock_file)
            self._lock_file.close()
            self._lock_file = None


class _FileLockContext:
    """Context manager for file lock."""
    def __init__(self, lock_file: Any):
        self.lock_file = lock_file

    def __enter__(self):
        return self

    def __exit__(self, *args):
        _unlock(self.lock_file)
        self.lock_file.close()


class RedisMergeLock:
    """Redis-based distributed merge lock (multi-machine/container)."""

    def __init__(self, redis_url: str, lock_name: str):
        import redis.asyncio as redis
        self.client = redis.from_url(redis_url)
        self.lock_name = f"lock:{lock_name}"
        self.lock_timeout = 30  # seconds
        self._acquired = False

    async def _acquire(self, timeout: float = 30.0):
        start = time.monotonic()
        while True:
            # SET key value NX EX timeout
            acquired = await self.client.set(
                self.lock_name,
                str(os.getpid()),
                nx=True,
                ex=self.lock_timeout
            )
            if acquired:
                return _RedisLockContext(self.client, self.lock_name)

            if time.monotonic() - start > timeout:
                raise TimeoutError("Could not acquire distributed merge lock")

            await asyncio.sleep(0.1)

    def acquire(self, timeout: float = 30.0):
        """Acquire distributed lock using Redis SET NX with TTL."""
        # Run async in sync context
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        return loop.run_until_complete(self._acquire(timeout))


class _RedisLockContext:
    """Context manager for Redis lock."""
    def __init__(self, client, lock_name: str):
        self.client = client
        self.lock_name = lock_name

    def __enter__(self):
        return self

    def __exit__(self, *args):
        import asyncio

        async def _release():
            # Only delete if we own it (Lua script for atomic check-and-delete)
            lua_script = """
            if redis.call("GET", KEYS[1]) == ARGV[1] then
                return redis.call("DEL", KEYS[1])
            else
                return 0
            end
            """
            await self.client.eval(lua_script, 1, self.lock_name, str(os.getpid()))

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        loop.run_until_complete(_release())


class SandboxManager:
    """Manages isolated sandboxes for agents using git worktrees and Docker."""

    def __init__(self, project_root: Path, config: Config | None = None, redis_url: str | None = None):
        self.project_root = project_root
        self.config = config or Config.load()
        self.worktrees_dir = project_root / ".worktrees"
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        self.redis_url = redis_url

        # CRITICAL: Use file-based lock for single-machine,
        # or Redis-based lock for distributed deployments.
        if redis_url:
            self._merge_lock = RedisMergeLock(redis_url, f"merge:{project_root.name}")
        else:
            self._merge_lock = FileMergeLock(self.worktrees_dir / ".merge.lock")

    def create_worktree(self, agent_id: str, base_branch: str = "main") -> WorktreeHandle:
        """Create isolated git worktree for agent."""
        _check_id(agent_id, "agent_id")
        worktree_path = self.worktrees_dir / agent_id

        # Create worktree if it doesn't exist
        if not worktree_path.exists():
            import subprocess
            # git worktree add -b {agent_id}-branch {worktree_path} {base_branch}
            subprocess.run([
                "git", "worktree", "add",
                "-b", f"{agent_id}-branch",
                str(worktree_path),
                base_branch
            ], cwd=self.project_root, check=True, capture_output=True)

        return WorktreeHandle(
            agent_id=agent_id,
            path=worktree_path,
            branch=f"{agent_id}-branch"
        )

    def get_container_mounts(self, agent_id: str, session_id: str) -> list[MountSpec]:
        """Return Docker bind mount specs for guard container.

        CRITICAL FIX for wildcard paths:
        Docker does NOT support glob/wildcard bind mounts.
        Solution: Pre-create the agent worktree directory, then bind mount
        the specific absolute path (not a glob).
        """
        _check_id(agent_id, "agent_id")
        _check_id(session_id, "session_id")
        worktree_path = self.worktrees_dir / agent_id
        worktree_path.mkdir(parents=True, exist_ok=True)

        # Also ensure session directory exists for RW mount
        session_rw = self.project_root / ".runtime" / "sessions" / session_id / "agents" / agent_id
        session_rw.mkdir(parents=True, exist_ok=True)

        return [
            MountSpec(
                source=str(worktree_path.resolve()),      # ABSOLUTE host path (concrete, not glob)
                target="/workspace",                       # Container path
                read_only=False
            ),
            MountSpec(
                source=str(self.project_root.resolve()),   # ABSOLUTE host path
                target="/workspace/main",                  # Read-only main tree
                read_only=True
            ),
            MountSpec(
                source=str(session_rw.resolve()),
                target="/workspace/.runtime/sessions",
                read_only=False
            ),
        ]

    def commit_and_merge(self, agent_id: str, message: str) -> dict:
        """Serialize all merges to main via cross-process/distributed lock."""
        _check_id(agent_id, "agent_id")
        with self._merge_lock.acquire(timeout=30.0) as lock:
            # 1. Commit in worktree
            self._git_commit_in_worktree(agent_id, message)
            # 2. Merge to main (single writer)
            result = self._git_merge_to_main(agent_id)
            # 3. Clean up worktree (optional, configurable)
            if self.config.get("cleanup_on_merge", False):
                self._cleanup_worktree(agent_id)
            return result

    def _git_commit_in_worktree(self, agent_id: str, message: str):
        import subprocess
        worktree = self.worktrees_dir / agent_id
        # git -C {worktree} add -A && git -C {worktree} commit -m "{message}"
        subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
        subprocess.run(["git", "commit", "-m", message], cwd=worktree, check=True)

    def _git_merge_to_main(self, agent_id: str) -> dict:
        import subprocess
        worktree = self.worktrees_dir / agent_id
        branch = f"{agent_id}-branch"
        # git -C {worktree} merge main --no-ff -m "Merge {agent_id}"
        # git -C {project_root} merge {agent_id}-branch
        subprocess.run(["git", "merge", "main", "--no-ff", "-m", f"Merge {agent_id}"], cwd=worktree, check=True)
        result = subprocess.run(["git", "merge", branch], cwd=self.project_root, check=True, capture_output=True, text=True)
        return {"merged": True, "branch": branch, "output": result.stdout}

    def _cleanup_worktree(self, agent_id: str):
        import subprocess
        worktree = self.worktrees_dir / agent_id
        branch = f"{agent_id}-branch"
        subprocess.run(["git", "worktree", "remove", str(worktree)], cwd=self.project_root, check=True)
        subprocess.run(["git", "branch", "-D", branch], cwd=self.project_root, check=True)


def get_sandbox_manager(config: Config | None = None, project_root: Path | None = None, redis_url: str | None = None) -> SandboxManager:
    """Factory function to get or create sandbox manager."""
    global _sandbox_manager
    with _sandbox_manager_lock:
        if _sandbox_manager is None:
            _sandbox_manager = SandboxManager(
                project_root=project_root or Path.cwd(),
                config=config,
                redis_url=redis_url
            )
        return _sandbox_manager


_sandbox_manager: SandboxManager | None = None
_sandbox_manager_lock = threading.Lock()