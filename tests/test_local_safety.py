# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-opus-5-5, claude-sonnet-5-5
# Licensed under MIT License

"""Safety tests for running rex without Docker (local tools) and for
conversation-history invariants. No network, no keys."""

from __future__ import annotations

import io
import json
import os
import shutil
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


def _tool_msg(text):
    """Plain-text tool message -> {"success", "result", "error"} (the loop's "[result of ...]" header line first,
    failures then start with "ERROR: ")."""
    if text.startswith("[result of "):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.startswith("ERROR: "):
        return {"success": False, "result": None, "error": text[len("ERROR: "):]}
    return {"success": True, "result": text, "error": None}


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
    assert tool.execute(operation="read", path="src/a.py") == "1\tprint('a')"
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
    for bad in ("../*.txt", "src/../../*.txt", "..", "src\\..\\..\\*", "**/../*"):
        with pytest.raises(PermissionError):
            tool.execute(operation="glob", pattern=bad)
    # '..' inside a name is not a segment and stays allowed.
    assert tool.execute(operation="glob", pattern="*..txt")["matches"] == []


def test_fileops_glob_falls_back_when_native_raises(workspace):
    tool = FileOpsTool(LOCAL)
    with mock.patch("runtimelib.native.glob", side_effect=OSError("native broke")):
        res = tool.execute(operation="glob", pattern="src/*.py")
    assert res["matches"] == ["src/a.py"]


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
        return 0, "ok", "", False

    from runtimelib.tools import bash as bash_mod
    with mock.patch.object(bash_mod, "run_tree", fake_run):
        out = BashTool(LOCAL).execute(command="echo hi", description="t")
    assert out["exit_code"] == 0
    assert "REX_TEST_API_KEY" not in seen
    assert "PATH" in seen or "Path" in seen


def test_bash_local_uses_bash_not_a_shell(monkeypatch):
    """Local mode runs [bash, -c, command] without shell=True (never cmd.exe)."""
    from runtimelib.tools import bash as bash_mod
    seen = {}

    def fake_popen(cmd, **kw):
        seen["cmd"], seen["shell"] = cmd, kw.get("shell")
        raise OSError("not started in this test")

    monkeypatch.setattr(bash_mod, "find_bash", lambda: "C:/Git/bin/bash.exe")
    with mock.patch("subprocess.Popen", fake_popen):
        BashTool(LOCAL).execute(command="echo 'a & b'", description="t")
    if os.name == "nt":  # the text travels in the environment, argv is constant
        assert seen["cmd"] == ["C:/Git/bin/bash.exe", "-c", 'eval "$REX_PRELUDE"; eval "$REX_COMMAND"']
    else:
        assert seen["cmd"] == ["C:/Git/bin/bash.exe", "-c", "echo 'a & b'"]
    assert not seen["shell"]


def test_bash_local_without_bash_is_a_clear_error(monkeypatch):
    from runtimelib.tools import bash as bash_mod
    monkeypatch.setattr(bash_mod, "find_bash", lambda: None)
    only_bash = Config({"cli": {"unsafe_local_tools": True}, "tools": {"shell": "bash"}})
    with mock.patch("subprocess.Popen") as popen:
        out = BashTool(only_bash).execute(command="echo hi", description="t")
    popen.assert_not_called()
    assert out["exit_code"] != 0
    if os.name == "nt":
        assert ("ai-rex needs Git Bash on Windows: install Git for Windows from https://git-scm.com/download/win"
                in out["stderr"])
    else:
        assert "not found" in out["stderr"]


def test_find_bash_on_windows_skips_wsl_launcher(monkeypatch):
    from runtimelib.tools import bash as bash_mod
    monkeypatch.setattr(bash_mod.os, "name", "nt")
    monkeypatch.setattr(bash_mod, "safe_which", lambda n: "C:\\Windows\\System32\\bash.exe" if n == "bash" else None)
    monkeypatch.setattr(bash_mod.os.path, "isfile", lambda p: False)
    assert bash_mod.find_bash() is None


@pytest.mark.parametrize("cmd", ["git push", "git push origin main","cd x && git -C . push -f", "GIT_X=1 git  push"])
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


class _SlowProc:
    """Popen stand-in: times out, then hands back partial output as bytes (as on POSIX)."""
    pid = 0
    returncode = None
    _handle = 0

    def __init__(self, cmd, **kw):
        self.cmd, self.calls = cmd, 0

    def communicate(self, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        raise subprocess.TimeoutExpired(self.cmd, timeout, output=b"partial \xc3\xa9", stderr=b"err\xff")

    def kill(self):
        pass


def test_bash_timeout_decodes_bytes():
    """Output is read as bytes and decoded without ever failing (the timeout itself: test_bash_tree_timeout)."""
    from runtimelib.tools import bash as bash_mod
    assert bash_mod._decode(b"partial \xc3\xa9", "utf-8") == "partial \u00e9"
    assert bash_mod._decode(b"err\xff\r\nx", "utf-8").startswith("err") and "\r" not in bash_mod._decode(b"a\r\nb")
    assert bash_mod._decode(b"x", "no-such-codec") == "x"


def test_shell_timeout_ends_the_command(tmp_path, monkeypatch):
    from runtimelib.tools.bash import resolve_shell
    if resolve_shell("auto") is None:
        pytest.skip("no shell")
    monkeypatch.chdir(tmp_path)
    res = run_shell("sleep 9", timeout=1)
    assert res.exit_code == 124
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


@pytest.mark.parametrize("bad", [".", "..", "../x", "a..b", "/abs", "C:foo", "x y", "-b", r"a\b"])
def test_git_checkout_rejects_path_like_branch(workspace, bad):
    with mock.patch("subprocess.run") as run:
        with pytest.raises(ValueError):
            GitTool(LOCAL).execute(operation="checkout", branch=bad)
    run.assert_not_called()


@pytest.mark.parametrize("name", ["src", "src/a.py", "README.md"])
def test_git_checkout_allows_branch_named_like_path(workspace, name):
    """`git switch` never reads its argument as a path, so such names pass."""
    (workspace / "README.md").write_text("x", encoding="utf-8")  # src/a.py comes from the fixture
    with mock.patch("subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        GitTool(LOCAL).execute(operation="checkout", branch=name)
    assert run.call_args[0][0] == ["git", "switch", name]


def test_git_checkout_uses_switch(workspace):
    with mock.patch("subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        GitTool(LOCAL).execute(operation="checkout", branch="feature/x")
        assert run.call_args[0][0] == ["git", "switch", "feature/x"]
        GitTool(LOCAL).execute(operation="checkout", branch="new", create=True)
        assert run.call_args[0][0] == ["git", "switch", "-c", "new"]
    assert Path(run.call_args[1]["cwd"]) == workspace.resolve()


def test_git_checkout_dot_keeps_work(tmp_path, monkeypatch):
    """Real repo: `checkout .` must not discard an uncommitted change."""
    if not shutil.which("git"):
        pytest.skip("git not installed")
    monkeypatch.chdir(tmp_path)
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], check=True)
    (tmp_path / "f.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "c"], check=True)
    (tmp_path / "f.txt").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError):
        GitTool(LOCAL).execute(operation="checkout", branch=".")
    # A file name is passed to `git switch`, which fails (no such branch)
    # instead of restoring the file the way `git checkout f.txt` would.
    try:
        GitTool(LOCAL).execute(operation="checkout", branch="f.txt")
    except Exception:
        pass
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "changed\n"


@pytest.mark.parametrize("bad", ["..", "../", "/etc", "C:/Windows", "C:foo", "\\\\?\\C:\\Windows"])
def test_git_and_bash_cwd_confined(workspace, bad):
    with mock.patch("subprocess.run") as run:
        with pytest.raises(PermissionError):
            GitTool(LOCAL).execute(operation="status", cwd=bad)
        out = BashTool(LOCAL).execute(command="echo hi", description="t", cwd=bad)
    run.assert_not_called()
    assert out["exit_code"] != 0


def test_bash_cwd_inside_workspace(workspace):
    from runtimelib.tools import bash as bash_mod
    with mock.patch.object(bash_mod, "run_tree", return_value=(0, "", "", False)) as run:
        BashTool(LOCAL).execute(command="echo hi", description="t", cwd="src")
    assert Path(run.call_args[1]["cwd"]) == (workspace / "src").resolve()


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
    payload = _tool_msg(tool_msgs[0].content)
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


def _tc(index, *, call_id=None, name=None, args=None):
    d = {"index": index, "function": {}}
    if call_id:
        d["id"] = call_id
        d["type"] = "function"
    if name is not None:
        d["function"]["name"] = name
    if args is not None:
        d["function"]["arguments"] = args
    return d


class RecordingTool(EchoTool):
    def __init__(self, name):
        super().__init__(name)
        self.kwargs = []

    def execute(self, **kwargs):
        self.kwargs.append(kwargs)
        return super().execute(**kwargs)


def test_stream_parallel_tool_calls_interleaved():
    """OpenAI format: id/name only on the first delta, args interleaved by index."""
    stream = [
        {"tool_calls": [_tc(0, call_id="call_a", name="first", args="")]},
        {"tool_calls": [_tc(1, call_id="call_b", name="second", args="")]},
        {"tool_calls": [_tc(0, args='{"x": ')]},
        {"tool_calls": [_tc(1, args='{"y": ')]},
        {"tool_calls": [_tc(1, args='"two"}'), _tc(0, args="1}")]},
    ]
    first, second = RecordingTool("first"), RecordingTool("second")
    llm = ScriptLLM([stream, [{"content": "both done"}]])
    loop = _loop(llm, [first, second])
    assert loop.run("go") == "both done"
    calls = next(m.tool_calls for m in loop.state.messages if m.tool_calls)
    assert [(c.id, c.function["name"], c.function["arguments"]) for c in calls] == [
        ("call_a", "first", '{"x": 1}'),
        ("call_b", "second", '{"y": "two"}'),
    ]
    assert first.kwargs[0]["x"] == 1 and second.kwargs[0]["y"] == "two"
    results = {m.tool_call_id for m in loop.state.messages if m.role == "tool"}
    assert results == {"call_a", "call_b"}
    _assert_pairs_valid(loop.state.messages)
    # The request sent back to the model never carries the stream index.
    sent = [m.to_dict() for m in llm.seen[-1]]
    assert all("index" not in tc for m in sent for tc in m.get("tool_calls", []))


def test_stream_tool_call_without_index_or_id():
    """Gateways that omit index: continuation deltas extend the last call."""
    stream = [
        {"tool_calls": [{"id": "c1", "type": "function", "function": {"name": "first", "arguments": "{"}}]},
        {"tool_calls": [{"function": {"arguments": "}"}}]},
        {"tool_calls": [{"id": "c2", "type": "function", "function": {"name": "second", "arguments": "{}"}}]},
    ]
    llm = ScriptLLM([stream, [{"content": "ok"}]])
    loop = _loop(llm, [RecordingTool("first"), RecordingTool("second")])
    loop.run("go")
    calls = next(m.tool_calls for m in loop.state.messages if m.tool_calls)
    assert [(c.id, c.function["arguments"]) for c in calls] == [("c1", "{}"), ("c2", "{}")]


def test_stream_tool_call_missing_id_gets_one():
    stream = [{"tool_calls": [_tc(0, name="first", args="{}")]}]
    llm = ScriptLLM([stream, [{"content": "ok"}]])
    loop = _loop(llm, [RecordingTool("first")])
    loop.run("go")
    calls = next(m.tool_calls for m in loop.state.messages if m.tool_calls)
    assert calls[0].id
    _assert_pairs_valid(loop.state.messages)


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


def _assert_compaction_honest(original, out):
    """Compaction may drop whole call groups, never invent or lose a result.

    compact_messages() runs ensure_tool_results() itself, so a plain pair
    check would hide a CANCELLED result it had to insert. Here: no
    CANCELLED result appears, and every call still in ``out`` has its own
    real result (possibly shortened) right after it.
    """
    _assert_pairs_valid(out)
    assert not any(m.role == "tool" and m.content == CANCELLED_RESULT for m in out)
    real = {m.tool_call_id: m.content for m in original if m.role == "tool"}
    kept_calls = [tc.id for m in out if m.tool_calls for tc in m.tool_calls]
    results = {m.tool_call_id: m.content for m in out if m.role == "tool"}
    assert set(results) == set(kept_calls)
    for call_id, content in results.items():
        assert call_id in real
        stem = content.split("\n...[", 1)[0]
        assert real[call_id].startswith(stem)


def _assert_latest_user_kept(original, out):
    latest = [m for m in original if m.role == "user"][-1]
    assert any(m.role == "user" and m.content == latest.content for m in out)


@pytest.mark.parametrize("keep_recent", [1, 2, 3, 4, 6])
def test_compaction_keeps_tool_pairs(keep_recent):
    msgs = []
    for i in range(6):
        msgs += _turn(i, big=4000)
    out, changed = compact_messages(msgs, threshold=2000, target=1500, keep_recent=keep_recent)
    assert changed
    _assert_compaction_honest(msgs, out)
    _assert_latest_user_kept(msgs, out)


def test_compaction_single_turn_tool_heavy():
    # One user turn with many tool rounds: boundary pass cannot cut at a user
    # message and must not start inside a tool-result group.
    msgs = [ChatMessage(role="user", content="q")]
    for i in range(8):
        msgs += _turn(i, big=3000)[1:4]
    out, changed = compact_messages(msgs, threshold=1000, target=500, keep_recent=3)
    assert changed
    _assert_compaction_honest(msgs, out)
    _assert_latest_user_kept(msgs, out)
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


@pytest.mark.parametrize("keep_recent", [1, 2, 4])
def test_compaction_never_drops_or_truncates_latest_user(keep_recent):
    big_question = "please look at this: " + "x" * 20000
    msgs = []
    for i in range(4):
        msgs += _turn(i, big=4000)
    msgs.append(ChatMessage(role="user", content=big_question))
    out, changed = compact_messages(msgs, threshold=2000, target=500, keep_recent=keep_recent)
    assert changed
    assert out[-1].role == "user" and out[-1].content == big_question
    _assert_compaction_honest(msgs, out)


def test_compaction_latest_user_first_in_single_turn():
    """Only one user message, at the start: it survives, tool rounds shrink."""
    question = "q" * 3000
    msgs = [ChatMessage(role="user", content=question)]
    for i in range(6):
        msgs += _turn(i, big=3000)[1:4]
    out, changed = compact_messages(msgs, threshold=1000, target=500, keep_recent=2)
    assert changed
    assert [m.content for m in out if m.role == "user"].count(question) == 1
    _assert_compaction_honest(msgs, out)



# ---------------------------------------------------------------- env scrub (more names)

def test_env_scrub_more_secret_shapes():
    base = {
        "PATH": "/bin", "GH_PAT": "x", "AZURE_DEVOPS_PAT": "x", "BASIC_AUTH": "x", "AUTHORIZATION": "x",
        "SESSION_COOKIE": "x", "DATABASE_URL": "postgres://u:p@h/db", "MY_DATABASE_URL": "x",
        "HTTPS_PROXY": "http://user:pw@proxy:8080", "http_proxy": "http://u:p@proxy:3128",
        "NO_PROXY": "localhost", "HTTP_PROXY_PLAIN": "x", "ALL_PROXY": "http://proxy:3128",
        "PATCH_LEVEL": "1", "COMPATH": "x", "XAUTHORITY": "/run/user/1000/xauth",
        "MY_XAUTHORITY_TOKEN": "x",
    }
    env = scrubbed_env(base)
    assert set(env) == {"PATH", "NO_PROXY", "HTTP_PROXY_PLAIN", "ALL_PROXY", "PATCH_LEVEL", "COMPATH",
                        "XAUTHORITY"}


# ---------------------------------------------------------------- config masking (more shapes)

def test_mask_secret_key_names():
    from runtimelib.config.config import is_secret_key

    for name in ("authorization", "Authorization", "auth", "basic_auth", "github_pat", "GH_PAT",
                 "cookie", "session_cookie", "cookies", "api_key", "apiKey"):
        assert is_secret_key(name), name
    for name in ("max_tokens", "keep_recent", "auth_type", "path", "author", "pattern"):
        assert not is_secret_key(name), name


def test_mask_secrets_lists_urls_and_nested():
    from runtimelib.config.config import mask_secrets, mask_value

    data = {
        "headers": {"Authorization": "Bearer abc", "Accept": "json"},
        "secrets": ["t1", "t2", {"inner": "t3"}],
        "api_key": ["k1", "k2"],
        "credentials": {"user": "u", "pass": "p"},
        "proxy": "http://alice:s3cret@proxy:8080",
        "mirrors": ["https://ghp_tok@github.com/x.git", "https://plain.example/y"],
        "base_url": "http://127.0.0.1:4000",
        "max_tokens": 5,
    }
    out = mask_secrets(data)
    dumped = json.dumps(out)
    for leaked in ("abc", "t1", "t2", "t3", "s3cret", "alice", "ghp_tok", '"u"', '"p"'):
        assert leaked not in dumped, leaked
    assert out["headers"]["Accept"] == "json"
    assert out["secrets"] == ["***", "***", {"inner": "***"}]
    assert out["api_key"] == ["***", "***"]
    assert out["proxy"] == "http://***@proxy:8080"
    assert out["mirrors"] == ["https://***@github.com/x.git", "https://plain.example/y"]
    assert out["base_url"] == "http://127.0.0.1:4000" and out["max_tokens"] == 5
    assert mask_value("net.proxy", "https://u:p@h") == "https://***@h"
    assert mask_value("x.github_pat", "abc") == "***"


# ---------------------------------------------------------------- Windows path shapes

@pytest.mark.parametrize("bad", [r"\\?\C:\Windows\win.ini", r"\\?\UNC\host\share\f", r"\\.\C:\x",
                                 "C:foo", r"C:..\outside.txt", "c:", "/C:/x"])
def test_fileops_rejects_device_and_drive_relative(workspace, bad):
    tool = FileOpsTool(LOCAL)
    with pytest.raises(PermissionError):
        tool.execute(operation="read", path=bad)
    with pytest.raises(PermissionError):
        tool.execute(operation="write", path=bad, content="x")
    with pytest.raises(PermissionError):
        tool.execute(operation="glob", pattern="*", root=bad)


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows-only")
def test_fileops_rejects_junction_escape(workspace, tmp_path):
    outside_dir = tmp_path / "outdir"
    outside_dir.mkdir()
    (outside_dir / "s.txt").write_text("secret-outside\n", encoding="utf-8")
    link = workspace / "junc"
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside_dir)],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not link.exists():
        pytest.skip(f"mklink /J failed: {proc.stderr.strip() or proc.stdout.strip()}")
    tool = FileOpsTool(LOCAL)
    with pytest.raises(PermissionError):
        tool.execute(operation="read", path="junc/s.txt")
    with pytest.raises(PermissionError):
        tool.execute(operation="write", path="junc/new.txt", content="x")
    assert not (outside_dir / "new.txt").exists()
    assert "junc" not in tool.execute(operation="glob", pattern="*")["matches"]
    assert tool.execute(operation="grep", pattern="secret-outside")["matches"] == []
    with mock.patch("subprocess.run") as run:
        with pytest.raises(PermissionError):
            GitTool(LOCAL).execute(operation="status", cwd="junc")
    run.assert_not_called()


def test_find_bash_on_windows_rejects_bat_and_cmd(monkeypatch):
    from runtimelib.tools import bash as bash_mod
    monkeypatch.setattr(bash_mod.os, "name", "nt")
    monkeypatch.setattr(bash_mod, "safe_which", lambda n: r"C:\tools\bash.bat" if n == "bash" else None)
    monkeypatch.setattr(bash_mod.os.path, "isfile", lambda p: False)
    assert bash_mod.find_bash() is None


@pytest.mark.skipif(os.name != "nt", reason="Git Bash command-line re-parsing is Windows only")
@pytest.mark.parametrize("cmd", ["@pay2", '"@pay2"', 'a"b', r"x\y", "%PATH%", "!x"])
def test_windows_bash_receives_command_unchanged(cmd, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from runtimelib.tools import bash as bash_mod
    if bash_mod.find_bash() is None:
        pytest.skip("no Git Bash")
    (tmp_path / "pay2").write_text("echo RAN_PAYLOAD\n")
    # The odd text sits in a trailing comment so it cannot break the parse; the command
    # writes the text bash was handed to a file.
    text = 'printf %s "$REX_COMMAND" > seen.txt # ' + cmd
    res = BashTool(LOCAL).execute(command=text, description="t")
    assert res["exit_code"] == 0, res
    assert "RAN_PAYLOAD" not in res["stdout"]
    assert (tmp_path / "seen.txt").read_bytes().decode() == text
