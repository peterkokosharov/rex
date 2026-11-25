# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-4-8
# Licensed under MIT License
"""rex_runtime_lib: the in-process agent facade, driven by a fake OpenAI chat callable (no network)."""
import io

from runtimelib.config import Config

from rex_runtime_lib import AgentEvent, GatewayLLM, Session, llm_from_gateway, serve_acp


def fake_chat(body):
    """A stand-in kiko gateway: OpenAI body in, OpenAI result out (stream or not)."""
    if body.get("stream"):
        def gen():
            yield {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hello "}}]}
            yield {"choices": [{"index": 0, "delta": {"content": "world"}}]}
            yield {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
        return gen()
    return {"choices": [{"message": {"role": "assistant", "content": "Hello world"}, "finish_reason": "stop"}]}


def _session():
    cfg = Config({"liteLLM": {"model": "gpt-x"}, "agent": {"max_steps": 3}})
    return Session(config=cfg, llm_client=GatewayLLM(fake_chat, "gpt-x"), tools=[])


def test_send_returns_final_text():
    assert _session().send("hi") == "Hello world"


def test_stream_yields_tokens_then_final():
    events = list(_session().stream("hi"))
    kinds = [e.kind for e in events]
    assert "token" in kinds and kinds[-1] == "final"
    tokens = "".join(e.text for e in events if e.kind == "token")
    assert tokens == "Hello world"
    assert events[-1].text == "Hello world"


def test_user_on_token_callback_fires():
    seen = []
    cfg = Config({"liteLLM": {"model": "gpt-x"}, "agent": {"max_steps": 3}})
    s = Session(config=cfg, llm_client=GatewayLLM(fake_chat, "gpt-x"), tools=[], on_token=seen.append)
    s.send("hi")
    assert "".join(seen) == "Hello world"


def test_llm_from_gateway_wraps_chat():
    class FakeGateway:
        chat = staticmethod(fake_chat)

    llm = llm_from_gateway(FakeGateway(), "gpt-x")
    assert isinstance(llm, GatewayLLM) and llm.default_model == "gpt-x"
    chunks = list(llm.chat_completion([], stream=True))
    assert "".join(c.content or "" for c in chunks) == "Hello world"


def test_conversation_persists_across_messages():
    s = _session()
    s.send("first")
    s.send("second")
    # system + (user, assistant) * 2 ; the loop keeps the running context.
    roles = [m.role for m in s.loop.state.messages]
    assert roles.count("user") == 2 and roles.count("assistant") == 2


def test_serve_acp_starts_and_stops_on_eof():
    cfg = Config({"liteLLM": {"model": "gpt-x"}})
    rc = serve_acp(config=cfg, stream_in=io.StringIO(""), stream_out=io.StringIO())
    assert rc == 0


def test_agent_event_shape():
    ev = AgentEvent("tool_start", name="bash", args={"cmd": "ls"})
    assert ev.kind == "tool_start" and ev.name == "bash" and ev.args == {"cmd": "ls"}
