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
    ("example.com", "https://example.com./x", True),             # FQDN trailing dot = same host
    ("example.com.", "https://api.example.com/x", True),         # trailing dot in the entry
    ("example.com", "https://evilexample.com./x", False),
    ("*.example.com", "https://a.example.com/x", True),
    ("*", "https://anything.org/x", False),                      # a bare wildcard is not "allow all"
    ("example.com", "https://example.com:8443/x", True),         # ports are not restricted
    ("example.com", "https://example.com@evil.org/x", False),    # userinfo is not the host
    ("example.com", "https://evil.org．example.com/x", False),  # fullwidth dot is not a label dot
    ("example.com", "https://example.com。evil.org/x", False),
    ("localhost", "http://127.0.0.1/x", False),                  # names never match IP literals
    ("127.0.0.1", "http://127.0.0.1:8080/x", True),
    ("example.com", "file:///etc/passwd", False),
])
def test_network_policy(entry, monkeypatch, domains, url, ok):
    monkeypatch.setenv("GUARD_ALLOWED_DOMAINS", domains)
    assert entry._check_network_policy(url) is ok


def test_bash_exec_does_not_inherit_stdin(entry, tmp_path):
    server = entry.create_server(tmp_path)
    handler = server._methods["bash.exec"]
    # A child that reads stdin must see EOF at once, not the guard's JSON-RPC stream.
    res = handler(command=f'"{sys.executable}" -c "import sys; print(len(sys.stdin.read()))"', timeout=20)
    assert res["stdout"].strip() == "0"


def test_every_guard_subprocess_closes_stdin():
    text = ENTRY.read_text(encoding="utf-8")
    import re
    calls = re.findall(r"subprocess\.run\((.*?)\n\s*\)|subprocess\.run\(([^\n]*)\)", text, re.S)
    assert calls
    for a, b in calls:
        assert "stdin=subprocess.DEVNULL" in (a or b), (a or b)[:80]
