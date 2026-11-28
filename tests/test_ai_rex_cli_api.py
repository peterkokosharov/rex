# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5
# Licensed under MIT License
"""``ai-rex -i`` one-shot and the embeddable ``Agent``, against a stub OpenAI server (no real network)."""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class _Stub(BaseHTTPRequestHandler):
    bodies: list = []

    def log_message(self, *a):  # silent
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).bodies.append(body)
        last = body["messages"][-1]["content"]
        text = f"ECHO[{body['model']}]: {last}"
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for part in (text[:6], text[6:]):
                chunk = {"choices": [{"index": 0, "delta": {"content": part}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            end = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            self.wfile.write(f"data: {json.dumps(end)}\n\ndata: [DONE]\n\n".encode())
        else:
            data = json.dumps({"choices": [{"message": {"role": "assistant", "content": text},
                                            "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def do_GET(self):
        data = b'{"data": []}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def stub():
    _Stub.bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], _Stub.bodies
    server.shutdown()


def _cli(args, port, stdin="", extra_env=None, cwd=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AI_REX_", "RUNTIME_", "LITELLM_"))}
    env.update(PYTHONPATH=str(ROOT), AI_REX_PORT=str(port), **(extra_env or {}))
    return subprocess.run([sys.executable, "-P", "-m", "runtime", *args], input=stdin, text=True,
                          capture_output=True, env=env, cwd=cwd, timeout=60)


def test_oneshot_prints_plain_text_default_model(stub, tmp_path):
    port, bodies = stub
    r = _cli(["-i", "hello", "--no-tools"], port, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "ECHO[sonnet]: hello\n"
    assert "tools" not in bodies[0]


def test_oneshot_model_flag_and_piped_stdin(stub, tmp_path):
    port, bodies = stub
    r = _cli(["-i", "summarise", "-m", "haiku", "--no-tools"], port, stdin="line one\nline two\n", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("ECHO[haiku]: summarise")
    assert "line two" in r.stdout


def test_oneshot_stdin_only(stub, tmp_path):
    port, _ = stub
    r = _cli(["--no-tools"], port, stdin="just stdin", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "just stdin" in r.stdout


def test_oneshot_env_model_and_no_config_files_written(stub, tmp_path):
    port, _ = stub
    home = tmp_path / "h"
    home.mkdir()
    r = _cli(["-i", "x", "--no-tools"], port, extra_env={"AI_REX_MODEL": "opus", "HOME": str(home),
                                                         "USERPROFILE": str(home)}, cwd=tmp_path)
    assert r.stdout.startswith("ECHO[opus]")
    # session history may be written, but never a config file
    assert not list((home / ".config").rglob("config.yaml")) and not list((home / ".config").rglob(".env"))


def test_oneshot_gateway_down_is_one_line_error(tmp_path):
    r = _cli(["-i", "x", "--no-tools"], 1, cwd=tmp_path)
    assert r.returncode == 1 and r.stderr.startswith("ai-rex:") and "Traceback" not in r.stderr


def test_no_input_is_usage_error(stub, tmp_path):
    port, _ = stub
    r = _cli([], port, stdin="", cwd=tmp_path)
    assert r.returncode == 2


# ---- Agent API ----
def test_agent_run_and_stream(stub):
    from ai_runtime_rex.api import Agent

    port, bodies = stub
    agent = Agent(model="sonnet", port=port, tools=[])
    assert agent.run("hi") == "ECHO[sonnet]: hi"
    assert "tools" not in bodies[0]  # tools=[] really means no tools
    assert "".join(agent.stream("again")) == "ECHO[sonnet]: again"


def test_agent_stream_events(stub):
    from ai_runtime_rex.api import Agent

    port, _ = stub
    kinds = [e.kind for e in Agent(port=port).stream("hi", events=True)]
    assert "token" in kinds and kinds[-1] == "final"


def test_agent_base_url_and_error(stub):
    from ai_runtime_rex.api import Agent

    port, _ = stub
    assert Agent(base_url=f"http://127.0.0.1:{port}/v1").run("q") == "ECHO[sonnet]: q"
    with pytest.raises(Exception):
        Agent(port=1).run("q")


def test_agent_has_no_side_effects(stub, tmp_path):
    """No stdout/stderr, no SIGINT handler, no atexit hook, no history file."""
    port, _ = stub
    code = (
        "import signal, atexit, sys\n"
        "before = signal.getsignal(signal.SIGINT)\n"
        "from ai_runtime_rex.api import Agent\n"
        "n0 = atexit._ncallbacks()\n"
        f"a = Agent(port={port}, tools=[])\n"
        "out = a.run('hi'); list(a.stream('again'))\n"
        "assert signal.getsignal(signal.SIGINT) is before, 'SIGINT handler changed'\n"
        "assert atexit._ncallbacks() == n0, 'atexit hook registered'\n"
    )
    home = tmp_path / "h"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AI_REX_", "RUNTIME_"))}
    env.update(PYTHONPATH=str(ROOT), HOME=str(home), USERPROFILE=str(home))
    r = subprocess.run([sys.executable, "-P", "-c", code], capture_output=True, text=True, env=env,
                       cwd=tmp_path, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "" and r.stderr == ""
    assert not (home / ".config").exists()


# ---- flags ----
def test_input_file_output_file_append(stub, tmp_path):
    port, _ = stub
    (tmp_path / "in.txt").write_text("from file", encoding="utf-8")
    out = tmp_path / "out.txt"
    r = _cli(["-i", "ask", "-f", "in.txt", "-o", str(out), "--no-tools"], port, cwd=tmp_path)
    assert r.returncode == 0 and r.stdout == ""
    assert "ask" in out.read_text() and "from file" in out.read_text()
    _cli(["-i", "again", "-o", str(out), "--append", "--no-tools"], port, cwd=tmp_path)
    assert out.read_text().count("ECHO") == 2


def test_input_file_stdin_dash_and_json(stub, tmp_path):
    port, _ = stub
    r = _cli(["-f", "-", "--json", "--no-tools"], port, stdin="piped text", cwd=tmp_path)
    data = json.loads(r.stdout)
    assert data["ok"] and "piped text" in data["response"] and data["model"] == "sonnet"


def test_system_and_params_reach_the_request(stub, tmp_path):
    port, bodies = stub
    (tmp_path / "sys.txt").write_text("SYS-FROM-FILE", encoding="utf-8")
    r = _cli(["-i", "x", "--no-tools", "--system", "@sys.txt", "--temperature", "0.1", "--max-tokens", "33"],
             port, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "SYS-FROM-FILE" in bodies[0]["messages"][0]["content"]
    assert bodies[0]["temperature"] == 0.1 and bodies[0]["max_tokens"] == 33


def test_logs_go_to_stderr_or_file_never_stdout(stub, tmp_path):
    port, _ = stub
    r = _cli(["-i", "x", "--no-tools", "-vv"], port, cwd=tmp_path)
    assert "model=sonnet" in r.stderr and "model=" not in r.stdout
    log = tmp_path / "l.json"
    r = _cli(["-i", "x", "--no-tools", "-vv", "--log-file", str(log), "--log-format", "json"], port, cwd=tmp_path)
    assert r.stderr == "" and json.loads(log.read_text().splitlines()[0])["level"] == "debug"


def test_port_flag_base_url_and_exit_codes(stub, tmp_path):
    port, _ = stub
    r = _cli(["-i", "x", "--no-tools", "-p", str(port), "--base-url", f"http://127.0.0.1:{port}"], 1, cwd=tmp_path)
    assert r.returncode == 0
    assert _cli(["-f", "missing.txt"], port, cwd=tmp_path).returncode == 2
    assert _cli(["-i", "x", "--provider", "anthropic"], port, cwd=tmp_path,
                extra_env={"ANTHROPIC_API_KEY": ""}).returncode == 2


def test_api_key_env_name_missing_is_usage_error(stub, tmp_path):
    port, _ = stub
    r = _cli(["-i", "x", "--api-key-env", "NOPE_KEY"], port, cwd=tmp_path)
    assert r.returncode == 2 and "NOPE_KEY" in r.stderr
