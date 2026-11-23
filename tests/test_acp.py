# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Tests for the ACP bridge. No network, no keys, no rex subprocess."""

from __future__ import annotations

import json
import queue
import tempfile
import threading
import time
import unittest
from typing import Any

from runtimelib.acp import ACPServer


class QueueStream:
    """stdin stand-in that stays open until the test closes it."""

    def __init__(self) -> None:
        self.items: queue.Queue[str | None] = queue.Queue()

    def send(self, line: str) -> None:
        self.items.put(line)

    def close(self) -> None:
        self.items.put(None)

    def __iter__(self):
        while True:
            item = self.items.get()
            if item is None:
                return
            yield item


class CollectStream:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.lock = threading.Lock()

    def write(self, line: str) -> None:
        with self.lock:
            self.lines.append(line)

    def flush(self) -> None:
        pass

    def messages(self) -> list[dict[str, Any]]:
        with self.lock:
            return [json.loads(line) for line in self.lines]

    def wait_id(self, rid: int, timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for msg in self.messages():
                if msg.get("id") == rid:
                    return msg
            time.sleep(0.01)
        raise AssertionError(f"no response to id {rid}")


class FakeSession:
    def __init__(self, sid: str, cwd: str, server: ACPServer) -> None:
        self.id = sid
        self.cwd = cwd
        self.server = server
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.sent: list[dict[str, Any]] = []
        self.busy = False
        self.cancel_requested = False
        for event in (
            {"type": "ready", "model": "gemini-lite"},
            {"type": "token", "text": "O"},
            {"type": "token", "text": "K"},
            {"type": "final", "text": "OK"},
        ):
            self.events.put(event)

    def send(self, item: dict[str, Any]) -> bool:
        self.sent.append(item)
        return True

    def close(self) -> None:
        pass


class FakeACP(ACPServer):
    def _spawn(self, sid: str, cwd: str) -> FakeSession:  # type: ignore[override]
        session = FakeSession(sid, cwd, self)
        self.sessions[sid] = session  # type: ignore[assignment]
        return session


class ACPBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stdin = QueueStream()
        self.stdout = CollectStream()
        self.server = FakeACP({}, stream_in=self.stdin, stream_out=self.stdout)  # type: ignore[arg-type]
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        self.rid = 0

    def tearDown(self) -> None:
        self.stdin.close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive(), "ACP server did not exit")

    def request(self, method: str, params: dict[str, Any] | None = None) -> int:
        self.rid += 1
        self.stdin.send(json.dumps({"jsonrpc": "2.0", "id": self.rid, "method": method, "params": params or {}}) + "\n")
        return self.rid

    def test_initialize_negotiates_v1(self) -> None:
        rid = self.request("initialize", {"protocolVersion": 9, "clientCapabilities": {}})
        result = self.stdout.wait_id(rid)["result"]
        self.assertEqual(result["protocolVersion"], 1)
        self.assertEqual(result["agentInfo"]["name"], "rex")
        self.assertFalse(result["agentCapabilities"]["loadSession"])

    def test_prompt_streams_and_answers(self) -> None:
        self.stdout.wait_id(self.request("initialize", {"protocolVersion": 1}))
        sid = self.stdout.wait_id(self.request("session/new", {"cwd": tempfile.gettempdir()}))["result"]["sessionId"]

        rid = self.request("session/prompt", {
            "sessionId": sid,
            "prompt": [{"type": "text", "text": "Reply with exactly: OK"}],
        })
        result = self.stdout.wait_id(rid)["result"]
        self.assertEqual(result["stopReason"], "end_turn")

        chunks = [
            msg["params"]["update"]["content"]["text"]
            for msg in self.stdout.messages()
            if (msg.get("method") == "session/update"
                and msg["params"]["update"].get("sessionUpdate") == "agent_message_chunk")
        ]
        self.assertEqual("".join(chunks), "OK")

    def test_cancel_maps_to_stop(self) -> None:
        self.stdout.wait_id(self.request("initialize", {"protocolVersion": 1}))
        sid = self.stdout.wait_id(self.request("session/new", {"cwd": tempfile.gettempdir()}))["result"]["sessionId"]
        self.stdin.send(json.dumps({
            "jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": sid},
        }) + "\n")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            session = self.server.sessions[sid]
            if any(item.get("type") == "stop" for item in session.sent):  # type: ignore[attr-defined]
                return
            time.sleep(0.01)
        self.fail("session/cancel did not reach rex as stop")

    def test_unknown_method_is_reported(self) -> None:
        rid = self.request("session/load", {"sessionId": "sess_x"})
        error = self.stdout.wait_id(rid)["error"]
        self.assertEqual(error["code"], -32601)


if __name__ == "__main__":
    unittest.main()
