# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov
# AI models: claude-sonnet-5-5, claude-opus-5-5
"""The zero-width regex must be written with escapes, not invisible literal characters."""

import re
from pathlib import Path

import ai_runtime_rex.core.security.sanitizer as san

# zero-width, bidi marks/overrides/isolates, word joiner .. invisible plus, BOM
INVISIBLE = re.compile("[\u200b-\u200f\u2028-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")


def test_sources_have_no_invisible_literals():
    for path in (Path(san.__file__), Path(__file__)):
        assert INVISIBLE.findall(path.read_text(encoding="utf-8")) == [], path


def test_zero_width_chars_are_stripped():
    pattern = san.InputValidator.ZERO_WIDTH
    assert pattern.sub("", "a\u200bb\u200cc\u200dd\ufeffe") == "abcde"
    assert pattern.sub("", "plain text") == "plain text"
