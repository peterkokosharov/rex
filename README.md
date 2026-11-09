<!--
Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
LLM: nvidia nemotron ultra
Licensed under MIT License
-->

# Runtime AI

A Claude Code-like runtime for OpenAI-compatible models via LiteLLM proxy, built on a modular stdlib-only foundation (`kikolib`) with Docker Guard sandboxing for all tool execution.

---

## What Is Done

### Core Infrastructure (`runtimelib/`)

| Module | Status | Description |
|--------|--------|-------------|
| **CLI Framework** | ✅ Done | `CommandRegistry`, `ReadlineInterface` (history, tab completion), `ArgumentParser` |
| **Config System** | ✅ Done | YAML + `.env` + `RUNTIME_*` env overrides, nested keys, schema validation |
| **LLM Client** | ✅ Done | `LiteLLMClient` with streaming, tool calls, retry policy, cost tracking |
| **Model Registry** | ✅ Done | 20+ models with kiko.db classification (TEXT/REASONING/EYE/VOICE/MEMORY/AGENT/TOOL/IMAGE/VIDEO/CODE) |
| **Docker Guard** | ✅ Done | JSON-RPC 2.0 over stdio, bash/fs/net/git methods, cgroups, network policies |
| **Tool Framework** | ✅ Done | Base `Tool`, `ToolSchema`, `ToolRegistry`, `ToolExecutor` (timeout, retry) |
| **Concrete Tools** | ✅ Done | `fileops`, `bash`, `git`, `task` (stub), `websearch` (stub) |
| **Agent Loop** | ✅ Done | ReAct pattern with streaming; smart tool_choice (disabled for simple questions, action-keyword detection) |
| **Session Manager** | ✅ Done | JSONL persistence, checkpoints, history reconstruction, wired into AgentLoop |
| **Hooks Engine** | ✅ Done | `HookEngine`, `HookRegistry`, event types (pre/post tool, agent, session, LLM) |
| **Permissions** | ✅ Done | Role-based permissions (ORCHESTRATOR/CODER/TESTER/…), fnmatch matching, ALLOW/DENY/ASK |
| **SandboxManager** | ✅ Done | Git worktree isolation per agent, explicit RW/RO mount resolution, FileMergeLock/RedisMergeLock |
| **REX API** | ✅ Done | `run_agent()`, `spawn_subagent()`, `create_worktree()`, `commit_and_merge()`, `subscribe_hooks()` |
| **Security** | ✅ Done | `InputValidator` (structural validation), `StructuredPromptBuilder` (zone separation), defense-in-depth |
| **Skills System** | ✅ Done | YAML schema, entrypoints, hooks integration, auto_mode, permissions |
| **MCP Client** | ✅ Done | Stdio transport, initialize, list_tools/resources/prompts, call_tool |
| **Logging/Audit** | ✅ Done | Structured JSON logging, `AuditLogger` for security events |

### Implemented Phases

#### P0 — AgentLoop (Core)
- ReAct pattern with streaming
- Smart tool_choice: disabled for simple questions, enabled for action requests
- Action keyword detection (run, execute, create, write, edit, delete, search, test, build, etc.)

#### P1 — PermissionPolicy
- Role-based permissions (ORCHESTRATOR, ARCHITECT, CODER, TESTER, REVIEWER, RESEARCHER, DEBUGGER)
- fnmatch-based operation/pattern matching
- PermissionResult: ALLOW / DENY / ASK
- PermissionCheckingExecutor wrapper

#### P2 — SandboxManager
- Git worktree isolation per agent
- Docker Guard container with explicit mount resolution (no globs)
- FileMergeLock (fcntl) + RedisMergeLock (distributed)
- Explicit RW/RO mount resolution

#### P3 — REX API
- Dual mode: Vibe Coding (CLI) + Tool Provider (Python API)
- `run_agent()`, `spawn_subagent()`, `create_worktree()`, `commit_and_merge()`
- `subscribe_hooks()` for observability

#### P4 — Session + Hooks
- JSONL session persistence with checkpoints wired into AgentLoop
- HookEngine: SESSION_START, AGENT_START, AGENT_STEP, AGENT_END, TOOL_START, TOOL_END, AGENT_ERROR
- CheckpointManager: automatic checkpoints every N steps

#### P5 — Security (PromptSanitizer)
- InputValidator: structural validation (control chars, zero-width, unicode norm, length, JSON schema)
- StructuredPromptBuilder: zone separation (SYSTEM/CONTEXT/USER/TOOL_OUTPUT)
- Defense-in-depth: structural only, no content filtering

### Architecture Design (`arch-docs/`)

| Document | Version | Status |
|----------|---------|--------|
| `role-based-agent-architecture.md` | 0.2.0 | ✅ Revised after Gemini review |
| `gemini-review.md` | — | ✅ Gemini 3.5 Flash-Lite review output |
| `GEMINI_REVIEW_PROCEDURE.md` | — | ✅ How to run reviews |

### Key Design Decisions (v0.2.0)

1. **Git Worktrees** — Each subagent gets isolated worktree; only Orchestrator merges to main
2. **Explicit RW Mounts** — Project root read-only; only `.worktrees/{agent_id}/**` and session data mounted RW
3. **Network for Build Tools** — CODER/TESTER/DEBUGGER have `network: bridge` for npm/pip/cargo
4. **Task Recursion Guards** — `max_depth=3`, parent→child role matrix, `CircuitBreaker`
5. **Parallel Sync** — Shared `.worktrees/` directory with read-only cross-agent access
6. **Prompt Injection Protection** — `PromptSanitizer`, context injection rules, system prompt isolation

---

## Plan

### P6 — Orchestrator Pipeline
- Opus → Sonnet → Haiku → Sonnet pipeline execution
- Parallel CODER + TESTER with worktree sync
- Automated PR creation and review

### P7 — Production Hardening
- Web UI for session visualization
- Packaging (pip, docker, deb)
- Documentation site
- CI/CD pipeline

---

## How to Run

### Prerequisites
```bash
# LiteLLM proxy on port 4000 (already running)
curl http://127.0.0.1:4000/health

# Models available:
curl http://127.0.0.1:4000/v1/models
# gemini/gemini-flash, gemini/gemini-flash-lite, nvidia/nemotron-ultra, etc.
```

### Run Tests
```bash
cd runtime-ai
.venv/bin/python -m pytest tests/ -v
```

### Run CLI
```bash
.venv/bin/python -m runtime
# Or with prompt:
.venv/bin/python -m runtime "your prompt here"
```

### Run Architecture Review
```bash
.venv/bin/python scripts/review_architecture.py
# Output: arch-docs/gemini-review.md
```

---

## Project Structure

```
runtime-ai/
├── runtimelib/                 # Core library (reusable)
│   ├── cli/                    # Command registry, readline, parser
│   ├── config/                 # Config, schema, sources
│   ├── llm/                    # LiteLLM client, messages, models
│   ├── guard/                  # GuardClient (JSON-RPC to Docker) + SandboxManager
│   ├── tools/                  # Tool framework + concrete tools
│   ├── agent/                  # AgentLoop, state, callbacks
│   ├── session/                # SessionManager, checkpoints
│   ├── hooks/                  # HookEngine, events
│   ├── permissions/            # PermissionPolicy, executor
│   ├── security/               # PromptSanitizer, InputValidator
│   ├── skills/                 # SkillRegistry, schema, auto_mode
│   ├── mcp/                    # MCP client
│   ├── logging/                # Structured logging, audit
│   ├── kikolib/                # Stdlib-only foundation (copied from ~/kiko)
│   └── rex.py                  # REX API class
├── runtime/                    # App-specific (thin wrapper)
│   ├── __main__.py             # CLI entry point
│   └── commands.py             # /guard, /agent, /tool, /history
├── guard/                      # Docker Guard container (Dockerfile, entrypoint)
├── arch-docs/                  # Architecture documents
├── ocs/                        # Architecture docs (HTML + Mermaid)
├── scripts/                    # Utility scripts (review_architecture.py)
├── tests/                      # Unit tests
├── .venv/                      # Virtual environment
└── pyproject.toml              # Package config
```

---

## Development Workflow

1. **Write code** in `runtimelib/` (reusable) or `runtime/` (app-specific)
2. **Add tests** in `tests/` (parallel with code)
3. **Run tests** → `pytest tests/ -v`
4. **Git commit** after each completed task (local only)
5. **Update arch-docs** for architectural changes
6. **Run Gemini review** for major design changes

---

## Configuration

### Global Config (optional)
`~/.config/runtime/config.yaml`:
```yaml
liteLLM:
  base_url: "http://127.0.0.1:4000"
  api_key: "dummy"
  model: "gemini/gemini-flash"
```

### Role Configs
`~/.config/runtime/roles/{ORCHESTRATOR,CODER,TESTER,...}.yaml` — see `arch-docs/role-based-agent-architecture.md` §10.1

---

## License

MIT
