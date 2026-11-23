# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-5-5
# Licensed under MIT License

"""Fake `claude -p` for the gateway tests. Launched as [sys.executable, fake_claude.py, ...].

Environment:
    FAKE_CLAUDE_SCRIPT   JSON file with a list of replies, one per call (the last one repeats).
                         A reply is a string (the `result` text) or an object with any of
                         result, structured_output, is_error, exit, sleep, stderr (text written to
                         stderr) and raw (written to stdout instead of the JSON result).
    FAKE_CLAUDE_COUNTER  file holding the number of calls so far (created when missing).
    FAKE_CLAUDE_LOG      file; one JSON line per call is appended: {"argv": [...], "stdin": "..."}.

Like the real CLI with --json-schema, a reply without structured_output gets one parsed from
`result` when that is a JSON object.
"""

import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    argv = sys.argv[1:]
    stdin = sys.stdin.buffer.read().decode("utf-8")
    counter = Path(os.environ["FAKE_CLAUDE_COUNTER"])
    n = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
    counter.write_text(str(n + 1), encoding="utf-8")
    log = os.environ.get("FAKE_CLAUDE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"argv": argv, "stdin": stdin}) + "\n")

    script = json.loads(Path(os.environ["FAKE_CLAUDE_SCRIPT"]).read_text(encoding="utf-8"))
    reply = script[min(n, len(script) - 1)]
    if isinstance(reply, str):
        reply = {"result": reply}
    if reply.get("sleep"):
        time.sleep(float(reply["sleep"]))
    if reply.get("stderr"):
        sys.stderr.write(reply["stderr"])
    if "raw" in reply:  # not a JSON result at all
        sys.stdout.write(reply["raw"])
        return int(reply.get("exit", 0))
    out = {"type": "result", "subtype": "success", "is_error": bool(reply.get("is_error")),
           "result": reply.get("result", ""), "num_turns": 1,
           "usage": {"input_tokens": 7, "output_tokens": 3}}
    if "--json-schema" in argv:
        structured = reply.get("structured_output")
        if structured is None:
            try:
                structured = json.loads(out["result"])
            except ValueError:
                structured = None
        if isinstance(structured, dict):
            out["structured_output"] = structured
    sys.stdout.write(json.dumps(out))
    return int(reply.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
