<!-- SPDX-License-Identifier: MIT -->
<!-- Author: Peter Kokosharov <peter.kokosharov@gmail.com> -->
<!-- AI models: claude-opus-4-8 -->

# rex_runtime_lib

A thin, importable facade over the rex `runtimelib` agent. One clean entry point to run an agent **in-process**:
create a session, send a user message, and iterate the streamed tokens and tool events. Plus a one-call launcher
for the existing ACP server (for IDEs), and a bridge so the model endpoint can be an in-process kiko gateway (or
any OpenAI-compatible endpoint). No agent logic is added; it is wiring over `runtimelib`.

## Public API

* `Session(*, config=None, endpoint=None, llm_client=None, model=None, tools=None, role="CODER", on_token=None, on_tool_start=None, on_tool_end=None, config_overrides=None, **loop_kwargs)`
  One agent session (one `AgentLoop`, so the conversation persists across messages). Give it a model backend by
  either `llm_client=` (e.g. `GatewayLLM`, in-process, no HTTP) or `endpoint={"base_url","api_key","model"}`
  (the default HTTP client against any OpenAI-compatible endpoint).
  * `send(message) -> str` — block until the agent finishes; return the final assistant text.
  * `stream(message) -> Iterator[AgentEvent]` — yield events as they happen, ending with a `final` (or `error`).
  * `interrupt()` — stop the running agent at the next step. `reset()` — clear the conversation.
  * `loop` — the underlying `AgentLoop`.
* `AgentEvent(kind, text, name, args, result)` — `kind` is `token` | `tool_start` | `tool_end` | `final` | `error`.
* `GatewayLLM(chat, model, *, context_window=None)` — an `llm_client` over any `chat(body) -> dict | Iterable[dict]`
  OpenAI chat callable (a `kiko_runtime_lib.Gateway.chat` plugs straight in).
* `llm_from_gateway(gateway, model, *, context_window=None)` — wrap a kiko `Gateway` as an `llm_client`.
* `serve_acp(*, config=None, endpoint=None, model=None, local_tools=False, stream_in=None, stream_out=None, ...) -> int`
  Launch the existing ACP server (JSON-RPC over stdio); blocks until the client disconnects.

## Usage

```python
from rex_runtime_lib import Session, llm_from_gateway
from kiko_runtime_lib import Gateway

kiko = Gateway.from_config(models={"gpt-4.1": "openai/gpt-4.1"})     # in-process model gateway
session = Session(llm_client=llm_from_gateway(kiko, "gpt-4.1"), tools=[])
print(session.send("say hello in five words"))
for event in session.stream("and now count to three"):
    print(event.kind, event.text)
```

## Composition

`rex_runtime_lib` (agent) -> `kiko_runtime_lib` (model gateway) -> provider adapters (OpenAI, Gemini), all
in one process. `GatewayLLM` is the seam: `AgentLoop` only ever calls `llm_client.chat_completion(...)`, and
`GatewayLLM` serves that from `Gateway.chat(body)`. Point `endpoint=` at an HTTP URL instead for any
OpenAI-compatible server.

## Tests

`tests/test_rex_runtime_lib.py` (runs with the normal rex suite, `pytest` in `.venv-test`). It uses a fake
in-process gateway callable to check `send`, `stream` (tokens + final), the `GatewayLLM` bridge, and that
`serve_acp` starts and returns cleanly on stdin EOF.
