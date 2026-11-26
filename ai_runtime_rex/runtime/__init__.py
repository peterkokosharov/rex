# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License
"""ai_runtime_rex.runtime: embeddable library API (see ``embed``). The cmd-line chat folds in later."""

from .embed import AgentEvent, GatewayLLM, Session, llm_from_gateway, serve_acp

__all__ = ["Session", "AgentEvent", "GatewayLLM", "llm_from_gateway", "serve_acp"]
