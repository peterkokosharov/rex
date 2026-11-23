# SPDX-License-Identifier: MIT
"""CLI tests (stdlib unittest, no network/TTY)."""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from runtimelib.config import Config
from runtimelib.cli.parser import ArgumentParser
from runtimelib.cli.render import Renderer
from runtimelib.cli.registry import CommandRegistry
from runtimelib.cli.readline import READLINE_AVAILABLE


class TestConfigSet(unittest.TestCase):
    def test_set_dot_notation(self):
        cfg = Config({})
        cfg.set("liteLLM.model", "gemini-lite")
        self.assertEqual(cfg.get("liteLLM.model"), "gemini-lite")

    def test_set_creates_nested(self):
        cfg = Config({})
        cfg.set("cli.verbose", True)
        self.assertEqual(cfg.get("cli.verbose"), True)
        self.assertEqual(cfg.to_dict()["cli"]["verbose"], True)

    def test_set_overrides(self):
        cfg = Config({"liteLLM": {"model": "old"}})
        cfg.set("liteLLM.model", "new")
        self.assertEqual(cfg.get("liteLLM.model"), "new")


class TestArgumentParser(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(ArgumentParser.parse(""), {})

    def test_key_value_and_positional(self):
        args = ArgumentParser.parse('foo=bar --flag "hello world" baz')
        self.assertEqual(args["foo"], "bar")
        self.assertTrue(args["flag"])
        self.assertEqual(args["_0"], "hello world")
        self.assertEqual(args["_1"], "baz")

    def test_bool_and_number(self):
        args = ArgumentParser.parse("a=true b=12 c=1.5")
        self.assertIs(args["a"], True)
        self.assertEqual(args["b"], 12)
        self.assertEqual(args["c"], 1.5)


class TestRenderer(unittest.TestCase):
    def test_final_prints_response_when_no_tokens(self):
        buf = io.StringIO()
        r = Renderer(stream=buf)
        r.final("hello")
        self.assertEqual(buf.getvalue(), "hello\n")

    def test_no_double_print_when_tokens_streamed(self):
        buf = io.StringIO()
        r = Renderer(stream=buf)
        r.on_token("he")
        r.on_token("llo")
        r.final("hello")
        self.assertEqual(buf.getvalue(), "hello\n")

    def test_tool_start_and_error(self):
        buf = io.StringIO()
        r = Renderer(stream=buf)
        r.on_tool_start("bash", {"command": "echo hi", "_role": "CODER"})
        class R:
            success = False
            error = "boom"
        r.on_tool_end(R())
        out = buf.getvalue()
        self.assertIn("bash", out)
        self.assertIn("boom", out)

    def test_verbose_tool_result(self):
        buf = io.StringIO()
        r = Renderer(stream=buf, verbose=True)
        r.on_tool_start("fileops", {"operation": "read", "path": "x"})
        class R:
            success = True
            error = None
            result = {"ok": True}
        r.on_tool_end(R())
        self.assertIn("ok", buf.getvalue())


class TestRegistry(unittest.TestCase):
    def test_register_execute(self):
        reg = CommandRegistry()
        @reg.register("ping", "ping")
        def ping(args):
            return 7
        self.assertEqual(reg.execute("ping", {}), 7)
        self.assertIn("ping", reg.list_commands())

    def test_unknown(self):
        reg = CommandRegistry()
        with self.assertRaises(ValueError):
            reg.execute("nope", {})


class TestCLIFlags(unittest.TestCase):
    def test_parser_flags(self):
        from runtime.__main__ import create_parser
        p = create_parser()
        args = p.parse_args(["--model", "gemini-lite", "--json", "--no-stream", "--verbose", "--unsafe-local-tools", "hi"])
        self.assertEqual(args.model, "gemini-lite")
        self.assertTrue(args.json)
        self.assertTrue(args.no_stream)
        self.assertTrue(args.verbose)
        self.assertTrue(args.unsafe_local_tools)
        self.assertEqual(args.prompt, ["hi"])


class TestModelCompleter(unittest.TestCase):
    @unittest.skipUnless(READLINE_AVAILABLE, "needs GNU readline or pyreadline3")
    def test_model_completion_uses_cache(self):
        from runtime.__main__ import completer, _MODEL_CACHE
        import runtime.__main__ as mainmod
        mainmod._MODEL_CACHE = ["gemini-lite", "nemotron-ultra", "codestral"]
        reg = CommandRegistry()
        cfg = Config({})
        with mock.patch("readline.get_line_buffer", return_value="/model gem"):
            m0 = completer("gem", 0, reg, cfg)
            m1 = completer("gem", 1, reg, cfg)
        self.assertEqual(m0, "gemini-lite")
        self.assertIsNone(m1)
        mainmod._MODEL_CACHE = []


class TestCmdModel(unittest.TestCase):
    def test_set_and_get_model(self):
        from runtime.__main__ import register_core_commands
        cfg = Config({"liteLLM": {"model": "old"}})
        reg = CommandRegistry()
        register_core_commands(reg, cfg)
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            reg.execute("model", {"_0": "new-model"})
        self.assertEqual(cfg.get("liteLLM.model"), "new-model")
        self.assertIn("new-model", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
