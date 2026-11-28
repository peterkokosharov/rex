<!--
Author: Peter Kokosharov <peter.kokosharov@gmail.com>
AI models: claude-sonnet-5-5, claude-opus-5-5
Licensed under MIT License
-->

# Agent API (embedding)

`from ai_runtime_rex.api import Agent` runs the engine in-process. It installs no Ctrl+C handler, prints nothing and
writes no history file. It registers no atexit hook, except `tools="all"` with MCP servers configured (released by
`close()`). Known phase-1 item: importing the engine appends its `vendor/` folder to `sys.path`. Later it moves to `lib_runtime_rex` (same names).

```python
from pathlib import Path
from ai_runtime_rex.api import Agent

agent = Agent(model="sonnet", port=4000)            # or base_url="http://host:4000", provider="anthropic"
text = Path("report.txt").read_text(encoding="utf-8")
print(agent.run("Summarise in five bullets:\n\n" + text))

for chunk in agent.stream("Now in one sentence"):   # text chunks; events=True gives AgentEvent objects
    print(chunk, end="", flush=True)
```

- `tools=None` or `[]` (default): no tools, plain chat. `tools=["fileops", "bash"]` enables only those (globs allowed);
  `tools="all"` is the full coding set (call `agent.close()` or use `with Agent(...) as a:` to release MCP).
  Tools run in the Docker guard; pass `config_overrides={"cli.unsafe_local_tools": True}` to run them on the host
  (the Agent does not fall back by itself, unlike the `ai-rex` command).
- Permission questions are denied unless you pass `asker=` (an object with
  `ask(request) -> "allow_once" | "allow_always" | "deny"`; anything else denies).
- Configuration is the same environment as the `ai-rex` command (see `docs/AI-REX.md`); explicit arguments win.
- `agent.reset()` forgets the conversation; `agent.interrupt()` stops a running call; errors raise.
- Tests can pass `llm_client=` (any object with `chat_completion`, e.g. `GatewayLLM(fake_chat, "m")`).
