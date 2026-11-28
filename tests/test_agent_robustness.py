# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-5-5
# Licensed under MIT License

"""Loop safety net, plain-text tool results, numbered reads, noise folders, tool descriptions, timeout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtimelib.config import Config
from runtimelib.llm.client import ChatCompletionChunk
from runtimelib.tools.base import ToolResult
from ai_runtime_rex.core.agent.loop import NUDGE_EMPTY, NUDGE_LEAKED, format_tool_result, leaked_tool_call
from ai_runtime_rex.core.profiles import build_profile, profile_from_agents_md
from ai_runtime_rex.core.tools.fileops import FileOpsTool, number_lines

BASE = {"liteLLM": {"base_url": "http://d:4000", "api_key": "k", "model": "m"}}
LOCAL = Config({**BASE, "cli": {"unsafe_local_tools": True}})


def _text(t):
    return ChatCompletionChunk(choices=[{"delta": {"content": t}, "finish_reason": "stop"}])


def _call(cid, name, args):
    return ChatCompletionChunk(choices=[{"delta": {"tool_calls": [{"index": 0, "id": cid, "type": "function",
                                                                   "function": {"name": name, "arguments": json.dumps(args)}}]},
                                         "finish_reason": "tool_calls"}])


class Script:
    default_model = "m"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat_completion(self, **kw):
        self.calls.append(kw)
        return iter(self.replies.pop(0))


def _loop(replies, tokens=None):
    from runtimelib.agent import create_agent_loop
    loop = create_agent_loop(LOCAL, on_token=(tokens.append if tokens is not None else None))
    loop.llm = Script(replies)
    return loop


# --- safety net -----------------------------------------------------------------------------------------

def test_leak_detection():
    assert leaked_tool_call('{"tool_calls": [{"name": "fileops"}]}')
    assert leaked_tool_call('I will look.\n<invoke name="fileops">')
    assert not leaked_tool_call("The project uses <dependency> tags in pom.xml.")
    assert not leaked_tool_call('{"answer": 42}')


def test_leaked_call_is_nudged_and_never_printed(tmp_path):
    tokens = []
    loop = _loop([[_text('{"tool_calls": [{"function": {"name": "fileops"}}]}')], [_text("Final answer.")]], tokens)
    assert loop.run("review") == "Final answer."
    assert "tool_calls" not in "".join(tokens)
    users = [m.content for m in loop.state.messages if m.role == "user"]
    assert NUDGE_LEAKED in users


def test_leak_gives_up_after_two_nudges():
    leak = [_text('<invoke name="fileops">')]
    loop = _loop([leak, leak, leak])
    with pytest.raises(RuntimeError, match="tool calls as text"):
        loop.run("go")


def test_empty_answer_after_tools_is_nudged(tmp_path):
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    loop = _loop([[_call("c1", "fileops", {"operation": "read", "path": "a.txt"})], [_text("")],
                  [_text("It says hello.")]])
    assert loop.run("what is in a.txt") == "It says hello."
    assert NUDGE_EMPTY in [m.content for m in loop.state.messages if m.role == "user"]


def test_markup_answer_that_is_not_a_call_is_streamed():
    tokens = []
    loop = _loop([[_text('{"answer": 42}')]], tokens)
    assert loop.run("json please") == '{"answer": 42}'
    assert "".join(tokens) == '{"answer": 42}'


# --- tool results as plain text -------------------------------------------------------------------------

def test_tool_result_formats():
    assert format_tool_result(ToolResult(tool_name="t", success=True, result="line1\nline2")) == "line1\nline2"
    assert format_tool_result(ToolResult(tool_name="t", success=False, error="no such file")) == "ERROR: no such file"
    shell = format_tool_result(ToolResult(tool_name="t", success=True, result={"exit_code": 1, "stdout": "out", "stderr": "bad"}))
    assert shell == "exit code: 1\nstdout:\nout\nstderr:\nbad"
    grep = format_tool_result(ToolResult(tool_name="t", success=True, result={"matches": [{"path": "a.py", "line": 3, "text": "x = 1"}]}))
    assert grep == "a.py:3: x = 1"
    assert format_tool_result(ToolResult(tool_name="t", success=True, result={"files": ["a", "b"]})) == "a\nb"


def test_read_result_reaches_model_as_numbered_text(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    loop = _loop([[_call("c1", "fileops", {"operation": "read", "path": "a.py"})], [_text("done")]])
    loop.run("read a.py")
    tool_msg = [m for m in loop.state.messages if m.role == "tool"][0]
    assert tool_msg.content == "1\tx = 1\n2\ty = 2"


# --- fileops read / list -------------------------------------------------------------------------------

def test_number_lines_offset_limit_and_truncation_note():
    text = "\n".join(f"l{i}" for i in range(1, 11)) + "\n"
    assert number_lines(text, 3, 2) == "3\tl3\n4\tl4\n[... truncated: lines 5-10 not shown; read again with offset=5]"
    assert number_lines(text, 9, None).endswith("10\tl10")
    assert "past the end" in number_lines(text, 50, None)
    assert number_lines("", None, None) == "(empty file)"


def test_list_glob_grep_skip_noise_folders(tmp_path):
    for d in ("src", "target", "node_modules", ".git", "src/__pycache__"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "a.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "target" / "b.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "src" / "__pycache__" / "a.py").write_text("needle\n", encoding="utf-8")
    tool = FileOpsTool(LOCAL)
    listed = tool.execute(operation="list", path=".", recursive=True)["files"]
    assert any(p.endswith("a.py") for p in listed)
    assert not any(seg in p for p in listed for seg in ("target", "node_modules", ".git", "__pycache__"))
    globbed = tool.execute(operation="glob", pattern="**/*.py")["matches"]
    assert [Path(p).name for p in globbed] == ["a.py"] and "__pycache__" not in globbed[0]
    grep = tool.execute(operation="grep", pattern="needle")["matches"]
    assert [m["path"] for m in grep] == ["src/a.py"]


# --- prompt and tool descriptions -----------------------------------------------------------------------

def test_prompt_carries_tool_usage_and_no_concise_rule():
    loop = _loop([])
    p = loop.system_prompt
    assert "A reply without a tool call ends the task" in p and "Batch independent calls" in p
    assert "Be concise in your responses" not in p
    assert "fileops: Workspace files. Operations:" in p and "offset" in p
    assert "timeout=600" in p
    assert "review-project skill" not in p or "code-review is for a diff" in p


def test_tool_descriptions_have_usage_and_example():
    loop = _loop([])
    for name in ("fileops", "bash", "git", "edit", "webfetch", "websearch"):
        desc = loop.tools[name].schema.description
        assert "\n" in desc and "Example" in desc, name


def test_default_timeout_is_600():
    from runtimelib.llm import LiteLLMClient
    assert LiteLLMClient.from_config(Config(BASE)).timeout == 600.0


# --- review follow-ups on profiles ---------------------------------------------------------------------

def test_untrusted_project_agents_md_cannot_choose_profile(tmp_path):
    (tmp_path / "AGENTS.md").write_text("ai-rex-profile: programming/python\n", encoding="utf-8")
    loop = _loop([])  # workspace not trusted
    assert "Python projects" not in loop.system_prompt and "Active profile: base" in loop.system_prompt


def test_profile_line_in_code_fence_ignored_and_last_wins():
    text = "ai-rex-profile: programming/java\n```\nai-rex-profile: programming/python\n```\n"
    assert profile_from_agents_md(text) == "programming/java"
    assert profile_from_agents_md("ai-rex-profile: a\nai-rex-profile: b\n") == "b"


def test_detect_is_case_insensitive_on_every_os(tmp_path):
    (tmp_path / "POM.XML").write_text("<project/>", encoding="utf-8")
    assert build_profile(tmp_path, Config(BASE)).paths[-1] == "programming/java"


# --- second review follow-ups --------------------------------------------------------------------------

def test_quoted_markup_in_a_review_is_not_a_leak():
    assert not leaked_tool_call("The parser looks for `<invoke name=...>` and `{\"tool_calls\"` markers.")
    assert not leaked_tool_call('Example:\n```\n<invoke name="fileops">\n{"tool_calls": []}\n```\nDone.')
    assert leaked_tool_call('Let me read it.\n<invoke name="fileops">')


def test_leak_after_prose_is_not_printed():
    tokens = []
    loop = _loop([[_text("I'll look.\n"), _text('<invoke name="fileops">\n<parameter>x</parameter>')],
                  [_text("Final.")]], tokens)
    assert loop.run("go") == "Final."
    shown = "".join(tokens)
    assert "I'll look." in shown and "<invoke" not in shown


def test_stream_guard_passes_normal_text_and_html_lines():
    from ai_runtime_rex.core.agent.loop import StreamGuard
    g = StreamGuard()
    out = g.feed("Hello wor") + g.feed("ld\n<b>bold</b>\nend") + g.finish(False)
    assert out == "Hello world\n<b>bold</b>\nend"


def test_hook_note_keeps_plain_text():
    r = ToolResult(tool_name="t", success=True, result={"result": "1\tx = 1", "hook_note": "checked"})
    assert format_tool_result(r) == "1\tx = 1\n[hook note] checked"
    g = ToolResult(tool_name="t", success=True, result={"files": ["a", "b"], "hook_note": "n"})
    assert format_tool_result(g) == "a\nb\n[hook note] n"


def test_skip_folder_can_be_inspected_when_asked(tmp_path):
    (tmp_path / "target" / "generated").mkdir(parents=True)
    (tmp_path / "target" / "generated" / "X.java").write_text("class X {}\n", encoding="utf-8")
    tool = FileOpsTool(LOCAL)
    assert tool.execute(operation="list", path="target/generated")["files"] == ["target/generated/X.java"]
    assert [m["path"] for m in tool.execute(operation="grep", pattern="class", path="target")["matches"]] == \
        ["target/generated/X.java"]
    assert not any("target" in p for p in tool.execute(operation="list", path=".", recursive=True)["files"])


def test_crlf_file_reads_without_carriage_returns():
    assert number_lines("a\r\nb\r\n") == "1\ta\n2\tb"
