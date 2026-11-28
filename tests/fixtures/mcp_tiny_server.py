# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5
# Licensed under MIT License

"""Tiny stdio MCP server for the ``--mcp`` / ``--mcp-config`` tests (stdlib only, newline-delimited JSON-RPC).

Tools: ``greet`` (readOnlyHint, returns "hello <name>"), ``save`` (not read-only, writes the file named by the
env MCP_TINY_OUT), ``dirty`` (returns text with a control and a bidi character).
Env MCP_TINY_INIT_DELAY=<seconds> delays the initialize answer; MCP_TINY_PIDFILE=<path> records our pid.
"""

import json
import os
import sys
import time


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def text(s):
    return {"content": [{"type": "text", "text": s}]}


TOOLS = [
    {"name": "greet", "description": "Greet someone",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "save", "description": "Write a note to disk",
     "inputSchema": {"type": "object", "properties": {"note": {"type": "string"}}}},
    {"name": "dirty", "description": "Returns unclean text", "inputSchema": {"type": "object"}},
]


def main():
    pidfile = os.environ.get("MCP_TINY_PIDFILE")
    if pidfile:
        with open(pidfile, "w") as fh:
            fh.write(str(os.getpid()))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method, mid = msg.get("method"), msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            time.sleep(float(os.environ.get("MCP_TINY_INIT_DELAY", "0")))
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "tiny", "version": "1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name == "greet":
                out = text("hello " + str(args.get("name", "world")))
            elif name == "save":
                with open(os.environ["MCP_TINY_OUT"], "a", encoding="utf-8") as fh:
                    fh.write(str(args.get("note", "")) + "\n")
                out = text("saved")
            elif name == "dirty":
                out = text("a\x07b‮c")
            else:
                out = text("unknown tool", )
                out["isError"] = True
            send({"jsonrpc": "2.0", "id": mid, "result": out})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "no such method"}})


if __name__ == "__main__":
    main()
