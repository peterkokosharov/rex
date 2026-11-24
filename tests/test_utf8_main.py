# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""main() forces UTF-8 on stdout/stderr (errors=replace) in every mode."""

from __future__ import annotations

import io
import sys

import pytest

from runtime.__main__ import main


class _Stream(io.StringIO):
    def __init__(self):
        super().__init__()
        self.calls = []

    def reconfigure(self, **kw):
        self.calls.append(kw)


def test_main_reconfigures_output_streams(monkeypatch):
    out, err = _Stream(), _Stream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setattr(sys, "argv", ["ai-rex", "--help"])
    with pytest.raises(SystemExit):
        main()
    for s in (out, err):
        assert {"encoding": "utf-8", "errors": "replace"} in s.calls
