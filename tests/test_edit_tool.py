# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""Tests for the edit tool (read-before-edit, ambiguity refusal, diff)."""

from __future__ import annotations

import pytest

from runtimelib.config import Config
from runtimelib.tools.edit import EditError, EditTool, ReadTracker, make_diff
from runtimelib.tools.fileops import FileOpsTool


class FakeGuard:
    """In-memory guard client with the fs_read / fs_write surface."""

    def __init__(self, files=None):
        self.files = dict(files or {})
        self.fs_edit_called = False

    def fs_read(self, path, encoding="utf-8"):
        return self.files[path]

    def fs_write(self, path, content, mode=0o644):
        self.files[path] = content

    def fs_edit(self, *a, **k):  # must never be used: edits are computed locally
        self.fs_edit_called = True


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def local_tools():
    cfg = Config({"cli": {"unsafe_local_tools": True}})
    tracker = ReadTracker()
    return FileOpsTool(cfg, tracker), EditTool(cfg, tracker), tracker


def guard_tools(guard):
    tracker = ReadTracker()
    fo = FileOpsTool(Config({}), tracker)
    fo._guard = guard
    et = EditTool(Config({}), tracker)
    et._guard = guard
    return fo, et, tracker


def test_unique_match_edits_and_returns_diff(ws):
    (ws / "a.txt").write_bytes(b"one\ntwo\nthree\n")
    fo, et, _ = local_tools()
    fo.execute(operation="read", path="a.txt")
    res = et.execute(path="a.txt", old_string="two", new_string="2")
    assert res["ok"] is True and res["replacements"] == 1 and res["path"] == "a.txt"
    assert (ws / "a.txt").read_bytes() == b"one\n2\nthree\n"
    assert res["diff"] == "\n".join([
        "--- a/a.txt", "+++ b/a.txt", "@@ -1,3 +1,3 @@", " one", "-two", "+2", " three",
    ])


def test_multiple_matches_refused_with_count(ws):
    (ws / "a.txt").write_bytes(b"x\nx\nx\n")
    fo, et, _ = local_tools()
    fo.execute(operation="read", path="a.txt")
    with pytest.raises(EditError, match="3 times"):
        et.execute(path="a.txt", old_string="x", new_string="y")
    assert (ws / "a.txt").read_bytes() == b"x\nx\nx\n"


def test_replace_all(ws):
    (ws / "a.txt").write_bytes(b"x\nx\nx\n")
    fo, et, _ = local_tools()
    fo.execute(operation="read", path="a.txt")
    res = et.execute(path="a.txt", old_string="x", new_string="y", replace_all=True)
    assert res["replacements"] == 3
    assert (ws / "a.txt").read_bytes() == b"y\ny\ny\n"


def test_missing_match_has_hint(ws):
    (ws / "a.txt").write_bytes(b"def f():\n    return 1\n")
    fo, et, _ = local_tools()
    fo.execute(operation="read", path="a.txt")
    with pytest.raises(EditError, match="not found"):
        et.execute(path="a.txt", old_string="nothing here", new_string="z")
    with pytest.raises(EditError, match="indentation"):
        et.execute(path="a.txt", old_string="def f():\n  return 1", new_string="z")


def test_identical_strings_refused(ws):
    (ws / "a.txt").write_bytes(b"abc\n")
    fo, et, _ = local_tools()
    fo.execute(operation="read", path="a.txt")
    with pytest.raises(EditError, match="identical"):
        et.execute(path="a.txt", old_string="abc", new_string="abc")


def test_not_read_refused(ws):
    (ws / "a.txt").write_bytes(b"abc\n")
    _, et, _ = local_tools()
    with pytest.raises(EditError, match="not been read"):
        et.execute(path="a.txt", old_string="abc", new_string="d")
    assert (ws / "a.txt").read_bytes() == b"abc\n"


def test_changed_since_read_refused(ws):
    (ws / "a.txt").write_bytes(b"abc\n")
    fo, et, _ = local_tools()
    fo.execute(operation="read", path="a.txt")
    (ws / "a.txt").write_bytes(b"abc changed\n")
    with pytest.raises(EditError, match="changed since"):
        et.execute(path="a.txt", old_string="abc", new_string="d")


def test_second_edit_after_first_works(ws):
    (ws / "a.txt").write_bytes(b"a\nb\n")
    fo, et, _ = local_tools()
    fo.execute(operation="read", path="a.txt")
    et.execute(path="a.txt", old_string="a", new_string="A")
    et.execute(path="a.txt", old_string="b", new_string="B")
    assert (ws / "a.txt").read_bytes() == b"A\nB\n"


def test_write_counts_as_read(ws):
    fo, et, _ = local_tools()
    fo.execute(operation="write", path="n.txt", content="hello\n")
    et.execute(path="n.txt", old_string="hello", new_string="bye")
    assert (ws / "n.txt").read_bytes() == b"bye\n"


def test_crlf_preserved(ws):
    (ws / "w.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    fo, et, _ = local_tools()
    assert fo.execute(operation="read", path="w.txt") == "one\ntwo\nthree\n"
    et.execute(path="w.txt", old_string="two\nthree", new_string="2\n3")
    assert (ws / "w.txt").read_bytes() == b"one\r\n2\r\n3\r\n"


def test_missing_file_and_outside_workspace(ws):
    _, et, _ = local_tools()
    with pytest.raises(EditError, match="not found"):
        et.execute(path="nope.txt", old_string="a", new_string="b")
    with pytest.raises(PermissionError):
        et.execute(path="../x.txt", old_string="a", new_string="b")


def test_diff_capped():
    old = "".join(f"line {i}\n" for i in range(1000))
    new = "".join(f"LINE {i}\n" for i in range(1000))
    lines = make_diff(old, new, "big.txt").splitlines()
    assert len(lines) == 401
    assert lines[-1].startswith("... ") and lines[-1].endswith(" more lines")


def test_fileops_edit_routes_to_same_code(ws):
    (ws / "a.txt").write_bytes(b"x\nx\n")
    fo, _, _ = local_tools()
    with pytest.raises(EditError, match="not been read"):
        fo.execute(operation="edit", path="a.txt", old_str="x", new_str="y")
    fo.execute(operation="read", path="a.txt")
    with pytest.raises(EditError, match="2 times"):
        fo.execute(operation="edit", path="a.txt", old_str="x", new_str="y")
    res = fo.execute(operation="edit", path="a.txt", old_str="x", new_str="y", replace_all=True)
    assert res["ok"] and res["replacements"] == 2 and "diff" in res
    assert (ws / "a.txt").read_bytes() == b"y\ny\n"


def test_describe_gives_diff_preview(ws):
    (ws / "a.txt").write_bytes(b"one\ntwo\n")
    fo, et, _ = local_tools()
    fo.execute(operation="read", path="a.txt")
    summary, detail = et.describe({"path": "a.txt", "old_string": "two", "new_string": "2"})
    assert summary == "edit a.txt" and "-two" in detail and "+2" in detail
    assert (ws / "a.txt").read_bytes() == b"one\ntwo\n"  # preview writes nothing


def test_guard_path_edit_and_hash_check():
    guard = FakeGuard({"src/a.py": "a = 1\nb = 2\n"})
    fo, et, _ = guard_tools(guard)
    with pytest.raises(EditError, match="not been read"):
        et.execute(path="src/a.py", old_string="a = 1", new_string="a = 9")
    assert fo.execute(operation="read", path="src/a.py") == "a = 1\nb = 2\n"
    guard.files["src/a.py"] = "a = 1\nb = 3\n"  # changed behind our back
    with pytest.raises(EditError, match="changed since"):
        et.execute(path="src/a.py", old_string="a = 1", new_string="a = 9")
    fo.execute(operation="read", path="src/a.py")
    res = et.execute(path="src/a.py", old_string="a = 1", new_string="a = 9")
    assert res["ok"] and guard.files["src/a.py"] == "a = 9\nb = 3\n"
    assert "+a = 9" in res["diff"]
    assert not guard.fs_edit_called


def test_guard_fileops_edit_routes_and_refuses_ambiguous():
    guard = FakeGuard({"f.txt": "k\nk\n"})
    fo, _, _ = guard_tools(guard)
    fo.execute(operation="read", path="f.txt")
    with pytest.raises(EditError, match="2 times"):
        fo.execute(operation="edit", path="f.txt", old_str="k", new_str="m")
    fo.execute(operation="edit", path="f.txt", old_str="k", new_str="m", replace_all=True)
    assert guard.files["f.txt"] == "m\nm\n"
    assert not guard.fs_edit_called


def test_guard_crlf_preserved():
    guard = FakeGuard({"w.txt": "a\r\nb\r\n"})
    fo, et, _ = guard_tools(guard)
    fo.execute(operation="read", path="w.txt")
    et.execute(path="w.txt", old_string="b", new_string="B")
    assert guard.files["w.txt"] == "a\r\nB\r\n"
