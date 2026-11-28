# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-opus-5-5
# Licensed under MIT License
"""The bash tool's shell is chosen explicitly (auto | bash | powershell | cmd), named in the description and the
result, and a running command ends promptly on Ctrl+C (the loop's interrupt event)."""
from __future__ import annotations

import os
import threading
import time
from unittest.mock import MagicMock

import pytest

from runtimelib.agent import create_agent_loop
from runtimelib.config import Config
from runtimelib.llm import ChatCompletionChunk
from runtimelib.session import SessionManager
from runtimelib.tools import bash as bash_mod
from runtimelib.tools.base import Tool, ToolSchema
from runtimelib.tools.bash import BashTool, describe, find_powershell, resolve_shell, shell_choice

LOCAL = Config({"cli": {"unsafe_local_tools": True}})
NT = os.name == "nt"


def local(shell: str) -> Config:
    return Config({"cli": {"unsafe_local_tools": True}, "tools": {"shell": shell}})


# ---------------------------------------------------------------- detection order (fake finders)

FB = lambda: "C:/Git/bin/bash.exe"  # noqa: E731
FPS = lambda: ("C:/ps/powershell.exe", "Windows PowerShell 5.1")  # noqa: E731
FCMD = lambda: "C:/Windows/System32/cmd.exe"  # noqa: E731
NONE = lambda: None  # noqa: E731


@pytest.mark.parametrize("finders, kind", [
    ((FB, FPS, FCMD), "bash"),
    ((NONE, FPS, FCMD), "powershell"),
    ((NONE, NONE, FCMD), "cmd"),
    ((NONE, NONE, NONE), None),
])
def test_auto_order_on_windows(finders, kind):
    shell = resolve_shell("auto", nt=True, bash=finders[0], powershell=finders[1], cmd=finders[2])
    assert (shell.kind if shell else None) == kind


def test_auto_on_posix_is_bash_only():
    assert resolve_shell("auto", nt=False, bash=lambda: "/bin/bash", powershell=FPS, cmd=FCMD).kind == "bash"
    assert resolve_shell("auto", nt=False, bash=NONE, powershell=FPS, cmd=FCMD) is None


@pytest.mark.parametrize("choice, kind", [("bash", "bash"), ("powershell", "powershell"), ("cmd", "cmd")])
def test_explicit_choice(choice, kind):
    assert resolve_shell(choice, nt=True, bash=FB, powershell=FPS, cmd=FCMD).kind == kind
    assert resolve_shell(choice, nt=True, bash=NONE, powershell=NONE, cmd=NONE) is None


def test_pwsh_before_windows_powershell():
    paths = {"pwsh": "C:/pwsh/pwsh.exe", "powershell": "C:/ps/powershell.exe"}
    assert find_powershell(lambda n: paths.get(n))[1] == "PowerShell 7"
    assert find_powershell(lambda n: {"powershell": paths["powershell"]}.get(n))[1] == "Windows PowerShell 5.1"


def test_shell_choice_sources():
    assert shell_choice(local("PowerShell"), {}) == "powershell"
    assert shell_choice(Config({}), {"AI_REX_SHELL": "cmd"}) == "cmd"
    assert shell_choice(local("cmd"), {"AI_REX_SHELL": "bash"}) == "cmd"  # __main__ puts flag/env into config
    assert shell_choice(local("zsh"), {}) == "auto"


def test_description_names_the_shell():
    ps = resolve_shell("powershell", nt=True, bash=NONE, powershell=FPS, cmd=FCMD)
    cmd = resolve_shell("cmd", nt=True, bash=NONE, powershell=NONE, cmd=FCMD)
    gb = resolve_shell("bash", nt=True, bash=FB, powershell=NONE, cmd=NONE)
    assert describe(ps).startswith("Commands run in Windows PowerShell 5.1: use PowerShell syntax, not bash")
    assert "cmd syntax, not bash" in describe(cmd)
    assert "Git Bash" in describe(gb) and "bash syntax" in describe(gb)
    assert describe(ps) != describe(cmd) != describe(gb)


def test_tool_description_follows_the_setting(monkeypatch):
    monkeypatch.setattr(bash_mod, "find_powershell", FPS)
    tool = BashTool(local("powershell"))
    assert "PowerShell syntax" in tool.schema.description and "PowerShell syntax" in tool.description
    assert tool.shell.kind == "powershell"


# ---------------------------------------------------------------- live, on this machine

def test_live_auto_runs_bash_syntax(tmp_path, monkeypatch):
    if resolve_shell("auto") is None:
        pytest.skip("no shell")
    monkeypatch.chdir(tmp_path)
    tool = BashTool(LOCAL)
    if NT:
        assert tool.shell.kind == "bash" and tool.shell.path.lower().endswith("bash.exe")
    out = tool.execute(command="echo $(echo hi)", description="t", timeout=30)
    assert out["stdout"] == "hi\n" and out["exit_code"] == 0
    assert out["shell"] == tool.shell.label


@pytest.mark.skipif(not NT, reason="Windows: Git Bash path conversion")
def test_live_cmd_slash_c_works_from_git_bash(tmp_path, monkeypatch):
    """Git Bash turned a lone /c into C:/, so cmd.exe /c started an interactive cmd."""
    monkeypatch.chdir(tmp_path)
    started = time.monotonic()
    out = BashTool(LOCAL).execute(command="cmd.exe /c echo hi", description="t", timeout=30)
    assert out["stdout"].strip() == "hi" and time.monotonic() - started < 10, out


@pytest.mark.skipif(find_powershell() is None, reason="no PowerShell")
def test_live_powershell(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tool = BashTool(local("powershell"))
    out = tool.execute(command="Write-Output hi; Write-Output 'ä €'", description="t", timeout=60)
    assert out["exit_code"] == 0 and out["stdout"].splitlines() == ["hi", "ä €"], out
    assert "PowerShell" in out["shell"]
    assert tool.execute(command="cmd /c exit 3", description="t", timeout=60)["exit_code"] == 3


@pytest.mark.skipif(not NT, reason="cmd.exe only on Windows")
def test_live_cmd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = BashTool(local("cmd")).execute(command='echo hi && echo "a b"', description="t", timeout=30)
    assert [ln.rstrip() for ln in out["stdout"].splitlines()] == ["hi", '"a b"'] and out["shell"] == "cmd.exe", out


# ---------------------------------------------------------------- interrupt

def test_interrupt_ends_a_blocking_command(tmp_path, monkeypatch):
    if resolve_shell("auto") is None:
        pytest.skip("no shell")
    monkeypatch.chdir(tmp_path)
    tool = BashTool(LOCAL)
    tool.cancel_event = threading.Event()
    threading.Timer(1.0, tool.cancel_event.set).start()
    started = time.monotonic()
    out = tool.execute(command="sleep 60", description="t", timeout=120)
    assert time.monotonic() - started < 4
    assert out["exit_code"] == 130 and "[interrupted]" in out["stderr"]


class _Blocker(Tool):
    """Stands in for the bash tool: blocks until its cancel_event is set."""

    def __init__(self) -> None:
        super().__init__("blocker", "blocks", ToolSchema("blocker", "blocks", {"type": "object", "properties": {}}))
        self.cancel_event = None

    def execute(self, **kwargs):
        for _ in range(600):
            if self.cancel_event is not None and self.cancel_event.is_set():
                return "stopped"
            time.sleep(0.01)
        return "never interrupted"


class _LLM:
    default_model = "m"

    def __init__(self):
        self.n = 0

    def chat_completion(self, messages, **kwargs):
        self.n += 1
        delta = ({"tool_calls": [{"id": "c1", "type": "function", "function": {"name": "blocker", "arguments": "{}"}}]}
                 if self.n == 1 else {"content": "done"})
        yield ChatCompletionChunk(id="1", model="m", created=0, choices=[{"delta": delta, "finish_reason": None}])


def test_loop_hands_its_interrupt_event_to_the_tool():
    session = MagicMock(spec=SessionManager)
    session.session_id = "s"
    cfg = Config({"liteLLM": {"base_url": "http://dummy:4000", "api_key": "dummy", "model": "m"},
                  "permissions": {"allow": ["blocker"]}})
    tool = _Blocker()
    loop = create_agent_loop(cfg, llm_client=_LLM(), session_manager=session, tools=[tool])
    loop._quiet = True
    threading.Timer(0.5, loop.interrupt).start()
    started = time.monotonic()
    loop.run("go")
    assert time.monotonic() - started < 3
    assert tool.cancel_event is loop._interrupt_event


def test_cli_accepts_shell_and_trust_flags():
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}
    env["PYTHONPATH"] = str(root)
    for flags in (["--shell", "powershell"], ["--mcp-trust-readonly"], ["--shell", "auto"]):
        r = subprocess.run([sys.executable, "-P", "-m", "runtime", *flags, "--version"], capture_output=True,
                           text=True, env=env, timeout=60)
        assert r.returncode == 0 and r.stdout.startswith("ai-rex "), (flags, r.stderr)
    r = subprocess.run([sys.executable, "-P", "-m", "runtime", "--shell", "zsh", "--version"], capture_output=True,
                       text=True, env=env, timeout=60)
    assert r.returncode != 0
