# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""Tiny fake MCP server for tests: stdio, newline-delimited JSON-RPC, stdlib only.

Tools: echo, fail (isError), slow (sleeps), die (exits), spam (floods stderr), log (methods seen),
env (reads an environment variable), pingstate (did the client answer our ping?).
tools/list is paged: page 1 has echo/fail/slow, page 2 the rest.
Env MCP_FIXTURE_FAIL_INIT=1 makes the server print to stderr and exit before answering.
"""

import json
import os
import sys
import time

SEEN: list[str] = []
PING_ID = "srv-ping-1"
ping_reply: dict = {}

PAGE1 = [("echo", "Echo text back"), ("fail", "Always fails"), ("slow", "Sleeps")]
PAGE2 = [("die", "Exits"), ("spam", "Floods stderr"), ("log", "Methods seen"),
         ("env", "Read env var"), ("pingstate", "Ping reply state")]


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def tool_def(name, desc):
    return {"name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}


def text(s, error=False):
    out = {"content": [{"type": "text", "text": s}]}
    if error:
        out["isError"] = True
    return out


def call_tool(name, args):
    if name == "echo":
        return text(str(args.get("text", "")))
    if name == "fail":
        return text("boom", error=True)
    if name == "slow":
        time.sleep(float(args.get("seconds", 3)))
        return text("slept")
    if name == "die":
        sys.exit(3)
    if name == "spam":
        for i in range(2000):
            sys.stderr.write("spam line %d %s\n" % (i, "x" * 200))
        sys.stderr.flush()
        return text("spammed")
    if name == "log":
        return text(",".join(SEEN))
    if name == "env":
        return text(os.environ.get(args.get("name", ""), "<unset>"))
    if name == "pingstate":
        return text(json.dumps(ping_reply))
    return text("unknown tool " + name, error=True)


def main():
    if os.environ.get("MCP_FIXTURE_FAIL_INIT"):
        sys.stderr.write("fixture: cannot start\n")
        sys.stderr.flush()
        sys.exit(1)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        if method is None:
            if msg.get("id") == PING_ID:
                ping_reply.update(msg)
            continue
        SEEN.append(method)
        mid = msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1"}}})
        elif method == "notifications/initialized":
            # Server-initiated request: a client that ignores it would hang real servers.
            send({"jsonrpc": "2.0", "id": PING_ID, "method": "ping"})
            send({"jsonrpc": "2.0", "id": "srv-roots-1", "method": "roots/list"})
        elif method == "tools/list":
            if params.get("cursor") == "page2":
                send({"jsonrpc": "2.0", "id": mid,
                      "result": {"tools": [tool_def(n, d) for n, d in PAGE2]}})
            else:
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "tools": [tool_def(n, d) for n, d in PAGE1], "nextCursor": "page2"}})
        elif method == "tools/call":
            send({"jsonrpc": "2.0", "id": mid,
                  "result": call_tool(params.get("name"), params.get("arguments") or {})})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601, "message": "no such method: " + method}})


if __name__ == "__main__":
    main()
