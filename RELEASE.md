# Runtime AI - Release v0.1.0

## Overview

Runtime AI is a dual-mode execution runtime for AI agents, designed as a tool provider for orchestration systems (RONI, Archeo) and as a standalone vibe coding environment (like Codex/Claude Code).

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      ARCHEО (Product)                       │
│  • KB: GitHub + Confluence + Jira → local analysis          │
│  • Legacy auditor: docs, tickets, bugs, dead code, tests    │
│  • Runs ON: REX (vibe coding mode)                          │
└──────────────────────────┬──────────────────────────────────┘
                           │ uses as tool provider
┌──────────────────────────▼──────────────────────────────────┐
│                       RONI (Orchestrator)                    │
│  • Task decomposition → DAG                                  │
│  • Multi-agent delegation (Opus→Sonnet→Haiku→Sonnet)        │
│  • Doesn't remember 20k pages → asks Archeo                 │
│  • Runs ON: REX (tool provider)                             │
└──────────────────────────┬──────────────────────────────────┘
                           │ uses as tool provider
┌──────────────────────────▼──────────────────────────────────┐
│                        REX (Runtime)                         │
│  • Dual mode: Vibe Coding (human) + Tool Provider (RONI)    │
│  • AgentLoop, Tools, Sandbox, Sessions, Hooks, Permissions  │
│  • Docker Guard + Git Worktrees                             │
│  • LiteLLM proxy (multi-model)                              │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites
- Python 3.11+
- Docker (for Guard sandbox)
- LiteLLM proxy running on port 4000

### Installation
```bash
git clone https://github.com/petark/runtime-ai
cd runtime-ai
pip install -e .
```

### Configuration
Create `~/.config/runtime/config.yaml`:
```yaml
liteLLM:
  base_url: "http://127.0.0.1:4000"
  api_key: "your-key"
  model: "gemini/gemini-flash"

docker_guard:
  image: "runtime/guard:latest"
  memory: "2G"
  cpus: "2.0"
  network_mode: "bridge"
```

### Vibe Coding Mode (CLI)
```bash
# Interactive REPL
runtime

# Single prompt
runtime "Fix the login bug in auth.py"
```

### Tool Provider Mode (RONI/Archeo)
```python
from runtimelib import REX, AgentRole

rex = REX()

# Single agent
result = await rex.run_agent(
    task="Add JWT authentication",
    role=AgentRole(name="CODER", model_class="SONNET", max_steps=25),
    tools=["fileops", "bash", "git"],
    permissions=MyPermissionPolicy(),
)

# Subagent delegation (RONI)
handle = await rex.spawn_subagent(
    parent_context=current_context,
    task="Write tests for auth module",
    role=AgentRole(name="TESTER", model_class="HAIKU"),
    tools=["fileops", "bash", "git"],
    permissions=MyPermissionPolicy(),
)
test_result = await handle.wait()
```

## Features Implemented

### P0 - AgentLoop (Core)
- ReAct pattern with streaming
- Smart tool_choice: disabled for simple questions, enabled for actions
- Action keyword detection (run, execute, create, write, edit, delete, search, test, build, etc.)

### P1 - PermissionPolicy
- Role-based permissions (ORCHESTRATOR, ARCHITECT, CODER, TESTER, REVIEWER, RESEARCHER, DEBUGGER)
- fnmatch-based operation/pattern matching
- PermissionResult: ALLOW / DENY / ASK
- PermissionCheckingExecutor wrapper

### P2 - SandboxManager
- Git worktree isolation per agent
- Docker Guard container with explicit mount resolution (no globs)
- FileMergeLock (fcntl) + RedisMergeLock (distributed)
- Explicit RW/RO mount resolution

### P3 - REX API
- Dual mode: Vibe Coding (CLI) + Tool Provider (Python API)
- `run_agent()`, `spawn_subagent()`, `create_worktree()`, `commit_and_merge()`
- `subscribe_hooks()` for observability

### P4 - Session + Hooks
- JSONL session persistence with checkpoints
- HookEngine: SESSION_START, AGENT_START, AGENT_STEP, AGENT_END, TOOL_START, TOOL_END, AGENT_ERROR
- CheckpointManager: automatic checkpoints every N steps

### P5 - Security (PromptSanitizer)
- InputValidator: structural validation (control chars, zero-width, unicode norm, length, JSON schema)
- StructuredPromptBuilder: zone separation (SYSTEM/CONTEXT/USER/TOOL_OUTPUT)
- Defense-in-depth: structural only, no content filtering

## Configuration

### Project Config (`.runtime/config.yaml`)
```yaml
project:
  name: "my-project"
  root: "/home/user/my-project"

roles:
  CODER:
    model: "gemini/gemini-flash"
    max_steps: 25
    allowed_tools: ["fileops", "bash", "git", "websearch"]
    sandbox:
      network: "bridge"
      memory: "2G"

  TESTER:
    model: "gemini/gemini-flash-lite"
    max_steps: 15
    allowed_tools: ["fileops", "bash", "git"]
    sandbox:
      network: "bridge"
      memory: "1G"

task_spawn:
  max_depth: 3
  max_concurrent: 5
  max_total_per_session: 20

litellm_rate_limit:
  redis_url: "redis://localhost:6379/0"
  requests_per_minute: 60
  tokens_per_minute: 100000
  max_concurrent: 3

session:
  history_file: ".runtime/sessions/{session_id}/history.jsonl"
  checkpoint_interval: 10
```

### Role Config (`~/.config/runtime/roles/{role}.yaml`)
```yaml
role: CODER
model_class: SONNET
description: "Implementation agent"
max_steps: 25
permissions:
  allow:
    - "fileops:*"
    - "bash:pytest *"
    - "bash:cargo test *"
    - "bash:build *"
    - "git:commit"
    - "git:add"
    - "git:diff"
    - "git:status"
    - "git:log"
    - "git:branch"
    - "git:checkout"
    - "websearch:*"
  ask:
    - "bash:docker *"
    - "bash:sudo *"
  deny:
    - "bash:rm -rf *"
    - "bash:npm publish *"
allowed_tools:
  - fileops
  - bash
  - git
  - websearch
sandbox:
  network_mode: "bridge"
  memory_limit: "2G"
  cpu_limit: 2.0
```

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test
pytest tests/test_agent_loop.py -v
pytest tests/test_security.py -v
```

## Acceptance Tests (Definition of Done)

| Test | Description | Pass Criteria |
|------|-------------|---------------|
| **T1: Python Project** | Start in Python project, fix bug, add test, run pytest | Bug fixed, tests pass, diff shown |
| **T2: Java Project** | Start in Java project, make change, run Maven/Gradle | Change applied, tests pass |
| **T3: Model Switch** | Same task with `gemini/gemini-flash` vs `nvidia/nemotron-ultra` | Works without code changes |
| **T4: Session Recovery** | Interrupt task, restart runtime, resume | State restored, continues |
| **T5: Security** | Attempt `bash:rm -rf /` or `git:push` without permission | Blocked or ASK triggered |

## Project Structure

```
runtime-ai/
├── runtimelib/                 # Core library
│   ├── cli/                    # CLI framework
│   ├── config/                 # Config system
│   ├── llm/                    # LiteLLM client
│   ├── guard/                  # Docker Guard client + SandboxManager
│   ├── tools/                  # Tool framework
│   ├── agent/                  # AgentLoop, state, callbacks
│   ├── session/                # SessionManager, checkpoints
│   ├── hooks/                  # HookEngine, events
│   ├── permissions/            # PermissionPolicy, executor
│   ├── security/               # PromptSanitizer, InputValidator
│   ├── session/                # SessionManager
│   ├── skills/                 # Skills system
│   ├── mcp/                    # MCP client
│   ├── logging/                # Structured logging, audit
│   └── rex.py                  # REX API class
├── runtime/                    # Thin CLI wrapper
├── guard/                      # Docker Guard container
├── ocs/                        # Architecture docs (HTML + Mermaid)
├── arch-docs/                  # Markdown architecture docs
├── scripts/                    # Utility scripts
├── tests/                      # Unit tests
└── pyproject.toml
```

## Roadmap

### P6 - Orchestrator Pipeline (Next)
- Opus→Sonnet→Haiku→Sonnet pipeline execution
- Parallel CODER + TESTER with worktree sync
- Automated PR creation and review

### P7 - Production Hardening
- Web UI for session visualization
- Packaging (pip, docker, deb)
- Documentation site
- CI/CD pipeline

## License

MIT