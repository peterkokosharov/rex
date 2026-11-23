# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""Tests for the per-session store."""

import os

from runtimelib.llm import ChatMessage
from runtimelib.llm.messages import ToolCall
from runtimelib.session import SessionStore


def _conversation():
    call = ToolCall(id="c1", function={"name": "bash", "arguments": '{"command": "ls"}'})
    return [
        ChatMessage.user("list files\nplease"),
        ChatMessage.assistant(None, [call]),
        ChatMessage.tool("c1", "a.txt"),
        ChatMessage.assistant("done"),
    ]


def test_from_dict_round_trip():
    for m in _conversation():
        assert ChatMessage.from_dict(m.to_dict()).to_dict() == m.to_dict()


def test_round_trip_with_tool_calls(tmp_path):
    msgs = _conversation()
    s = SessionStore(tmp_path, cwd=str(tmp_path), model="m")
    for m in msgs:
        s.append_message(m)
    back = SessionStore.open(tmp_path, s.id).load_messages()
    assert [m.to_dict() for m in back] == [m.to_dict() for m in msgs]
    assert back[1].tool_calls[0].id == "c1"


def test_compact_snapshot(tmp_path):
    s = SessionStore(tmp_path, cwd=str(tmp_path))
    for m in _conversation():
        s.append_message(m)
    snap = [ChatMessage.user("summary"), ChatMessage.assistant("ok")]
    s.append_compact(snap)
    s.append_message(ChatMessage.user("next"))
    back = s.load_messages()
    assert [m.content for m in back] == ["summary", "ok", "next"]


def test_list_order_and_info(tmp_path):
    a = SessionStore(tmp_path, session_id="a", cwd="/x")
    a.append_message(ChatMessage.user("first"))
    b = SessionStore(tmp_path, session_id="b", cwd="/y")
    b.append_message(ChatMessage.user("second"))
    os.utime(a.path, (1_000_000, 1_000_000))
    os.utime(b.path, (2_000_000, 2_000_000))
    infos = SessionStore.list_sessions(tmp_path)
    assert [i.id for i in infos] == ["b", "a"]
    assert infos[0].title == "second" and infos[0].messages == 1
    assert SessionStore.list_sessions(tmp_path / "missing") == []


def test_cwd_filter(tmp_path):
    a = SessionStore(tmp_path, session_id="a", cwd=str(tmp_path / "p1"))
    b = SessionStore(tmp_path, session_id="b", cwd=str(tmp_path / "p2"))
    c = SessionStore(tmp_path, session_id="c", cwd=str(tmp_path / "p1"))
    os.utime(a.path, (1_000_000, 1_000_000))
    os.utime(b.path, (2_000_000, 2_000_000))
    os.utime(c.path, (3_000_000, 3_000_000))
    assert SessionStore.latest_for_cwd(tmp_path, str(tmp_path / "p1")).id == "c"
    assert SessionStore.latest_for_cwd(tmp_path, str(tmp_path / "p2")).id == "b"
    assert SessionStore.latest_for_cwd(tmp_path, str(tmp_path / "none")) is None


def test_corrupt_last_line_tolerated(tmp_path):
    s = SessionStore(tmp_path, cwd="/x")
    s.append_message(ChatMessage.user("hi"))
    with open(s.path, "ab") as f:
        f.write(b'{"type": "message", "m": {"role": "ass')
    assert [m.content for m in s.load_messages()] == ["hi"]
    s.append_message(ChatMessage.assistant("later"))
    s2 = SessionStore.open(tmp_path, s.id)
    assert [m.content for m in s2.load_messages()] == ["hi", "later"]


def test_open_missing_raises(tmp_path):
    try:
        SessionStore.open(tmp_path, "nope")
    except FileNotFoundError:
        return
    raise AssertionError("expected FileNotFoundError")


def test_title_written_once(tmp_path):
    s = SessionStore(tmp_path, cwd="/x")
    s.append_message(ChatMessage.user("one"))
    s.append_message(ChatMessage.user("two"))
    s = SessionStore.open(tmp_path, s.id)
    s.append_message(ChatMessage.user("three"))
    assert s.path.read_text(encoding="utf-8").count('"type": "title"') == 1
    assert SessionStore.list_sessions(tmp_path)[0].title == "one"
