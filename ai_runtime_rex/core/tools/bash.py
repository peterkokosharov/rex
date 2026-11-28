# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: nvidia nemotron ultra, claude-opus-5-5, claude-sonnet-5-5
# Licensed under MIT License

"""Shell command tool ("bash"): the Docker guard, or locally Git Bash / bash / PowerShell / cmd.exe."""

from __future__ import annotations

import os
import re
import shutil
import time
from typing import Any, Mapping

from .base import Tool, ToolSchema
from ..guard import get_guard_client
from ..config import Config
from ..procutil import kill_tree, popen_group_kwargs, safe_which

NO_BASH_WINDOWS = ("ai-rex needs Git Bash on Windows: install Git for Windows from "
                   "https://git-scm.com/download/win")


def find_bash() -> str | None:
    """Absolute path of the bash that runs local commands, or None.

    Never cmd.exe. On Windows the WSL launcher (System32 / WindowsApps ``bash.exe``)
    is skipped: rex needs Git Bash. Elsewhere ``bash`` on PATH, else ``/bin/sh``.
    """
    found = safe_which("bash")
    if os.name == "nt":
        if found and not found.lower().endswith(".exe"):
            found = None  # bash.bat / bash.cmd would run through cmd.exe
        if found and not any(x in found.lower().replace("\\", "/") for x in ("/system32/", "/windowsapps/")):
            return found
        roots = [os.environ.get(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData")]
        git = safe_which("git")
        if git:
            roots.append(os.path.dirname(os.path.dirname(git)))
        for root in roots:
            for rel in ("Git/bin/bash.exe", "Programs/Git/bin/bash.exe", "bin/bash.exe"):
                cand = os.path.join(root, rel) if root else ""
                if cand and os.path.isfile(cand):
                    return cand
        return None
    return found or shutil.which("sh") or "/bin/sh"


BASH_DESCRIPTION = (
    "Run one shell command (bash; Git Bash on Windows) in the workspace; returns exit code, stdout, stderr.\n"
    "Use it for builds, tests, linters, version checks and quick inspections; read the output before the "
    "next step (errors are usually at the end; pipe long output through tail -n 60).\n"
    "Default timeout 30 s: pass timeout=600 for builds and test runs.\n"
    "Prefer fileops for listing, reading and searching files.\n"
    'Example: {"command": "mvn -q -o test", "description": "run the unit tests", "timeout": 600}'
)

# ---------------------------------------------------------------- shell choice
#
# The tool keeps its public name "bash", but the shell that runs the command is chosen explicitly:
# config tools.shell / env AI_REX_SHELL / flag --shell = auto | bash | powershell | cmd.
# auto: Windows -> Git Bash, else PowerShell (pwsh, then Windows PowerShell 5.1), else cmd.exe;
# Linux/macOS -> bash, else sh. The description, the result and the banner name the shell that runs.

SHELL_CHOICES = ("auto", "bash", "powershell", "cmd")
ENV_SHELL = "AI_REX_SHELL"

# Git Bash converts a lone "/c" argument for native programs into "C:/", so `cmd.exe /c x` started an
# interactive cmd that waited on the console. These wrappers pass single-letter switches through.
_BASH_PRELUDE = ('cmd() { local a=() x; for x in "$@"; do case "$x" in /[A-Za-z]) a+=("/$x");; *) a+=("$x");; '
                 'esac; done; command cmd.exe "${a[@]}"; }; cmd.exe() { cmd "$@"; }')
_PS_PRELUDE = ("$ProgressPreference = 'SilentlyContinue'; "
               "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
               "$OutputEncoding = [System.Text.Encoding]::UTF8; ")


class Shell:
    """One shell that runs tool commands: kind (bash, sh, powershell, cmd), executable and a label."""

    def __init__(self, kind: str, path: str, label: str) -> None:
        self.kind, self.path, self.label = kind, path, label

    def __repr__(self) -> str:
        return f"Shell({self.kind!r}, {self.path!r}, {self.label!r})"

    @property
    def encoding(self) -> str:
        if self.kind == "powershell":
            return "utf-8"  # set by _PS_PRELUDE
        if self.kind == "cmd" and os.name == "nt":
            return "oem"
        import locale
        return locale.getpreferredencoding(False) or "utf-8"

    def command_line(self, command: str, env: dict[str, str]) -> list[str] | str:
        """argv (or, for cmd.exe, the exact Windows command line) that runs ``command``; may add to ``env``."""
        if self.kind in ("bash", "sh"):
            if os.name == "nt":
                # Git Bash re-parses the Windows command line (expands `@file`, mangles quotes): the text
                # travels in the environment and the argv stays constant.
                env["REX_COMMAND"] = command
                env["REX_PRELUDE"] = _BASH_PRELUDE
                return [self.path, "-c", 'eval "$REX_PRELUDE"; eval "$REX_COMMAND"']
            return [self.path, "-c", command]
        if self.kind == "powershell":
            import base64
            script = _PS_PRELUDE + command + "\nif ($LASTEXITCODE) { exit $LASTEXITCODE }"
            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            return [self.path, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
        # cmd.exe: /s strips exactly the outer quotes, the command text stays as written
        return f'"{self.path}" /d /s /c "{command}"'

    def note(self) -> str:
        """One line for the tool description and the system prompt: which shell, which syntax."""
        if self.kind == "powershell":
            return (f"Commands run in {self.label}: use PowerShell syntax, not bash (Get-ChildItem, "
                    "Select-String, $env:NAME, ';' between commands).")
        if self.kind == "cmd":
            return ("Commands run in cmd.exe: use cmd syntax, not bash (dir, type, findstr, %NAME%, "
                    "'&&' between commands).")
        if os.name == "nt":
            return (f"Commands run in {self.label} (bash syntax; Windows paths via cygpath -w; "
                    "open a file for the user with: start file.html).")
        return f"Commands run in {self.label} (POSIX shell syntax)."


def find_powershell(which: Any = None) -> tuple[str, str] | None:
    """(path, label) of pwsh (PowerShell 7) or Windows PowerShell 5.1, or None."""
    which = which or safe_which
    for name, label in (("pwsh", "PowerShell 7"), ("powershell", "Windows PowerShell 5.1")):
        path = which(name)
        if path and (os.name != "nt" or path.lower().endswith(".exe")):
            return path, label
    if os.name == "nt":
        cand = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows",
                            "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if os.path.isfile(cand):
            return cand, "Windows PowerShell 5.1"
    return None


def find_cmd() -> str | None:
    if os.name != "nt":
        return None
    cand = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "cmd.exe")
    return cand if os.path.isfile(cand) else None


def shell_choice(config: Any = None, environ: Mapping[str, str] | None = None) -> str:
    """auto | bash | powershell | cmd from config tools.shell, else env AI_REX_SHELL; anything else is auto."""
    value = config.get("tools.shell") if config is not None else None
    if not value:
        value = (os.environ if environ is None else environ).get(ENV_SHELL)
    value = str(value or "auto").strip().lower()
    return value if value in SHELL_CHOICES else "auto"


def resolve_shell(choice: str = "auto", nt: bool | None = None, bash: Any = None, powershell: Any = None,
                  cmd: Any = None) -> Shell | None:
    """The shell for ``choice``, or None when that shell is not installed (finders injectable for tests)."""
    nt = (os.name == "nt") if nt is None else nt
    bash = bash or find_bash
    powershell = powershell or find_powershell
    cmd = cmd or find_cmd

    def get_bash() -> Shell | None:
        path = bash()
        if not path:
            return None
        if nt:
            return Shell("bash", path, "Git Bash")
        return Shell("sh" if os.path.basename(path) in ("sh", "dash") else "bash", path,
                     os.path.basename(path))

    def get_ps() -> Shell | None:
        found = powershell()
        return Shell("powershell", found[0], found[1]) if found else None

    def get_cmd() -> Shell | None:
        path = cmd() if nt else None
        return Shell("cmd", path, "cmd.exe") if path else None

    if choice == "bash":
        return get_bash()
    if choice == "powershell":
        return get_ps()
    if choice == "cmd":
        return get_cmd()
    if nt:
        return get_bash() or get_ps() or get_cmd()
    return get_bash()


def describe(shell: Shell | None) -> str:
    """The tool description for ``shell`` (the shell line comes first)."""
    if shell is None or shell.kind in ("bash", "sh"):
        body = BASH_DESCRIPTION
    else:
        name = "PowerShell" if shell.kind == "powershell" else "cmd.exe"
        tail = "| Select-Object -Last 60" if shell.kind == "powershell" else "a shorter output"
        body = (f"Run one {name} command in the workspace; returns exit code, stdout, stderr.\n"
                "Use it for builds, tests, linters, version checks and quick inspections; read the output "
                f"before the next step (errors are usually at the end; keep long output short with {tail}).\n"
                "Default timeout 30 s: pass timeout=600 for builds and test runs.\n"
                "Prefer fileops for listing, reading and searching files.\n"
                'Example: {"command": "mvn -q -o test", "description": "run the unit tests", "timeout": 600}')
    return (shell.note() + "\n" + body) if shell is not None else body


class BashTool(Tool):
    """Bash command execution tool."""

    def __init__(self, config: Config | None = None) -> None:
        local = bool(config and config.get("cli.unsafe_local_tools"))
        self.shell_choice = shell_choice(config)
        self.shell: Shell | None = resolve_shell(self.shell_choice) if local else None
        self.cancel_event: Any = None  # set by the agent loop: Ctrl+C ends the running command
        schema = ToolSchema(
            name="bash",
            description=describe(self.shell),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description of what the command does",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds",
                        "default": 30,
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory",
                        "default": ".",
                    },
                    "env": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Environment variables",
                    },
                },
                "required": ["command", "description"],
            },
        )
        super().__init__("bash", describe(self.shell), schema)
        self._config = config
        self._guard = None

    def _get_guard(self):
        if self._guard is None:
            self._guard = get_guard_client(self._config)
        return self._guard

    def execute(self, **kwargs: Any) -> Any:
        if self._config and self._config.get("cli.unsafe_local_tools"):
            return self._exec_local(**kwargs)
        guard = self._get_guard()
        return guard.bash_exec(
            command=kwargs["command"],
            timeout=kwargs.get("timeout", 30),
            cwd=kwargs.get("cwd", "."),
            env=kwargs.get("env"),
        )

    def _exec_local(self, **kwargs: Any) -> dict[str, Any]:
        import subprocess
        command = kwargs["command"]
        timeout = kwargs.get("timeout", 30)
        from pathlib import Path
        from .fileops import resolve_in_workspace
        try:
            cwd = resolve_in_workspace(kwargs.get("cwd", ".") or ".", Path.cwd().resolve())
        except (PermissionError, ValueError) as e:
            return {"exit_code": 1, "stdout": "", "stderr": f"[bash: {e}]"}
        if blocks_git_push(command):
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": "[bash: 'git push' is not allowed from the agent; push by hand]",
            }
        shell = self.shell or resolve_shell(self.shell_choice)
        if shell is None:
            if self.shell_choice == "bash" and os.name == "nt":
                why = NO_BASH_WINDOWS
            elif self.shell_choice == "auto":
                why = NO_BASH_WINDOWS + " (no PowerShell or cmd.exe found either)" if os.name == "nt" else "no shell found"
            else:
                why = f"shell '{self.shell_choice}' not found"
            return {"exit_code": 1, "stdout": "", "stderr": f"[bash: {why}]"}
        env_extra = {str(k): str(v) for k, v in (kwargs.get("env") or {}).items()}
        env = scrubbed_env(extra=env_extra)
        try:
            argv = shell.command_line(command, env)
            code, out, err, ended = run_tree(argv, cwd=cwd, env=env, timeout=timeout, cancel=self.cancel_event,
                                             encoding=shell.encoding)
            if ended == "interrupted":
                return {"exit_code": 130, "stdout": out, "stderr": err + "\n[interrupted]", "shell": shell.label}
            if ended:
                return {"exit_code": 124, "stdout": out, "stderr": err + f"\nTimeout after {timeout}s",
                        "shell": shell.label}
            return {"exit_code": code, "stdout": out, "stderr": err, "shell": shell.label}
        except Exception as e:
            return {"exit_code": 1, "stdout": "", "stderr": str(e), "shell": shell.label}


def run_tree(argv: list[str] | str, cwd: Any, env: dict[str, str], timeout: float, cancel: Any = None,
             encoding: str | None = None) -> tuple[int, str, str, Any]:
    """Run ``argv`` in its own process group; on timeout kill the whole tree (``sleep 9 &`` included).

    Returns ``(exit_code, stdout, stderr, ended)``; ``ended`` is falsy, "timeout" or "interrupted" (``cancel``,
    a threading.Event, is polled every 0.1 s: Ctrl+C ends the command's tree within a moment). The call ends when the shell ends, not when the
    pipes close: a launched program (``cmd /c start msedge x.html``, ``xdg-open``, ``server &``) inherits
    the output pipes and may live for hours. Its output is collected for DRAIN_SECONDS more, then the call
    returns and that program keeps running (only a timeout of the shell itself kills the tree).
    """
    import subprocess
    import threading
    kw = popen_group_kwargs()
    job = _WinJob.create() if os.name == "nt" else None
    if job is not None:
        kw["creationflags"] = kw.get("creationflags", 0) | _CREATE_SUSPENDED
    try:
        proc = subprocess.Popen(argv, shell=False, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
        if job is not None:
            job.adopt(proc)  # assigns, then resumes: every child the command starts is in the job
    except BaseException:
        if job is not None:
            job.close()
        raise

    chunks: dict[str, list[bytes]] = {"out": [], "err": []}
    lock = threading.Lock()

    def reader(stream: Any, key: str) -> None:
        try:
            while True:
                data = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
                if not data:
                    break
                with lock:
                    chunks[key].append(data)
        except (OSError, ValueError):
            pass

    readers = [threading.Thread(target=reader, args=(s, k), daemon=True)
               for s, k in ((proc.stdout, "out"), (proc.stderr, "err"))]
    for r in readers:
        r.start()

    def collected() -> tuple[str, str]:
        with lock:
            return (_decode(b"".join(chunks["out"]), encoding),
                    _decode(b"".join(chunks["err"]), encoding))

    def drain(seconds: float) -> None:
        end = time.monotonic() + seconds
        for r in readers:
            r.join(max(0.0, end - time.monotonic()))

    def kill() -> None:
        if job is not None:
            job.terminate()
        kill_tree(proc)

    try:
        deadline = time.monotonic() + float(timeout)
        code = None
        while code is None:
            if cancel is not None and cancel.is_set():
                kill()
                drain(2)
                out, err = collected()
                return 130, out, err, "interrupted"
            left = deadline - time.monotonic()
            if left <= 0:
                kill()
                drain(5)
                out, err = collected()
                return 124, out, err, "timeout"
            try:
                code = proc.wait(timeout=min(0.1, left))
            except subprocess.TimeoutExpired:
                pass
        drain(DRAIN_SECONDS)  # late output; a launched GUI program may hold the pipes for good
        out, err = collected()
        return code, out, err, False
    except BaseException:
        kill()
        raise
    finally:
        if job is not None:
            job.close()


DRAIN_SECONDS = 1.0


def _decode(data: bytes, encoding: str | None = None) -> str:
    """Bytes from the command as text, like text=True did (locale encoding unless given, universal newlines)."""
    import locale
    enc = encoding or locale.getpreferredencoding(False) or "utf-8"
    try:
        text = data.decode(enc, errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


_CREATE_SUSPENDED = 0x00000004


class _WinJob:
    """A Windows job object holding one command and all its children (Git Bash detaches background
    processes from the parent/child tree, so ``taskkill /T`` alone misses ``sleep 9 &``).

    The process starts suspended, joins the job, then resumes: nothing escapes before it is in the job.
    Closing the handle does not kill anything (no KILL_ON_JOB_CLOSE): a server the command deliberately
    left running stays, only a timeout ends the job. Any failure falls back to ``kill_tree`` alone.
    """

    def __init__(self, k32: Any, ntdll: Any, handle: int) -> None:
        self._k32, self._ntdll, self._handle = k32, ntdll, handle

    @classmethod
    def create(cls) -> "_WinJob | None":
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            ntdll = ctypes.WinDLL("ntdll")
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k32.AssignProcessToJobObject.restype = wintypes.BOOL
            k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k32.TerminateJobObject.restype = wintypes.BOOL
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
            ntdll.NtResumeProcess.restype = ctypes.c_long
            handle = k32.CreateJobObjectW(None, None)
        except (OSError, AttributeError, ImportError):
            return None
        return cls(k32, ntdll, handle) if handle else None

    def adopt(self, proc: Any) -> None:
        ph = int(proc._handle)  # noqa: SLF001 - the Popen process handle (Windows)
        if not self._k32.AssignProcessToJobObject(self._handle, ph):
            self.close()
        if self._ntdll.NtResumeProcess(ph) != 0:  # must run even when it could not join the job
            kill_tree(proc)
            raise OSError("could not start the command (resume failed)")

    def terminate(self) -> None:
        if self._handle:
            self._k32.TerminateJobObject(self._handle, 124)

    def close(self) -> None:
        if self._handle:
            self._k32.CloseHandle(self._handle)
            self._handle = 0


# Variables that look like credentials are never passed to agent commands.
# This scrub and the `git push` check below are guard rails against accidents,
# NOT a security boundary: on Linux a command can still read the parent's
# environment from /proc/<ppid>/environ. Real isolation needs the Docker guard.
_SECRET_NAME_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL",
                      "AUTH", "COOKIE", "DATABASE_URL")
_SECRET_SUFFIXES = ("_PAT",)
_SECRET_PREFIXES = ("AI_REX_LITELLM", "RUNTIME_LITELLM", "LITELLM")
# Exact names that match a secret pattern but are needed and hold no secret
# themselves (XAUTHORITY is the path X11 GUI programs need to find their cookie).
_NOT_SECRET_NAMES = frozenset({"XAUTHORITY"})

# `git push` check. This is a guard rail against an accidental push by the
# model, NOT a security boundary: aliases, scripts or `sh -c` with quoting
# tricks can still reach the network. Real isolation needs the Docker guard.
_GIT_PUSH_RE = re.compile(r"\bgit\b[^;&|\n]*\bpush\b")


def is_secret_env_name(name: str) -> bool:
    upper = name.upper()
    if upper in _NOT_SECRET_NAMES:
        return False
    return (
        upper.startswith(_SECRET_PREFIXES)
        or upper.endswith(_SECRET_SUFFIXES)
        or any(part in upper for part in _SECRET_NAME_PARTS)
    )


def is_secret_env(name: str, value: str) -> bool:
    """Name looks like a credential, or a proxy URL carries user:pass@."""
    if is_secret_env_name(name):
        return True
    return "PROXY" in name.upper() and "@" in (value or "")


def scrubbed_env(base: Mapping[str, str] | None = None, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy of the environment without credential-like variables.

    PATH, HOME/USERPROFILE, SYSTEMROOT, TEMP/TMP, LANG and other ordinary
    variables are kept so commands still work on Windows, Linux and macOS.
    """
    source = dict(os.environ if base is None else base)
    if extra:
        source.update(extra)
    return {k: v for k, v in source.items() if not is_secret_env(k, v)}


def blocks_git_push(command: str) -> bool:
    return bool(_GIT_PUSH_RE.search(command or ""))


def to_text(value: Any) -> str:
    """Output of TimeoutExpired is bytes on POSIX even with text=True."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def create_bash_tool(config: Config | None = None) -> BashTool:
    return BashTool(config)