# SPDX-License-Identifier: MIT
"""Docker Guard - JSON-RPC entrypoint for sandboxed tool execution."""

from __future__ import annotations

import json
import os
import sys
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from runtime.kikolib import posix_utils


class GuardError(Exception):
    """Base guard error."""
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class JSONRPCServer:
    """JSON-RPC 2.0 server over stdio."""

    def __init__(self) -> None:
        self._methods: dict[str, Callable] = {}
        self._running = False
        self._lock = threading.Lock()

    def register(self, name: str) -> Callable[[Callable], Callable]:
        def decorator(func: Callable) -> Callable:
            self._methods[name] = func
            return func
        return decorator

    def handle_request(self, line: str) -> str | None:
        """Handle a single JSON-RPC request. Returns response or None for notifications."""
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            return self._error_response(None, -32700, f"Parse error: {e}")

        # Notification (no id) - no response
        if "id" not in req:
            self._handle_notification(req)
            return None

        # Request
        return self._handle_request(req)

    def _handle_notification(self, req: dict[str, Any]) -> None:
        method = req.get("method")
        if method in self._methods:
            try:
                self._methods[method](**req.get("params", {}))
            except Exception:
                pass  # Ignore errors in notifications

    def _handle_request(self, req: dict[str, Any]) -> str:
        req_id = req["id"]
        method = req.get("method")
        params = req.get("params", {})

        if method not in self._methods:
            return self._error_response(req_id, -32601, f"Method not found: {method}")

        try:
            result = self._methods[method](**params)
            return self._success_response(req_id, result)
        except GuardError as e:
            return self._error_response(req_id, e.code, e.message)
        except Exception as e:
            return self._error_response(req_id, -32603, f"Internal error: {type(e).__name__}")

    def _success_response(self, req_id: Any, result: Any) -> str:
        return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}, ensure_ascii=False)

    def _error_response(self, req_id: Any, code: int, message: str) -> str:
        return json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}, ensure_ascii=False)

    def serve_stdio(self) -> None:
        """Serve requests from stdin, write responses to stdout."""
        self._running = True
        posix_utils.sd_notify("READY=1")

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            response = self.handle_request(line)
            if response is not None:
                print(response, flush=True)


# --- Tool Handlers ---

def _safe_path(base: Path, path: str) -> Path:
    """Resolve path relative to base, ensure it's within base."""
    target = (base / path).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise GuardError(-32001, f"Path traversal attempt: {path}")
    return target


def _check_network_policy(url: str) -> bool:
    """Check if network access is allowed for URL."""
    # TODO: implement policy check
    allowed_domains = os.environ.get("GUARD_ALLOWED_DOMAINS", "").split(",")
    if not allowed_domains or allowed_domains == [""]:
        return False
    from urllib.parse import urlparse
    host = urlparse(url).hostname
    return any(host and host.endswith(domain) for domain in allowed_domains)


def create_server(workspace: Path) -> JSONRPCServer:
    """Create configured JSON-RPC server."""
    server = JSONRPCServer()

    @server.register("bash.exec")
    def bash_exec(command: str, timeout: int = 30, cwd: str = ".", env: dict[str, str] | None = None) -> dict[str, Any]:
        work_dir = _safe_path(workspace, cwd)
        run_env = {**os.environ, **(env or {})}
        try:
            proc = subprocess.run(
                command, shell=True, cwd=work_dir, env=run_env,
                capture_output=True, text=True, timeout=timeout
            )
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except subprocess.TimeoutExpired as e:
            return {
                "exit_code": -1,
                "stdout": e.stdout or "",
                "stderr": f"Timeout after {timeout}s",
            }
        except Exception as e:
            raise GuardError(-32002, f"Bash execution failed: {e}")

    @server.register("fs.read")
    def fs_read(path: str, encoding: str = "utf-8") -> dict[str, Any]:
        target = _safe_path(workspace, path)
        try:
            content = target.read_text(encoding=encoding)
            return {"content": content}
        except FileNotFoundError:
            raise GuardError(-32003, f"File not found: {path}")
        except Exception as e:
            raise GuardError(-32004, f"Read failed: {e}")

    @server.register("fs.write")
    def fs_write(path: str, content: str, mode: int = 0o644) -> dict[str, Any]:
        target = _safe_path(workspace, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(content, encoding="utf-8")
            target.chmod(mode)
            return {"ok": True}
        except Exception as e:
            raise GuardError(-32005, f"Write failed: {e}")

    @server.register("fs.edit")
    def fs_edit(path: str, old_str: str, new_str: str, replace_all: bool = False) -> dict[str, Any]:
        target = _safe_path(workspace, path)
        try:
            content = target.read_text(encoding="utf-8")
            if old_str not in content:
                raise GuardError(-32006, "old_str not found in file")
            if not replace_all and content.count(old_str) > 1:
                raise GuardError(-32007, "old_str occurs multiple times, use replace_all=true")
            new_content = content.replace(old_str, new_str) if replace_all else content.replace(old_str, new_str, 1)
            target.write_text(new_content, encoding="utf-8")
            return {"ok": True}
        except GuardError:
            raise
        except Exception as e:
            raise GuardError(-32008, f"Edit failed: {e}")

    @server.register("fs.list")
    def fs_list(path: str = ".", recursive: bool = False) -> dict[str, Any]:
        target = _safe_path(workspace, path)
        try:
            if recursive:
                files = []
                for root, dirs, filenames in os.walk(target):
                    for f in filenames:
                        rel = Path(root).relative_to(target) / f
                        files.append(str(rel))
                return {"files": files}
            else:
                return {"files": [f.name for f in target.iterdir()]}
        except Exception as e:
            raise GuardError(-32009, f"List failed: {e}")

    @server.register("fs.glob")
    def fs_glob(pattern: str, root: str = ".") -> dict[str, Any]:
        import glob
        target = _safe_path(workspace, root)
        try:
            matches = glob.glob(str(target / pattern), recursive="**" in pattern)
            rel_matches = [str(Path(m).relative_to(target)) for m in matches]
            return {"matches": rel_matches}
        except Exception as e:
            raise GuardError(-32010, f"Glob failed: {e}")

    @server.register("fs.grep")
    def fs_grep(pattern: str, path: str = ".", include: str | None = None, exclude: str | None = None) -> dict[str, Any]:
        import re
        target = _safe_path(workspace, path)
        try:
            regex = re.compile(pattern)
            matches = []
            for root, dirs, filenames in os.walk(target):
                for f in filenames:
                    if include and not Path(f).match(include):
                        continue
                    if exclude and Path(f).match(exclude):
                        continue
                    filepath = Path(root) / f
                    try:
                        content = filepath.read_text(encoding="utf-8", errors="ignore")
                        for i, line in enumerate(content.splitlines(), 1):
                            if regex.search(line):
                                rel = filepath.relative_to(target)
                                matches.append({"file": str(rel), "line": i, "content": line.strip()})
                    except Exception:
                        continue
            return {"matches": matches}
        except Exception as e:
            raise GuardError(-32011, f"Grep failed: {e}")

    @server.register("net.fetch")
    def net_fetch(url: str, method: str = "GET", headers: dict[str, str] | None = None,
                  body: str | None = None, timeout: int = 30) -> dict[str, Any]:
        if not _check_network_policy(url):
            raise GuardError(-32012, f"Network access denied for: {url}")

        from runtime.kikolib import http_client
        client = http_client.client()
        try:
            resp = client.request(method, url, headers=headers, body=body.encode() if body else None, timeout=timeout)
            return {
                "status": resp.status,
                "headers": dict(resp.headers.items()),
                "body": resp.read().decode("utf-8", errors="replace"),
            }
        except Exception as e:
            raise GuardError(-32013, f"Network fetch failed: {e}")

    # --- Git methods ---

    @server.register("git.status")
    def git_status(cwd: str = ".") -> dict[str, Any]:
        """Get git status."""
        work_dir = _safe_path(workspace, cwd)
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain"], cwd=work_dir,
                capture_output=True, text=True, timeout=10
            )
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except Exception as e:
            raise GuardError(-32020, f"Git status failed: {e}")

    @server.register("git.diff")
    def git_diff(cwd: str = ".", staged: bool = False, files: list[str] | None = None) -> dict[str, Any]:
        """Get git diff."""
        work_dir = _safe_path(workspace, cwd)
        cmd = ["git", "diff"]
        if staged:
            cmd.append("--cached")
        if files:
            cmd.extend(["--"] + files)
        try:
            proc = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, timeout=10)
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except Exception as e:
            raise GuardError(-32021, f"Git diff failed: {e}")

    @server.register("git.add")
    def git_add(files: list[str], cwd: str = ".") -> dict[str, Any]:
        """Stage files for commit."""
        work_dir = _safe_path(workspace, cwd)
        cmd = ["git", "add"] + files
        try:
            proc = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, timeout=10)
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except Exception as e:
            raise GuardError(-32022, f"Git add failed: {e}")

    @server.register("git.commit")
    def git_commit(message: str, cwd: str = ".", author: str | None = None) -> dict[str, Any]:
        """Create a commit."""
        work_dir = _safe_path(workspace, cwd)
        cmd = ["git", "commit", "-m", message]
        if author:
            cmd.extend(["--author", author])
        try:
            proc = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, timeout=10)
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except Exception as e:
            raise GuardError(-32023, f"Git commit failed: {e}")

    @server.register("git.log")
    def git_log(cwd: str = ".", max_count: int = 10, oneline: bool = True) -> dict[str, Any]:
        """Get git log."""
        work_dir = _safe_path(workspace, cwd)
        cmd = ["git", "log"]
        if oneline:
            cmd.append("--oneline")
        cmd.extend(["-n", str(max_count)])
        try:
            proc = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, timeout=10)
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except Exception as e:
            raise GuardError(-32024, f"Git log failed: {e}")

    @server.register("git.branch")
    def git_branch(cwd: str = ".", all_branches: bool = False) -> dict[str, Any]:
        """List branches."""
        work_dir = _safe_path(workspace, cwd)
        cmd = ["git", "branch"]
        if all_branches:
            cmd.append("-a")
        try:
            proc = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, timeout=10)
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except Exception as e:
            raise GuardError(-32025, f"Git branch failed: {e}")

    @server.register("git.checkout")
    def git_checkout(branch: str, create: bool = False, cwd: str = ".") -> dict[str, Any]:
        """Checkout or create branch."""
        work_dir = _safe_path(workspace, cwd)
        cmd = ["git", "checkout"]
        if create:
            cmd.append("-b")
        cmd.append(branch)
        try:
            proc = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, timeout=10)
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except Exception as e:
            raise GuardError(-32026, f"Git checkout failed: {e}")

    return server


def main() -> int:
    """Entry point for guard container."""
    workspace = Path(os.environ.get("GUARD_WORKSPACE", "/workspace")).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    server = create_server(workspace)
    server.serve_stdio()
    return 0


if __name__ == "__main__":
    sys.exit(main())