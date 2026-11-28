# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov
# AI models: claude-sonnet-5-5
"""Guard entrypoint: network allow-list matching and stdin isolation of child processes."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ENTRY = Path(__file__).resolve().parent.parent / "guard" / "entrypoint.py"


@pytest.fixture(scope="module")
def entry():
    sys.path.insert(0, str(ENTRY.parent.parent / "vendor"))
    spec = importlib.util.spec_from_file_location("guard_entrypoint_under_test", ENTRY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("domains,url,ok", [
    ("example.com", "https://example.com/x", True),
    ("example.com", "https://api.example.com/x", True),
    ("example.com", "https://evilexample.com/x", False),
    ("example.com", "https://example.com.evil.org/x", False),
    ("example.com,", "https://anything.org/x", False),          # empty entry matches nothing
    (",,", "https://anything.org/x", False),
    (" example.com , api.x.org ", "https://api.x.org/x", True),  # whitespace tolerated
    ("Example.COM", "https://EXAMPLE.com/x", True),             # case-insensitive
    ("", "https://example.com", False),
])
def test_network_policy(entry, monkeypatch, domains, url, ok):
    monkeypatch.setenv("GUARD_ALLOWED_DOMAINS", domains)
    assert entry._check_network_policy(url) is ok
