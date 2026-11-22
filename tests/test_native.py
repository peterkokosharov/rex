# SPDX-License-Identifier: MIT
"""Tests for runtimelib.native (C++ native with Python fallback)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from runtimelib.native import _fallback
import runtimelib.native as native


class TestNativeBackend(unittest.TestCase):
    def test_backend_available(self):
        # In this environment we built the .so
        self.assertIn(native.backend(), ("native", "python"))

    def test_json_escape(self):
        self.assertEqual(native.json_escape('a"b\\c\n'), 'a\\"b\\\\c\\n')
        self.assertEqual(native.json_escape(""), "")

    def test_json_get_string(self):
        self.assertEqual(native.json_get_string('{"type":"token","text":"hi"}', "type"), "token")
        self.assertEqual(native.json_get_string('{"type":"token","text":"hi"}', "text"), "hi")
        self.assertEqual(native.json_get_string('{"a":"line\\n2"}', "a"), "line\n2")
        self.assertEqual(native.json_get_string('{"x":1}', "missing"), "")

    def test_parse_sse_data(self):
        self.assertIsNone(native.parse_sse_data("event: foo"))
        self.assertEqual(native.parse_sse_data("data: {\"a\":1}"), '{"a":1}')
        self.assertEqual(native.parse_sse_data("data: [DONE]"), "[DONE]")
        self.assertEqual(native.parse_sse_data("data:  [DONE]  "), "[DONE]")

    def test_grep_and_glob(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("hello world\nbye\n", encoding="utf-8")
            (root / "sub").mkdir()
            (root / "sub" / "b.py").write_text("print('hello')\n", encoding="utf-8")

            g = native.grep(str(root), "hello")
            texts = [m["text"] for m in g]
            self.assertTrue(any("hello world" in t for t in texts))
            self.assertTrue(any("print('hello')" in t for t in texts))

            g2 = native.grep(str(root), "hello", include="*.txt")
            texts2 = [m["text"] for m in g2]
            self.assertTrue(any("hello world" in t for t in texts2))
            self.assertFalse(any("print('hello')" in t for t in texts2))

            gl = native.glob(str(root), "**/*.py")
            self.assertTrue(any(p.endswith("b.py") for p in gl))


class TestFallback(unittest.TestCase):
    def test_json_escape(self):
        self.assertEqual(_fallback.json_escape('a"b\\c\n'), 'a\\"b\\\\c\\n')

    def test_json_get_string(self):
        self.assertEqual(_fallback.json_get_string('{"type":"token","text":"hi"}', "text"), "hi")

    def test_parse_sse_data(self):
        self.assertIsNone(_fallback.parse_sse_data("event: foo"))
        self.assertEqual(_fallback.parse_sse_data("data: [DONE]"), "[DONE]")

    def test_grep(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("hello world\n", encoding="utf-8")
            g = _fallback.grep(str(root), "hello")
            self.assertEqual(len(g), 1)
            self.assertIn("hello world", g[0]["text"])


class TestNativeFallbackParity(unittest.TestCase):
    def test_grep_matches_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("hello world\nbye\n", encoding="utf-8")
            (root / "sub").mkdir()
            (root / "sub" / "b.py").write_text("print('hello')\n", encoding="utf-8")
            for pattern in ("hello", "h.llo"):
                native_hits = native.grep(str(root), pattern)
                python_hits = _fallback.grep(str(root), pattern)
                key = lambda m: (m["path"], m["line"], m["text"])
                self.assertEqual(sorted(map(key, native_hits)), sorted(map(key, python_hits)))

    @unittest.skipUnless(native.NATIVE_AVAILABLE, "native library not built")
    def test_python_only_regex_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("HELLO world\n", encoding="utf-8")
            hits = native.grep(str(root), "(?i)hello")
            self.assertTrue(any("HELLO" in m["text"] for m in hits))


if __name__ == "__main__":
    unittest.main()
