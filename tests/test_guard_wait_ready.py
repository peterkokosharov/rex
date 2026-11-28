# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov
# AI models: claude-sonnet-5-5
"""GuardClient._wait_ready returns as soon as the guard answers, instead of sleeping the full timeout."""

import io
import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from runtimelib.guard import GuardClient


class _Stdin:
    def __init__(self, on_line):
        self.on_line = on_line

    def write(self, text):
        self.on_line(json.loads(text))

    def flush(self):
        pass


def _client_with_echo_guard(tmp_path, answer=True):
    client = GuardClient(image="x:latest", workspace=tmp_path)
    proc = MagicMock()
    proc.poll.return_value = None
    stdout_lines = []

    def on_line(req):
        if answer:
            threading.Thread(target=client._read_loop_line_for_test, args=(json.dumps(
                {"jsonrpc": "2.0", "id": req["id"], "result": {"files": []}}),), daemon=True).start()

    proc.stdin = _Stdin(on_line)
    client._proc = proc

    # feed one response line through the real reader logic
    def feed(line):
        proc.stdout = io.StringIO(line + "\n")
        client._read_loop()

    client._read_loop_line_for_test = feed
    return client


def test_wait_ready_returns_quickly_when_guard_answers(tmp_path):
    client = _client_with_echo_guard(tmp_path)
    t0 = time.monotonic()
    client._wait_ready(timeout=10.0)
    assert time.monotonic() - t0 < 3.0


def test_wait_ready_raises_when_container_exited(tmp_path):
    client = _client_with_echo_guard(tmp_path, answer=False)
    client._proc.poll.return_value = 1
    client._proc.stderr.read.return_value = "boom"
    with pytest.raises(RuntimeError, match="boom"):
        client._wait_ready(timeout=2.0)
