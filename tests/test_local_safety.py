# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-5-5
# Licensed under MIT License

"""Safety tests for running rex without Docker (local tools) and for
conversation-history invariants. No network, no keys."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from runtimelib.agent import compact_messages, create_agent_loop, ensure_tool_results
from runtimelib.agent.context import CANCELLED_RESULT
from runtimelib.cli.shell import run_shell
from runtimelib.config import Config
from runtimelib.llm import ChatCompletionChunk, ChatMessage, ToolCall
from runtimelib.native import _fallback
from runtimelib.session import SessionManager
from runtimelib.tools.base import Tool, ToolSchema
from runtimelib.tools.bash import BashTool, blocks_git_push, scrubbed_env
from runtimelib.tools.fileops import FileOpsTool
from runtimelib.tools.git import GitTool

LOCAL = Config({"cli": {"unsafe_local_tools": True}})


# ---------------------------------------------------------------- fileops

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "src" / "pkg").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
    (ws / "src" / "b.txt").write_text("b\n", encoding="utf-8")
    (ws / "src" / "pkg" / "c.py").write_text("print('c')\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("secret-outside\n", encoding="utf-8")
    monkeypatch.chdir(ws)
    return ws


def test_fileops_reads_inside_workspace(workspace):
    tool = FileOpsTool(LOCAL)
    assert tool.execute(operation="read", path="src/a.py") == "print('a')\n"
    tool.execute(operation="write", path="new/dir/x.txt", content="hi")
    assert (workspace / "new" / "dir" / "x.txt").read_text(encoding="utf-8") == "hi"


@pytest.mark.parametrize("bad", ["../outside.txt", "src/../../outside.txt", "src/../.."])
def test_fileops_rejects_traversal(workspace, bad):
    tool = FileOpsTool(LOCAL)
    with pytest.raises(PermissionError):
        tool.execute(operation="read", path=bad)
    with pytest.raises(PermissionError):
        tool.execute(operation="write", path=bad + "/x", content="x")
    with pytest.raises(PermissionError):
        tool.execute(operation="list", path=bad)


@pytest.mark.parametrize("bad", ["/etc/passwd", "C:/Windows/win.ini", "C:\\Windows", "\\\\host\\share\\f", "~/x"])
def test_fileops_rejects_absolute(workspace, bad):
    tool = FileOpsTool(LOCAL)
    with pytest.raises(PermissionError):
        tool.execute(operation="read", path=bad)
    with pytest.raises(PermissionError):
        tool.execute(operation="edit", path=bad, old_str="a", new_str="b")
    with pytest.raises(PermissionError):
        tool.execute(operation="glob", pattern="*", root=bad)


def test_fileops_absolute_workspace_path_also_rejected(workspace):
    tool = FileOpsTool(LOCAL)
    with pytest.raises(PermissionError):
        tool.execute(operation="read", path=str(workspace / "src" / "a.py"))


def test_fileops_glob_rejects_escaping_patterns(workspace):
    tool = FileOpsTool(LOCAL)
    with pytest.raises(PermissionError):
        tool.execute(operation="glob", pattern="/etc/*")
    # A relative pattern that climbs out finds nothing outside the root.
    res = tool.execute(operation="glob", pattern="../*.txt")
    assert res["matches"] == []


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this machine")


def test_fileops_rejects_symlink_escape(workspace, tmp_path):
    _symlink_or_skip(workspace / "link.txt", tmp_path / "outside.txt")
    _symlink_or_skip(workspace / "linkdir", tmp_path)
    tool = FileOpsTool(LOCAL)
    with pytest.raises(PermissionError):
        tool.execute(operation="read", path="link.txt")
    with pytest.raises(PermissionError):
        tool.execute(operation="read", path="linkdir/outside.txt")
    globbed = tool.execute(operation="glob", pattern="*")["matches"]
    assert "link.txt" not in globbed and "linkdir" not in globbed
    grepped = tool.execute(operation="grep", pattern="secret-outside")["matches"]
    assert grepped == []


def test_fileops_glob_nested_pattern(workspace):
    tool = FileOpsTool(LOCAL)
    assert tool.execute(operation="glob", pattern="src/*.py")["matches"] == ["src/a.py"]
    rec = tool.execute(operation="glob", pattern="**/*.py")["matches"]
    assert "src/a.py" in rec and "src/pkg/c.py" in rec


def test_fallback_glob_nested_pattern(workspace):
    found = _fallback.glob(".", "src/*.py")
    assert [Path(p).as_posix().lstrip("./") for p in found] == ["src/a.py"]
    assert [Path(p).name for p in _fallback.glob("src", "*.py")] == ["a.py"]


def test_fileops_grep_returns_relative_paths(workspace):
    tool = FileOpsTool(LOCAL)
    res = tool.execute(operation="grep", pattern="print", path="src")["matches"]
    assert sorted(m["path"] for m in res) == ["src/a.py", "src/pkg/c.py"]


# ---------------------------------------------------------------- bash

def test_env_scrub_drops_secrets_keeps_basics():
    base = {
        "PATH": "/bin", "HOME": "/h", "USERPROFILE": "C:/u", "SYSTEMROOT": "C:/Windows",
        "TEMP": "/t", "TMP": "/t", "LANG": "C.UTF-8",
        "OPENAI_API_KEY": "x", "GITHUB_TOKEN": "x", "CLIENT_SECRET": "x", "DB_PASSWORD": "x",
        "RUNTIME_LITELLM_BASE_URL": "x", "LITELLM_MASTER": "x", "aws_secret_access_key": "x",
    }
    env = scrubbed_env(base, extra={"MY_TOKEN": "x", "FOO": "bar"})
    assert set(env) == {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP", "LANG", "FOO"}


def test_bash_local_runs_with_scrubbed_env(monkeypatch):
    monkeypatch.setenv("REX_TEST_API_KEY", "should-not-leak")
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw["env"])
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    with mock.patch("subprocess.run", fake_run):
        out = BashTool(LOCAL).execute(command="echo hi", description="t")
    assert out["exit_code"] == 0
    assert "REX_TEST_API_KEY" not in seen
    assert "PATH" in seen or "Path" in seen


@pytest.mark.parametrize("cmd", ["git push", "git push origin main", "cd x && git -C . push -f", "GIT_X=1 git  push"])
def test_bash_blocks_git_push(cmd):
    assert blocks_git_push(cmd)
    with mock.patch("subprocess.run") as run:
        out = BashTool(LOCAL).execute(command=cmd, description="t")
    run.assert_not_called()
    assert out["exit_code"] != 0 and "git push" in out["stderr"]


@pytest.mark.parametrize("cmd", ["git status", "git log --oneline", "echo pushd", "git stash; echo push"])
def test_bash_allows_other_git(cmd):
    assert not blocks_git_push(cmd)


def _raise_timeout_bytes(cmd, **kw):
    raise subprocess.TimeoutExpired(cmd, kw.get("timeout"), output=b"partial \xc3\xa9", stderr=b"err\xff")


def test_bash_timeout_decodes_bytes():
    with mock.patch("subprocess.run", _raise_timeout_bytes):
        out = BashTool(LOCAL).execute(command="sleep 9", description="t", timeout=1)
    assert out["exit_code"] == 124
    assert out["stdout"] == "partial \u00e9"
    assert out["stderr"].startswith("err") and "Timeout after 1s" in out["stderr"]


def test_shell_timeout_decodes_bytes():
    with mock.patch("subprocess.run", _raise_timeout_bytes):
        res = run_shell("sleep 9", timeout=1)
    assert res.exit_code == 124
    assert res.stdout == "partial \u00e9"
    assert "timed out" in res.stderr


# ---------------------------------------------------------------- git

@pytest.mark.parametrize("op", ["diff", "add"])
def test_git_rejects_option_files(op):
    with mock.patch("subprocess.run") as run:
        with pytest.raises(ValueError):
            GitTool(LOCAL).execute(operation=op, files=["--output=/tmp/x"])
    run.assert_not_called()


def test_git_rejects_option_branch():
    with mock.patch("subprocess.run") as run:
        with pytest.raises(ValueError):
            GitTool(LOCAL).execute(operation="checkout", branch="--orphan")
    run.assert_not_called()


def test_git_files_after_separator():
    with mock.patch("subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        GitTool(LOCAL).execute(operation="add", files=["a.py"])
    assert run.call_args[0][0] == ["git", "add", "--", "a.py"]


def test_git_tool_has_no_push():
    with pytest.raises(ValueError):
        GitTool(LOCAL).execute(operation="push")


# ---------------------------------------------------------------- ACP

def test_acp_child_gets_config_and_env_file(tmp_path):
    from runtimelib.acp import ACPServer

    server = ACPServer({}, config_path="cfg.yaml", env_file=tmp_path / ".env", local_tools=True)
    cmd = server.child_command()
    cfg = cmd[cmd.index("--config") + 1]
    env = cmd[cmd.index("--env-file") + 1]
    assert Path(cfg).is_absolute() and Path(cfg).name == "cfg.yaml"
    assert Path(env) == (tmp_path / ".env").resolve()
    assert "--local-tools" in cmd
    assert "--config" not in ACPServer({}).child_command()


# ---------------------------------------------------------------- /config masking

def test_config_command_masks_secrets():
    from runtime.__main__ import register_core_commands
    from runtimelib.cli.registry import CommandRegistry

    secret = "sk-test-value-not-real"
    cfg = Config({"liteLLM": {"api_key": secret, "model": "m", "max_tokens": 5}})
    reg = CommandRegistry()
    register_core_commands(reg, cfg)
    buf = io.StringIO()
    with mock.patch("sys.stdout", buf):
        reg.execute("config", {})
        reg.execute("config", {"show": "liteLLM.api_key"})
        reg.execute("config", {"show": "liteLLM"})
    out = buf.getvalue()
    assert secret not in out
    assert "***" in out and '"max_tokens": 5' in out
    assert cfg.get("liteLLM.api_key") == secret  # real value is untouched


# ---------------------------------------------------------------- agent loop

class EchoTool(Tool):
    def __init__(self, name: str = "echo", on_run=None) -> None:
        super().__init__(name, "echo", ToolSchema(name=name, description="echo", parameters={}))
        self.calls = 0
        self.on_run = on_run

    def execute(self, **kwargs):
        self.calls += 1
        if self.on_run:
            self.on_run()
        return "done"


class ScriptLLM:
    """Yields scripted responses; each is a list of chunk deltas."""

    def __init__(self, responses) -> None:
        self.default_model = "test-model"
        self.responses = list(responses)
        self.seen: list[list[ChatMessage]] = []

    def chat_completion(self, messages, **kwargs):
        self.seen.append(list(messages))
        deltas = self.responses.pop(0) if self.responses else [{"content": "ok"}]
        for d in deltas:
            yield ChatCompletionChunk(id="1", model="m", created=0, choices=[{"delta": d, "finish_reason": None}])


def _call_delta(call_id: str, name: str) -> dict:
    return {"tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}]}


def _loop(llm, tools):
    session = MagicMock(spec=SessionManager)
    session.session_id = "s"
    cfg = Config({"liteLLM": {"base_url": "http://dummy:4000", "api_key": "dummy", "model": "test-model"}})
    return create_agent_loop(cfg, llm_client=llm, session_manager=session, tools=tools)


def _assert_pairs_valid(messages):
    assert ensure_tool_results(messages) == list(messages)


def test_unknown_tool_returns_error_result():
    llm = ScriptLLM([[_call_delta("c1", "nope")], [{"content": "fine"}]])
    loop = _loop(llm, [EchoTool()])
    assert loop.run("go") == "fine"
    tool_msgs = [m for m in loop.state.messages if m.role == "tool"]
    assert len(tool_msgs) == 1 and tool_msgs[0].tool_call_id == "c1"
    payload = json.loads(tool_msgs[0].content)
    assert payload["success"] is False and "Unknown tool" in payload["error"]


def test_interrupt_between_tool_calls_leaves_no_orphans():
    loop = None

    def stop():
        loop.interrupt()

    first = EchoTool("first", on_run=stop)
    second = EchoTool("second")
    llm = ScriptLLM([[{"content": "after"}]])
    loop = _loop(llm, [first, second])
    loop.state.messages.append(ChatMessage(role="user", content="go"))
    calls = [ToolCall(id="a", function={"name": "first", "arguments": "{}"}),
             ToolCall(id="b", function={"name": "second", "arguments": "{}"})]
    loop.state.messages.append(ChatMessage(role="assistant", content="", tool_calls=calls))
    loop._execute_tool_calls(calls)
    assert second.calls == 0
    results = {m.tool_call_id: m.content for m in loop.state.messages if m.role == "tool"}
    assert set(results) == {"a", "b"}
    assert results["b"] == CANCELLED_RESULT
    _assert_pairs_valid(loop.state.messages)
    # The next request starts from a valid history.
    assert loop.run("again") == "after"
    _assert_pairs_valid(llm.seen[-1][1:])


def test_interrupt_during_stream_drops_partial_tool_calls():
    loop = None

    class InterruptingLLM(ScriptLLM):
        def chat_completion(self, messages, **kwargs):
            yield ChatCompletionChunk(id="1", model="m", created=0, choices=[{"delta": _call_delta("p", "echo")}])
            loop.interrupt()
            yield ChatCompletionChunk(id="1", model="m", created=0, choices=[{"delta": {"content": "x"}}])

    loop = _loop(InterruptingLLM([]), [EchoTool()])
    loop.run("go")
    _assert_pairs_valid(loop.state.messages)
    assert not any(m.tool_calls for m in loop.state.messages)


def test_history_repair_for_stale_tool_call():
    msgs = [
        ChatMessage(role="user", content="u"),
        ChatMessage(role="assistant", content="", tool_calls=[ToolCall(id="x", function={"name": "e"})]),
        ChatMessage(role="tool", content="r", tool_call_id="zzz"),
        ChatMessage(role="assistant", content="stopped"),
    ]
    fixed = ensure_tool_results(msgs)
    assert [m.role for m in fixed] == ["user", "assistant", "tool", "assistant"]
    assert fixed[2].tool_call_id == "x" and fixed[2].content == CANCELLED_RESULT


# ---------------------------------------------------------------- compaction

def _turn(i: int, big: int = 0) -> list[ChatMessage]:
    calls = [ToolCall(id=f"t{i}a", function={"name": "e", "arguments": "{}"}),
             ToolCall(id=f"t{i}b", function={"name": "e", "arguments": "{}"})]
    return [
        ChatMessage(role="user", content=f"q{i}"),
        ChatMessage(role="assistant", content="", tool_calls=calls),
        ChatMessage(role="tool", content="r" * big, tool_call_id=f"t{i}a"),
        ChatMessage(role="tool", content="r" * big, tool_call_id=f"t{i}b"),
        ChatMessage(role="assistant", content=f"a{i}"),
    ]


@pytest.mark.parametrize("keep_recent", [1, 2, 3, 4, 6])
def test_compaction_keeps_tool_pairs(keep_recent):
    msgs = []
    for i in range(6):
        msgs += _turn(i, big=4000)
    out, changed = compact_messages(msgs, threshold=2000, target=1500, keep_recent=keep_recent)
    assert changed
    _assert_pairs_valid(out)


def test_compaction_single_turn_tool_heavy():
    # One user turn with many tool rounds: boundary pass cannot cut at a user
    # message and must not start inside a tool-result group.
    msgs = [ChatMessage(role="user", content="q")]
    for i in range(8):
        msgs += _turn(i, big=3000)[1:4]
    out, changed = compact_messages(msgs, threshold=1000, target=500, keep_recent=3)
    assert changed
    _assert_pairs_valid(out)
    # Calls are kept (shrunk), never stripped, so results still pair up.
    ids = {tc.id for m in out if m.tool_calls for tc in m.tool_calls}
    assert all(m.tool_call_id in ids for m in out if m.role == "tool")


# ---------------------------------------------------------------- readline

def test_history_without_readline(tmp_path, monkeypatch):
    import runtimelib.cli.readline as rl

    monkeypatch.setattr(rl, "readline", None)
    hist = tmp_path / "h" / "history"
    h = rl.HistoryManager(hist)
    h.add("one")
    h.add("two")
    h.save()
    assert rl.HistoryManager(hist).get_history() == ["one", "two"]
    rl.ReadlineInterface(h)  # must not touch readline
