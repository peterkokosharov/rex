# SPDX-License-Identifier: MIT
"""Guard client - communicates with Docker guard container via JSON-RPC over stdio."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ..config import Config


class GuardClient:
    """Client for Docker guard container."""

    def __init__(
        self,
        image: str = "runtime/guard:latest",
        workspace: Optional[Path] = None,
        cpus: str = "2.0",
        memory: str = "4G",
        pids_limit: int = 100,
        allowed_domains: str = "",
        network_mode: str = "none",
    ) -> None:
        self.image = image
        self.workspace = workspace or Path.cwd()
        self.cpus = cpus
        self.memory = memory
        self.pids_limit = pids_limit
        self.allowed_domains = allowed_domains
        self.network_mode = network_mode

        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._request_id = 0
        self._responses: dict[int, threading.Event] = {}
        self._results: dict[int, Any] = {}
        self._reader_thread: Optional[threading.Thread] = None
        self._started = False

    @classmethod
    def from_config(cls, config: Config) -> "GuardClient":
        guard_cfg = config.get("docker_guard", {})
        return cls(
            image=guard_cfg.get("image", "runtime/guard:latest"),
            workspace=Path(guard_cfg.get("workspace_mount", "/workspace")).resolve()
                if guard_cfg.get("workspace_mount") else Path.cwd(),
            cpus=guard_cfg.get("cpus", "2.0"),
            memory=guard_cfg.get("memory", "4G"),
            pids_limit=guard_cfg.get("pids_limit", 100),
            allowed_domains=guard_cfg.get("allowed_domains", ""),
            network_mode=guard_cfg.get("network_mode", "none"),
        )

    def start(self) -> None:
        """Start the guard container."""
        with self._lock:
            if self._started:
                return

            # Build docker run command
            cmd = [
                "docker", "run", "--rm", "-i",
                "--name", f"runtime-guard-{os.getpid()}",
                "--cpus", self.cpus,
                "--memory", self.memory,
                "--pids-limit", str(self.pids_limit),
                "--network", self.network_mode,
                "--read-only",
                "--tmpfs", "/tmp",
                "--tmpfs", "/var/tmp",
                "--security-opt", "no-new-privileges=true",
                "--cap-drop", "ALL",
                "--user", "1000:1000",
                "-v", f"{self.workspace}:/workspace:rw",
            ]

            # Add config volumes
            config_dir = Path.home() / ".config" / "runtime"
            if config_dir.exists():
                cmd.extend(["-v", f"{config_dir}:/home/guarduser/.config/runtime:ro"])

            kiko_dir = Path.home() / ".kiko"
            if kiko_dir.exists():
                cmd.extend(["-v", f"{kiko_dir}:/home/guarduser/.kiko:ro"])

            # Environment
            if self.allowed_domains:
                cmd.extend(["-e", f"GUARD_ALLOWED_DOMAINS={self.allowed_domains}"])
            cmd.extend(["-e", "GUARD_WORKSPACE=/workspace"])
            cmd.extend(["-e", f"KIKO_DB_PATH=/home/guarduser/.kiko/kiko.db"])

            cmd.append(self.image)

            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # line buffered
            )

            # Start reader thread
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()

            # Wait for READY notification
            self._wait_ready()
            self._started = True

    def _wait_ready(self, timeout: float = 10.0) -> None:
        """Wait for READY notification from guard."""
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if self._proc and self._proc.poll() is not None:
                stderr = self._proc.stderr.read() if self._proc.stderr else ""
                raise RuntimeError(f"Guard container exited: {stderr}")
            time.sleep(0.1)
        # If we get here, assume it's ready (guard sends READY=1 via sd_notify, not stdout)

    def _read_loop(self) -> None:
        """Read responses from guard stdout."""
        if not self._proc or not self._proc.stdout:
            return

        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Handle response
            if "id" in resp and resp["id"] in self._responses:
                req_id = resp["id"]
                self._results[req_id] = resp
                self._responses[req_id].set()

    def _send_request(self, method: str, params: dict[str, Any], timeout: float = 30.0) -> Any:
        """Send JSON-RPC request and wait for response."""
        if not self._started:
            self.start()

        with self._lock:
            self._request_id += 1
            req_id = self._request_id
            event = threading.Event()
            self._responses[req_id] = event

        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        try:
            if not self._proc or not self._proc.stdin:
                raise RuntimeError("Guard process not running")

            self._proc.stdin.write(json.dumps(request) + "\n")
            self._proc.stdin.flush()

            if not event.wait(timeout):
                raise TimeoutError(f"Guard request {method} timed out after {timeout}s")

            result = self._results.pop(req_id, None)
            if not result:
                raise RuntimeError("No response received")

            if "error" in result:
                raise RuntimeError(f"Guard error: {result['error']['message']}")

            return result.get("result")

        finally:
            self._responses.pop(req_id, None)

    # --- Tool Methods ---

    def bash_exec(self, command: str, timeout: int = 30, cwd: str = ".", env: dict[str, str] | None = None) -> dict[str, Any]:
        return self._send_request("bash.exec", {
            "command": command,
            "timeout": timeout,
            "cwd": cwd,
            "env": env or {},
        }, timeout=timeout + 5)

    def fs_read(self, path: str, encoding: str = "utf-8") -> str:
        result = self._send_request("fs.read", {"path": path, "encoding": encoding})
        return result["content"]

    def fs_write(self, path: str, content: str, mode: int = 0o644) -> None:
        self._send_request("fs.write", {"path": path, "content": content, "mode": mode})

    def fs_edit(self, path: str, old_str: str, new_str: str, replace_all: bool = False) -> None:
        self._send_request("fs.edit", {
            "path": path,
            "old_str": old_str,
            "new_str": new_str,
            "replace_all": replace_all,
        })

    def fs_list(self, path: str = ".", recursive: bool = False) -> list[str]:
        result = self._send_request("fs.list", {"path": path, "recursive": recursive})
        return result["files"]

    def fs_glob(self, pattern: str, root: str = ".") -> list[str]:
        result = self._send_request("fs.glob", {"pattern": pattern, "root": root})
        return result["matches"]

    def fs_grep(self, pattern: str, path: str = ".", include: str | None = None, exclude: str | None = None) -> list[dict[str, Any]]:
        result = self._send_request("fs.grep", {
            "pattern": pattern,
            "path": path,
            "include": include,
            "exclude": exclude,
        })
        return result["matches"]

    def net_fetch(self, url: str, method: str = "GET", headers: dict[str, str] | None = None,
                  body: str | None = None, timeout: int = 30) -> dict[str, Any]:
        return self._send_request("net.fetch", {
            "url": url,
            "method": method,
            "headers": headers or {},
            "body": body,
            "timeout": timeout,
        })

    # --- Git Methods ---

    def git_status(self, cwd: str = ".") -> dict[str, Any]:
        return self._send_request("git.status", {"cwd": cwd}, timeout=15)

    def git_diff(self, cwd: str = ".", staged: bool = False, files: list[str] | None = None) -> dict[str, Any]:
        return self._send_request("git.diff", {
            "cwd": cwd,
            "staged": staged,
            "files": files,
        }, timeout=15)

    def git_add(self, files: list[str], cwd: str = ".") -> dict[str, Any]:
        return self._send_request("git.add", {"files": files, "cwd": cwd}, timeout=15)

    def git_commit(self, message: str, cwd: str = ".", author: str | None = None) -> dict[str, Any]:
        return self._send_request("git.commit", {
            "message": message,
            "cwd": cwd,
            "author": author,
        }, timeout=15)

    def git_log(self, cwd: str = ".", max_count: int = 10, oneline: bool = True) -> dict[str, Any]:
        return self._send_request("git.log", {
            "cwd": cwd,
            "max_count": max_count,
            "oneline": oneline,
        }, timeout=15)

    def git_branch(self, cwd: str = ".", all_branches: bool = False) -> dict[str, Any]:
        return self._send_request("git.branch", {
            "cwd": cwd,
            "all_branches": all_branches,
        }, timeout=15)

    def git_checkout(self, branch: str, create: bool = False, cwd: str = ".") -> dict[str, Any]:
        return self._send_request("git.checkout", {
            "branch": branch,
            "create": create,
            "cwd": cwd,
        }, timeout=15)

    def stop(self) -> None:
        """Stop the guard container."""
        with self._lock:
            if self._proc:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait()
                self._proc = None
                self._started = False

    def __enter__(self) -> "GuardClient":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()


def get_guard_client(config: Optional[Config] = None) -> GuardClient:
    """Get or create global guard client."""
    global _guard_client
    if _guard_client is None:
        if config is None:
            from ..config import get_config
            config = get_config()
        _guard_client = GuardClient.from_config(config)
    return _guard_client


_guard_client: Optional[GuardClient] = None


# Import sandbox module
from .sandbox import (
    SandboxManager,
    MountSpec,
    WorktreeHandle,
    FileMergeLock,
    RedisMergeLock,
    get_sandbox_manager,
)