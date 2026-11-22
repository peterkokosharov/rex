# SPDX-License-Identifier: MIT
"""Integration tests: native helpers wired into rex components."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtimelib.config import Config
from runtimelib.tools.fileops import FileOpsTool
from runtimelib.llm.client import LiteLLMClient


class TestFileOpsNative(unittest.TestCase):
    def setUp(self):
        self.cfg = Config({"cli": {"unsafe_local_tools": True}})
        self.tool = FileOpsTool(self.cfg)

    def test_grep_and_glob(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("hello world\nbye\n", encoding="utf-8")
            (root / "sub").mkdir()
            (root / "sub" / "b.py").write_text("print('hello')\n", encoding="utf-8")

            g = self.tool.execute(operation="grep", pattern="hello", path=str(root))
            texts = [m["text"] for m in g["matches"]]
            self.assertTrue(any("hello world" in t for t in texts))
            self.assertTrue(any("print('hello')" in t for t in texts))

            gl = self.tool.execute(operation="glob", pattern="**/*.py", root=str(root))
            self.assertTrue(any(p.endswith("b.py") for p in gl["matches"]))


class TestSSEParseNative(unittest.TestCase):
    def setUp(self):
        self.client = LiteLLMClient("http://127.0.0.1:1", "dummy", "dummy")

    def test_parse(self):
        self.assertIsNone(self.client._parse_sse_line("event: ping"))
        self.assertEqual(self.client._parse_sse_line("data: [DONE]"), {"done": True})
        obj = self.client._parse_sse_line('data: {"id":"1","choices":[]}')
        self.assertEqual(obj["id"], "1")


if __name__ == "__main__":
    unittest.main()
