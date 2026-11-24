# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5
"""Every file with an Author header must carry an "AI models:" line."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_ai_header  # noqa: E402


def test_all_author_headers_have_ai_models():
    assert check_ai_header.missing(ROOT) == []
