# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-opus-5-5
# Licensed under MIT License

"""Step 0 of docs/DESIGN-orchestration.md: groundwork for several agents in one process.

SIGINT and SESSION_START only for the top-level loop, one read tracker per loop, gate id prefix,
session append lock, guard client write lock. Fakes only; no network, no Docker.
"""

from __future__ import annotations

import io
import json
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from runtimelib.agent.gate import PermissionGate, PermissionRequest
from runtimelib.agent.loop import AgentLoop
from runtimelib.config import Config
from runtimelib.guard import GuardClient
from runtimelib.hooks import HookEvent
from runtimelib.permissions.rules import PermissionRules
from runtimelib.session import SessionManager
from runtimelib.tools.edit import READ_TRACKER, ReadTracker

BASE = {"liteLLM": {"base_url": "http://x", "api_key": "k", "model": "m"}}


class _Session:
    session_id = "s"

    def append(self, *a, **k):
        pass


class _Hooks:
    def __init__(self) -> None:
        self.events: list[HookEvent] = []

    def trigger(self, event, **kwargs):
        self.events.append(event)
        return []


def make_loop(depth=0, hooks=None, **kwargs):
    with redirect_stderr(io.StringIO()):
        return AgentLoop(Config(dict(BASE)), llm_client=object(), session_manager=_Session(),
                         hook_engine=hooks or _Hooks(), depth=depth, tool_names=["fileops", "edit"], **kwargs)


class TopLevelOnly(unittest.TestCase):
    def setUp(self):
        self.saved = signal.getsignal(signal.SIGINT)
        self.addCleanup(signal.signal, signal.SIGINT, self.saved)

    def test_child_does_not_touch_sigint(self):
        sentinel = lambda *a: None  # noqa: E731
        signal.signal(signal.SIGINT, sentinel)
        make_loop(depth=1)
        self.assertIs(signal.getsignal(signal.SIGINT), sentinel)

    def test_top_level_on_main_thread_takes_sigint(self):
        loop = make_loop(depth=0)
        self.assertEqual(signal.getsignal(signal.SIGINT), loop._handle_interrupt)

    def test_top_level_off_main_thread_builds_without_signal(self):
        before = signal.getsignal(signal.SIGINT)
        errors: list[BaseException] = []
        loops: list[AgentLoop] = []

        def build():
            try:
                loops.append(make_loop(depth=0))
            except BaseException as exc:  # ValueError before step 0
                errors.append(exc)

        t = threading.Thread(target=build)
        t.start()
        t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(len(loops), 1)
        self.assertIs(signal.getsignal(signal.SIGINT), before)

    def test_session_start_only_at_depth_0(self):
        top, child = _Hooks(), _Hooks()
        make_loop(depth=0, hooks=top)
        make_loop(depth=1, hooks=child)
        self.assertIn(HookEvent.SESSION_START, top.events)
        self.assertNotIn(HookEvent.SESSION_START, child.events)
        self.assertIn(HookEvent.AGENT_START, child.events)


class ReadTrackerPerLoop(unittest.TestCase):
    def test_edit_and_fileops_share_the_loops_own_tracker(self):
        a, b = make_loop(), make_loop(depth=1)
        for loop in (a, b):
            self.assertIs(loop.tools["edit"]._tracker, loop.read_tracker)
            self.assertIs(loop.tools["fileops"]._tracker, loop.read_tracker)
            self.assertIsNot(loop.read_tracker, READ_TRACKER)
        self.assertIsNot(a.read_tracker, b.read_tracker)

    def test_given_tracker_is_used(self):
        tracker = ReadTracker()
        loop = make_loop(read_tracker=tracker)
        self.assertIs(loop.tools["edit"]._tracker, tracker)


class _Asker:
    def __init__(self) -> None:
        self.requests: list[PermissionRequest] = []

    def ask(self, request):
        self.requests.append(request)
        return "deny"


class GateIdPrefix(unittest.TestCase):
    def ids(self, prefix):
        tmp = Path(tempfile.mkdtemp())
        rules = PermissionRules({}, str(tmp), store_dir=tmp / "store", persist=False)
        asker = _Asker()
        gate = PermissionGate(rules, None, asker, "local", id_prefix=prefix)
        gate.check("bash", {"command": "rm x"})
        gate.check("bash", {"command": "rm y"})
        return [r.id for r in asker.requests]

    def test_without_prefix_ids_are_unchanged(self):
        self.assertEqual(self.ids(""), ["perm-1", "perm-2"])

    def test_prefix_makes_ids_unique_per_agent(self):
        self.assertEqual(self.ids("t-3"), ["t-3:perm-1", "t-3:perm-2"])


class SessionAppendLock(unittest.TestCase):
    def test_200_concurrent_appends_give_200_valid_lines(self):
        tmp = Path(tempfile.mkdtemp())
        sm = SessionManager(tmp / "history.jsonl")
        payload = "x" * 4000

        def worker(n):
            for i in range(20):
                sm.append("tool", {"agent_id": f"a{n}", "i": i, "text": payload})

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        lines = (tmp / "history.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 200)
        for line in lines:
            self.assertEqual(json.loads(line)["data"]["text"], payload)
        self.assertEqual(len(sm.get_history()), 200)


FAKE_GUARD = textwrap.dedent("""
    import json, sys
    bad = 0
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if req["method"] == "stats":
            result = {"bad": bad}
        else:
            result = {"n": req["params"]["n"], "len": len(req["params"]["data"])}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}) + "\\n")
        sys.stdout.flush()
""")


class _ChunkedWriter:
    """Writes each string in four pieces with a thread switch between them."""

    def __init__(self, raw) -> None:
        self.raw = raw

    def write(self, text: str) -> None:
        step = max(1, len(text) // 4)
        for i in range(0, len(text), step):
            self.raw.write(text[i:i + step])
            self.raw.flush()
            time.sleep(0.001)

    def flush(self) -> None:
        self.raw.flush()

    def close(self) -> None:
        self.raw.close()


class GuardClientWriteLock(unittest.TestCase):
    def test_two_threads_send_whole_lines(self):
        tmp = Path(tempfile.mkdtemp())
        script = tmp / "fake_guard.py"
        script.write_text(FAKE_GUARD, encoding="utf-8")
        client = GuardClient(image="fake", workspace=tmp)
        client._proc = subprocess.Popen(
            [sys.executable, "-I", str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        # A real pipe may take a long line in several pieces; this wrapper makes that
        # happen every time, so without the write lock two threads interleave their bytes.
        client._proc.stdin = _ChunkedWriter(client._proc.stdin)
        client._reader_thread = threading.Thread(target=client._read_loop, daemon=True)
        client._reader_thread.start()
        client._started = True
        self.addCleanup(client.stop)

        data = "y" * 64 * 1024
        failures: list[str] = []

        def worker(base):
            for i in range(10):
                n = base + i
                try:
                    res = client._send_request("echo", {"n": n, "data": data}, timeout=5)
                    if res != {"n": n, "len": len(data)}:
                        failures.append(f"{n}: {res}")
                except Exception as exc:
                    failures.append(f"{n}: {exc}")

        threads = [threading.Thread(target=worker, args=(k * 1000,)) for k in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        self.assertEqual(failures, [])
        self.assertEqual(client._send_request("stats", {}, timeout=20), {"bad": 0})


if __name__ == "__main__":
    unittest.main()
