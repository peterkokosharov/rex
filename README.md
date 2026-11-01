# Runtime AI

A Claude Code-like runtime for OpenAI-compatible models via LiteLLM proxy, built on a modular stdlib-only foundation (`kikolib`) with Docker Guard sandboxing for all tool execution.

---

## What Is Completed

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
| **Agent Loop** | ⚠️ Partial | ReAct pattern implemented, **but tool_choice logic broken** (LLM calls tools infinitely) |
| **Session Manager** | ✅ Done | JSONL persistence, checkpoints, history reconstruction |
| **Hooks Engine** | ✅ Done | `HookEngine`, `HookRegistry`, event types (pre/post tool, agent, session, LLM) |
| **Skills System** | ✅ Done | YAML schema, entrypoints, hooks integration, auto_mode, permissions |
| **MCP Client** | ✅ Done | Stdio transport, initialize, list_tools/resources/prompts, call_tool |
| **Logging/Audit** | ✅ Done | Structured JSON logging, `AuditLogger` for security events |

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

## What Is NOT Working (Blockers)

### 1. Agent Loop Tool Calling (CRITICAL)
```python
# In runtimelib/agent/loop.py:_get_llm_response()
# Problem: LLM calls tools even for simple questions like "2+2"
# Current fix attempt: enable_tools = len(messages) > 1  # DOESN'T WORK
# Root cause: tool_choice="auto" + streaming tool call parsing fragile
```
**Impact:** Cannot test any agent functionality.

### 2. No Permissions Integration
- `ToolExecutor` executes tools directly without permission checks
- `PermissionPolicy` / `RoleRegistry` designed but not implemented

### 3. No Session Persistence in Agent Loop
- `SessionManager` exists but not wired into `AgentLoop.run()`

### 4. No Hooks in Agent Loop
- `HookEngine` exists but not called from loop (pre/post tool, agent events)

### 5. TaskSpawnTool is Stub
- Returns `{"status": "delegated", ...}` instead of spawning real subagent

---

## Immediate Next Steps (Priority Order)

### P0: Fix Agent Loop Tool Calling
```python
# runtimelib/agent/loop.py
# Options:
# 1. tool_choice="none" for first turn, enable only after explicit user request
# 2. Add "think" tool that must be called before any action tool
# 3. Fix streaming tool call parsing (current delta aggregation may be buggy)
# 4. Add debug logging to see raw LLM responses
```

### P1: Implement Permissions System
```
runtimelib/permissions/
├── __init__.py
├── policy.py          # PermissionPolicy, PermissionRule, PermissionResult
├── registry.py        # RoleRegistry (loads from ~/.config/runtime/roles/*.yaml)
└── executor.py        # PermissionCheckingToolExecutor wraps ToolExecutor
```

### P2: Wire Session + Hooks into Agent Loop
```python
# In AgentLoop.run():
# 1. session.append("user_message", ...)
# 2. hook_engine.pre_tool(...)
# 3. tool_executor.execute(...)
# 4. hook_engine.post_tool(...)
# 5. session.append("tool_result", ...)
# 6. checkpoint every N steps
```

### P3: Implement SandboxManager + Git Worktrees
```
runtimelib/guard/
├── sandbox.py         # SandboxManager, create_worktree(), commit_and_merge()
└── mounts.py          # Explicit RW/RO mount resolution
```

### P4: Real TaskSpawnTool
- Spawn new `AgentLoop` in new sandbox with role config
- Pass context as structured JSON
- Return structured result to parent

### P5: Orchestrator Pipeline
- Opus → Sonnet → Haiku → Sonnet workflow
- Parallel CODER + TESTER with worktree sync

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
cd /home/petark/runtime-ai
.venv/bin/python -m pytest tests/ -v
# 10 tests pass (config + guard)
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
/home/petark/runtime-ai/
├── runtimelib/                 # Core library (reusable)
│   ├── cli/                    # Command registry, readline, parser
│   ├── config/                 # Config, schema, sources
│   ├── llm/                    # LiteLLM client, messages, models
│   ├── guard/                  # GuardClient (JSON-RPC to Docker)
│   ├── tools/                  # Tool framework + concrete tools
│   ├── agent/                  # AgentLoop, state, callbacks
│   ├── session/                # SessionManager, checkpoints
│   ├── hooks/                  # HookEngine, events
│   ├── skills/                 # SkillRegistry, schema, auto_mode
│   ├── mcp/                    # MCP client
│   ├── logging/                # Structured logging, audit
│   └── kikolib/                # Stdlib-only foundation (copied from ~/kiko)
├── runtime/                    # App-specific (thin wrapper)
│   ├── __main__.py             # CLI entry point
│   └── commands.py             # /guard, /agent, /tool, /history
├── guard/                      # Docker Guard container (Dockerfile, entrypoint)
├── arch-docs/                  # Architecture documents
├── scripts/                    # Utility scripts (review_architecture.py)
├── tests/                      # Unit tests (config, guard)
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