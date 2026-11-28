# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov
# AI models: claude-sonnet-5-5
"""The zero-width regex must be written with escapes, not invisible literal characters."""

import re
from pathlib import Path

import ai_runtime_rex.core.security.sanitizer as san


def test_source_has_no_invisible_literals():
    text = Path(san.__file__).read_text(encoding="utf-8")
    invisible = re.findall("[​-‏ -‮⁠-⁤﻿]", text)
    assert invisible == []


def test_zero_width_chars_are_stripped():
    pattern = san.InputSanitizer.ZERO_WIDTH if hasattr(san, "InputSanitizer") else None
    if pattern is None:
        pattern = next(v.ZERO_WIDTH for v in vars(san).values() if hasattr(v, "ZERO_WIDTH"))
    assert pattern.sub("", "a​b‌c‍d﻿e") == "abcde"
    assert pattern.sub("", "plain text") == "plain text"
