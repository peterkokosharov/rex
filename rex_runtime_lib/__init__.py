# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-4-8, claude-sonnet-5-5
# Licensed under MIT License
"""rex_runtime_lib: a thin, importable facade over the rex ``runtimelib`` agent.

One clean entry point to run a rex agent in-process: create a session, send a user message, and iterate the
streamed tokens and tool events, without starting a separate process. Plus a one-call launcher for the existing
ACP server (for IDEs), and a bridge so the agent's model endpoint can be an in-process kiko gateway (or any
OpenAI-compatible endpoint). Everything here is wiring over ``runtimelib``; no agent logic is added.

    from rex_runtime_lib import Session, GatewayLLM
    from kiko_runtime_lib import Gateway

    kiko = Gateway.from_config(models={"gpt-4.1": "openai/gpt-4.1"})
    session = Session(llm_client=GatewayLLM(kiko.chat, model="gpt-4.1"), tools=[])
    for event in session.stream("say hello"):
        print(event)
"""

from __future__ import annotations

# Back-compat shim: the code moved to ai_runtime_rex.runtime.embed (kept until all importers use the new path).
from ai_runtime_rex.runtime.embed import AgentEvent, GatewayLLM, Session, llm_from_gateway, serve_acp

__all__ = ["Session", "AgentEvent", "GatewayLLM", "llm_from_gateway", "serve_acp"]
__version__ = "0.1.0"
