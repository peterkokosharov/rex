# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""R1 items: hidden task tool, launcher -P."""

from __future__ import annotations

import re
from pathlib import Path

from runtimelib.agent import create_agent_loop
from runtimelib.config import Config
from runtimelib.tools import TaskSpawnTool

ROOT = Path(__file__).parent.parent


def test_task_tool_is_hidden_but_code_kept():
    loop = create_agent_loop(Config({"liteLLM": {"base_url": "http://d:4000", "api_key": "k", "model": "m"}}))
    assert "task" not in loop.tools
    assert "task" not in {s["function"]["name"] for s in loop.tool_schemas}
    assert TaskSpawnTool is not None


def test_launchers_use_isolated_path_flag():
    # python3 / python.exe / %PY% (the Windows launcher template) must all run the runtime with -P
    for rel in ("packaging/build-deb.sh", "packaging/ai-rex.cmd.in", "run-ai.sh"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        runs = re.findall(r"(?:python3?(?:\.exe)?|%PY%) (?!-c)(.*?-m runtime)", text)
        assert runs, rel
        assert all(r.startswith("-P ") for r in runs), (rel, runs)
