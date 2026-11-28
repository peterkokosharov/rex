# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-5-5
# Licensed under MIT License

"""Gateway tool mode: Claude's native XML call syntax in a plain-text reply, and the per-turn reminder.

Tag strings are built from pieces (``tag``) so this file never holds the raw markup on one line.
"""

import importlib.util
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "claude_cli_gateway.py"
_spec = importlib.util.spec_from_file_location("claude_cli_gateway_native", _PATH)
gw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gw)

LT = "<"
NS = "antml" + ":"


def tag(name, attr=None, close=False, ns=""):
    a = f' name="{attr}"' if attr is not None else ""
    return f"{LT}{'/' if close else ''}{ns}{name}{a}>"


def inv(name, params, ns="", sep="\n"):
    body = "".join(tag("parameter", k, ns=ns) + v + tag("parameter", close=True, ns=ns) + sep for k, v in params)
    return tag("invoke", name, ns=ns) + sep + body + tag("invoke", close=True, ns=ns)


def wrap(body, w="function_calls", ns=""):
    return tag(w, ns=ns) + "\n" + body + "\n" + tag(w, close=True, ns=ns)


# Recorded 2026-11-28 (ai-rex, sonnet, turn 2 after a tool result): native markup as plain text,
# no opening wrapper and a stray closing tool_calls tag.
RECORDED = inv("fileops", [("operation", "read"), ("path", "pom.xml")]) + "\n" + tag("tool_calls", close=True)
assert RECORDED == ('\x3cinvoke name="fileops">\n\x3cparameter name="operation">read\x3c/parameter>\n'
                    '\x3cparameter name="path">pom.xml\x3c/parameter>\n\x3c/invoke>\n\x3c/tool_calls>')

FILEOPS = {"type": "function", "function": {
    "name": "fileops", "description": "Read or write a file.",
    "parameters": {"type": "object", "properties": {
        "operation": {"type": "string", "enum": ["list", "read", "write"]},
        "path": {"type": "string"},
        "content": {"type": "string"}},
        "required": ["operation", "path"], "additionalProperties": False}}}
LISTER = {"type": "function", "function": {
    "name": "lister", "description": "List.",
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string"}, "depth": {"type": "integer"}, "recursive": {"type": "boolean"},
        "filter": {"type": "object"}, "names": {"type": "array", "items": {"type": "string"}},
        "label": {"type": "string"}, "free": {}},
        "required": ["path"], "additionalProperties": False}}}
TOOLS = [FILEOPS, LISTER]
READ_POM = ("fileops", {"operation": "read", "path": "pom.xml"})


def spec(**req):
    req.setdefault("tools", TOOLS)
    return gw.tool_spec(req)


def interp(text, **req):
    return gw.interpret_reply({"text": text}, spec(**req))


# ---------------------------------------------------------------- parsing

def test_recorded_reply_is_a_tool_call():
    content, calls = interp(RECORDED)
    assert calls == [READ_POM]
    assert not content


@pytest.mark.parametrize("text", [
    wrap(inv("fileops", [("operation", "read"), ("path", "pom.xml")])),
    inv("fileops", [("operation", "read"), ("path", "pom.xml")], sep=""),
    wrap(inv("fileops", [("operation", "read"), ("path", "pom.xml")], ns=NS), ns=NS),
    inv("fileops", [("operation", "read"), ("path", "pom.xml")], ns=NS),
    wrap(inv("fileops", [("operation", "read"), ("path", "pom.xml")]), w="tool_calls"),
    tag("function_calls") + "\n" + inv("fileops", [("operation", "read"), ("path", "pom.xml")]),
    "  \n" + RECORDED + "\n\n",
])
def test_native_variants(text):
    assert interp(text)[1] == [READ_POM]


def test_multiple_invokes_in_order():
    text = wrap(inv("fileops", [("operation", "list"), ("path", ".")]) + "\n"
                + inv("fileops", [("operation", "read"), ("path", "pom.xml")]))
    assert interp(text)[1] == [("fileops", {"operation": "list", "path": "."}), READ_POM]


def test_values_follow_the_schema():
    text = inv("lister", [("path", "42"), ("depth", "2"), ("recursive", "true"), ("filter", '{"ext": [".py"]}'),
                          ("names", '["a", "b"]'), ("label", "123"), ("free", "[1]")])
    (call,) = interp(text)[1]
    assert call == ("lister", {"path": "42", "depth": 2, "recursive": True, "filter": {"ext": [".py"]},
                               "names": ["a", "b"], "label": "123", "free": [1]})


def test_string_values_are_kept_exactly():
    content = '  line 1\n<b>x</b> & "q"\n'
    (call,) = interp(inv("fileops", [("operation", "write"), ("path", "a.txt"), ("content", content)]))[1]
    assert call[1]["content"] == content


def test_leading_prose_becomes_content():
    content, calls = interp("I'll read the POM next.\n\n" + RECORDED)
    assert content == "I'll read the POM next." and calls == [READ_POM]


@pytest.mark.parametrize("text, reason", [
    (inv("bash", [("command", "rm -rf /")]), "unknown tool"),
    (inv("fileops", [("operation", "delete"), ("path", "a")]), "allowed values"),
    (inv("fileops", [("operation", "read")]), "path is required"),
    (inv("fileops", [("operation", "read"), ("path", "a"), ("mode", "x")]), "unexpected property"),
    (inv("fileops", [("operation", "read"), ("path", "a"), ("path", "b")]), "duplicate parameter"),
    (inv("lister", [("path", "a"), ("depth", "two")]), "type integer"),
    (inv("x/y", [("a", "b")]), "unknown tool"),
])
def test_native_calls_are_validated_like_envelopes(text, reason):
    with pytest.raises(gw.ProtocolError, match=reason):
        interp(text)


def test_native_respects_parallel_and_forced():
    two = inv("fileops", [("operation", "read"), ("path", "a")]) + "\n" + inv("lister", [("path", ".")])
    with pytest.raises(gw.ProtocolError, match="only one tool call"):
        interp(two, parallel_tool_calls=False)
    with pytest.raises(gw.ProtocolError, match="only the tool lister"):
        interp(RECORDED, tool_choice={"type": "function", "function": {"name": "lister"}})
    assert interp(RECORDED, tool_choice="required")[1] == [READ_POM]


@pytest.mark.parametrize("text", [
    "Claude writes calls like this:\n```xml\n" + RECORDED + "\n```\nThat is all.",
    "Example:\n```\n" + RECORDED + "\n```",
    "Example:\n```xml\n" + RECORDED,
    "Use " + inv("fileops", [("operation", "read"), ("path", "a")], sep="") + " to read it.",
    RECORDED + "\nThen I will summarise.",
    "The tag " + tag("invoke", "fileops") + " is native syntax.",
])
def test_prose_that_mentions_markup_is_not_a_call(text):
    content, calls = interp(text)
    assert calls == [] and content == text


def test_malformed_native_is_prose_but_worth_a_nudge():
    broken = tag("invoke", "fileops") + "\n" + tag("parameter", "path") + "pom.xml"
    content, calls = interp(broken)
    assert calls == [] and gw.wants_nudge(content)
    assert gw.wants_nudge(RECORDED + "\nThen I will summarise.")
    assert not gw.wants_nudge("Claude writes calls like this:\n```xml\n" + RECORDED + "\n```\nThat is all.")
    assert not gw.wants_nudge("The project is a Maven build with three modules.")


# ---------------------------------------------------------------- prompt: reminder on every turn

def _history():
    return [
        {"role": "system", "content": "You are rex."},
        {"role": "user", "content": "look at the project's code and say how it is"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "fileops", "arguments": '{"operation":"list","path":"."}'}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "pom.xml\nsrc/"},
    ]


def test_every_prompt_forbids_native_markup():
    first = gw.render_tool_prompt(_history()[:2], spec())
    assert "XML" in first and "function_calls" in first


def test_follow_up_turn_repeats_the_envelope_reminder_at_the_end():
    p = gw.render_tool_prompt(_history(), spec())
    reply = p[p.index("[Reply]"):]
    assert "tool results" in reply.lower() and "XML" in reply
    assert '{"tool_calls":' in reply and p.rstrip().endswith('{"content":"final answer"}.')
    assert p.index("[Reply]") > p.index("</tool_result>")
    first = gw.render_tool_prompt(_history()[:2], spec())
    assert "tool results" not in first[first.index("[Reply]"):].lower()


# ---------------------------------------------------------------- handler end to end (stub runner)

class Script:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, prompt, model, claude_bin, timeout, **kw):
        self.calls.append(prompt)
        reply = self.replies.pop(0)
        return {"text": reply, "usage": {"input_tokens": 5, "output_tokens": 1}}


@pytest.fixture
def make_srv():
    servers = []

    def make(runner):
        srv = gw.make_server(port=0, claude_bin="claude", runner=runner)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv

    yield make
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def post(srv, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


REQ = {"model": "sonnet", "messages": _history(), "tools": TOOLS, "tool_choice": "auto"}
ENVELOPE = '{"tool_calls":[{"name":"fileops","arguments":{"operation":"read","path":"pom.xml"}}]}'


def _calls(body):
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    return [(c["function"]["name"], json.loads(c["function"]["arguments"])) for c in choice["message"]["tool_calls"]]


def test_handler_turns_recorded_reply_into_tool_calls(make_srv, capsys):
    runner = Script(RECORDED)
    body = post(make_srv(runner), REQ)
    assert _calls(body) == [READ_POM] and len(runner.calls) == 1
    err = ""
    for _ in range(100):
        err += capsys.readouterr().err
        if "status=" in err:
            break
        time.sleep(0.02)
    assert "tools=2 calls=1 retry=0 form=native" in err and "pom.xml" not in err


def test_handler_invalid_native_call_is_retried(make_srv):
    runner = Script(inv("bash", [("command", "ls")]), ENVELOPE)
    assert _calls(post(make_srv(runner), REQ)) == [READ_POM]
    assert "unknown tool" in runner.calls[1]


def test_handler_malformed_native_is_nudged_once(make_srv):
    runner = Script(tag("invoke", "fileops") + "\n" + tag("parameter", "path") + "pom.xml", ENVELOPE)
    assert _calls(post(make_srv(runner), REQ)) == [READ_POM] and len(runner.calls) == 2


def test_handler_code_block_mention_is_plain_content(make_srv):
    text = "Claude writes calls like this:\n```xml\n" + RECORDED + "\n```\nThat is all."
    runner = Script(text)
    body = post(make_srv(runner), REQ)
    assert body["choices"][0]["message"]["content"] == text and len(runner.calls) == 1


# ---------------------------------------------------------------- review: indented code blocks stay prose

@pytest.mark.parametrize("text", [
    "Here is how you call it, for example:\n\n" + "\n".join(
        "    " + ln for ln in inv("fileops", [("operation", "read"), ("path", "pom.xml")]).split("\n")),
    "\n".join("    " + ln for ln in RECORDED.split("\n")),
    "Example:\n\t" + inv("fileops", [("operation", "read"), ("path", "pom.xml")], sep=""),
])
def test_markup_in_an_indented_code_block_is_prose(text):
    content, calls = interp(text)
    assert calls == [] and content == text
    assert not gw.looks_like_native_call(text)


def test_small_indent_is_still_a_call():
    assert interp("  " + RECORDED)[1] == [READ_POM]


# ---------------------------------------------------------------- salvage of a trailing envelope
#
# Recorded 2026-11-28 by the architect (deployed gateway, sonnet): a broken native stub, then a valid
# envelope in a ```json fence. It was neither an envelope nor native markup, the nudge failed too and
# the text went back as a normal answer (finish_reason stop), so rex ended the turn.

LOAD_SKILL = {"type": "function", "function": {
    "name": "load_skill", "description": "Load a skill.",
    "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}}
FENCE = "`" * 3
TRACE = (tag("invoke", "load_skill") + "\n" + tag("invoke", close=True) + "\n" + tag("invoke", close=True)
         + "\n\n" + FENCE + "json\n"
         + '{"tool_calls":[{"name":"load_skill","arguments":{"name":"code-review"}}]}\n' + FENCE)
assert TRACE == ('\x3cinvoke name="load_skill">\n\x3c/invoke>\n\x3c/invoke>\n\n```json\n'
                 '{"tool_calls":[{"name":"load_skill","arguments":{"name":"code-review"}}]}\n```')
SKILL_CALL = ("load_skill", {"name": "code-review"})
ENV_SKILL = '{"tool_calls":[{"name":"load_skill","arguments":{"name":"code-review"}}]}'


def test_trace_is_salvaged_without_the_broken_stub():
    content, calls = interp(TRACE, tools=TOOLS + [LOAD_SKILL])
    assert calls == [SKILL_CALL] and not content


@pytest.mark.parametrize("text, content", [
    ("I'll load the skill.\n" + ENV_SKILL, "I'll load the skill."),
    ("I'll load the skill.\n\n" + FENCE + "\n" + ENV_SKILL + "\n" + FENCE + "\n", "I'll load the skill."),
    (FENCE + "json\n" + ENV_SKILL + "\n" + FENCE, None),
    ("Loading.\n" + '{"content":"note","tool_calls":[{"name":"load_skill","arguments":{"name":"code-review"}}]}',
     "note"),
])
def test_trailing_envelope_is_salvaged(text, content):
    got, calls = interp(text, tools=TOOLS + [LOAD_SKILL])
    assert calls == [SKILL_CALL] and (got or None) == content


@pytest.mark.parametrize("text", [
    "Sure: " + ENV_SKILL,                                            # not at a line start
    "Example:\n" + ENV_SKILL + "\nThat is the format.",              # text after it
    "Example:\n" + FENCE + "json\n" + ENV_SKILL + "\n" + FENCE + "\nThat is the format.",
    "Note:\n" + ENV_SKILL[:-1],                                      # broken JSON
    "First:\n" + ENV_SKILL + "\nor:\n" + ENV_SKILL,                  # more than one envelope
    "Two:\n" + FENCE + "\n" + ENV_SKILL + "\n" + FENCE + "\n" + FENCE + "\n" + ENV_SKILL + "\n" + FENCE,
    "Done:\n" + '{"content":"only text"}',                           # no tool calls
])
def test_not_salvaged(text):
    content, calls = interp(text, tools=TOOLS + [LOAD_SKILL])
    assert calls == [] and content == text


def test_salvaged_envelope_is_validated():
    with pytest.raises(gw.ProtocolError, match="unknown tool"):
        interp("Running it.\n" + '{"tool_calls":[{"name":"bash","arguments":{"command":"ls"}}]}')
    with pytest.raises(gw.ProtocolError, match="required"):
        interp("Loading.\n" + '{"tool_calls":[{"name":"load_skill","arguments":{}}]}', tools=TOOLS + [LOAD_SKILL])


# ---------------------------------------------------------------- a failed nudge after an attempted call is an error

BROKEN = tag("invoke", "load_skill") + "\n" + tag("invoke", close=True) + "\n" + tag("invoke", close=True)


def test_handler_trace_gives_tool_calls(make_srv):
    runner = Script(TRACE)
    body = post(make_srv(runner), {**REQ, "tools": TOOLS + [LOAD_SKILL]})
    assert _calls(body) == [SKILL_CALL] and len(runner.calls) == 1


def test_handler_broken_call_twice_is_502_not_stop(make_srv):
    runner = Script(BROKEN, BROKEN)
    srv = make_srv(runner)
    req = urllib.request.Request(
        f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions",
        data=json.dumps({**REQ, "tools": TOOLS + [LOAD_SKILL]}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=20)
    assert ei.value.code == 502
    err = json.loads(ei.value.read())["error"]
    assert err["type"] == "tool_protocol_error" and "could not be read" in err["message"]
    assert len(runner.calls) == 2


def test_broken_call_without_time_to_nudge_is_504():
    now = [0.0]

    def runner(prompt, model, binary, timeout):
        now[0] += 295
        return {"text": BROKEN, "usage": {}}

    with pytest.raises(gw.GatewayError) as ei:
        gw.run_tool_turn(runner, "P", "sonnet", "claude", 300, spec(tools=TOOLS + [LOAD_SKILL]),
                         clock=lambda: now[0])
    assert ei.value.status == 504


def test_refusal_prose_still_keeps_the_first_answer_when_the_nudge_fails(make_srv):
    runner = Script("No such tool available: bash", "Still no.")
    body = post(make_srv(runner), REQ)
    assert body["choices"][0]["message"]["content"] == "No such tool available: bash"
