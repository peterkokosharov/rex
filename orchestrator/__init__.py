# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra
# Licensed under MIT License

"""Orchestrator package — multi-agent orchestration layer for ai-rex.

This package contains all orchestration logic that was previously embedded in the runtime:
- TaskSpawnTool (real implementation, not stub)
- Role policies (model class, max_steps, allowed tools, sandbox profile)
- Orchestrator class (spawn_subagent, run_agent, worktree management)
- Review gates, git workflow, push decisions

The runtime (runtimelib/) is now clean: tools, isolation, protocol only.
"""

# Avoid circular imports by not importing at module level
__all__ = [
    "TaskSpawnTool",
    "create_taskspawn_tool",
    "RolePolicy",
    "get_role_policy",
    "load_policy_file",
    "Orchestrator",
    "SubagentHandle",
    "AgentContext",
]

__version__ = "0.1.0"