<!--
Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
LLM: nvidia nemotron ultra
Licensed under MIT License
-->

# REX — Runtime Execution Layer

> **Status (2026-11-23):** this is the original v1.0 design. The runtime has moved on; where this text and the
> code disagree, the code wins. Section 10 lists what exists. Differences that matter most:
>
> - **Permissions (section 5)** are not the `ALLOW/DENY/ASK` policy class described there. The gate in
>   `runtimelib/agent/gate.py` uses configurable `allow` / `ask` / `deny` rules (`runtimelib/permissions/rules.py`,
>   config key `permissions`), a mode (`permissions.mode`: `guard`, `sandbox`, `local`) and optional
>   `pre_tool` / `post_tool` hook commands (`runtimelib/hooks/commands.py`). Precedence: deny rule, hook deny,
>   ask, allow. Front-ends answer asks over the JSONL bridge (`permission` / `permission_reply`).
> - **Tools:** `fileops`, `edit` (read-before-edit, returns a unified diff), `bash`, `git` (no `push`), `websearch`,
>   `webfetch` (host-side, SSRF-checked), `render_diagram` (Mermaid via merman-cli), `load_skill` /
>   `read_skill_file`, `task`, plus MCP tools (`mcp.servers`, stdio client).
> - **Sessions (section 6):** `SessionStore` in `runtimelib/session/` writes one `<id>.jsonl` per session under
>   `session.dir` (default `~/.config/runtime/sessions`). It is not yet wired into the REPL or the JSONL bridge
>   (resume/continue is open).
> - **Context:** the estimate counts system prompt, tool schemas and messages; one warning at
>   `context.warn_percent` (default 80), mandatory auto-compaction at 90 % (`cli.compact_ratio`, clamped 0.5-0.95).
> - **Instructions and skills:** `AGENTS.md` (user and project) goes into the system prompt;
>   Claude-style skills (`SKILL.md`) come from `~/.config/runtime/skills`, `skills.paths` and
>   `<workspace>/.rex/skills`. Repository content (project skills, project `AGENTS.md`) is used only when
>   `workspace.trust` is true.
> - **Tool choice (section 3.2)** is `auto` whenever tools exist; the first-turn `none` rule was dropped.
> - **LLM access:** LiteLLM or any OpenAI-compatible endpoint; `scripts/claude_cli_gateway.py` serves the local
>   `claude` CLI with tool calling over the same API. Python 3.12 or newer.
> - **Orchestrator API (section 2):** `REX.run_agent` / `spawn_subagent` exist, but `TaskSpawnTool.execute` is
>   still a placeholder and role tool sets are not applied by the CLI. Subagent orchestration stays out of scope.


**Version:** 1.0.0  
**Status:** Architecture Defined  
**Date:** 2026-11-01

---

## 1. Overview

**REX (Runtime Execution Layer)** is a **dual-mode runtime**:

| Mode | Use Case | Entry Point |
|------|----------|-------------|
| **Vibe Coding** | Human ↔ Agent (like Codex/Claude Code) | `runtime` CLI — interactive REPL |
| **Tool Provider** | an orchestrator → Agent (programmatic) | `REX` Python API — `await rex.run_agent(...)` |

**Same installation, same folder, same components.** Mode determined by how you invoke it.

---

### What REX Provides (Both Modes)

- **AgentLoop** — single-agent ReAct execution with tools
- **Tools** — fileops, bash, git, websearch, task (subagent spawn)
- **Sandbox** — Docker Guard with git worktree isolation
- **Sessions** — JSONL persistence, checkpoints, audit trail
- **Hooks** — event bus (human TUI in vibe mode, the orchestrator observability in provider mode)
- **Permissions** — pluggable policy engine (human ask/allow in vibe, the orchestrator injects in provider)

### What REX Does NOT Do (v1)

- ❌ Multi-agent orchestration (DAG, planning) — the orchestrator owns this
- ❌ PR creation/approval UI — the orchestrator/human owns this
- ❌ DAG task scheduling — the orchestrator owns this

---

## 2. Dual-Mode Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      REX Runtime                            │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │   Vibe CLI   │  │   REX API    │  │   Shared Core    │  │
│  │  (runtime)   │  │  (for the orchestrator)  │  │                  │  │
│  │              │  │              │  │  AgentLoop       │  │
│  │ REPL + TUI   │  │ run_agent()  │  │  Tools           │  │
│  │ /chat, /tool │  │ spawn_subagent│  │  Sandbox         │  │
│  │ /session     │  │ create_worktree│  │  Sessions        │  │
│  └──────┬───────┘  └──────┬───────┘  │  Hooks           │  │
│         │                 │          │  Permissions     │  │
│         └────────┬────────┘          │  Config          │  │
│                  ▼                 │  Logging         │  │
│         ┌─────────────────────┐    │                  │  │
│         │   Shared Core       │    │                  │  │
│         │  (single codebase)  │    │                  │  │
│         └─────────────────────┘    └──────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Mode Differences

| Aspect | Vibe Coding Mode | Tool Provider Mode |
|--------|------------------|-------------------|
| **Entry** | `runtime` (interactive) | `REX()` Python class |
| **User** | Human in terminal | orchestrator |
| **Permissions** | Human: allow/deny/ask prompt | the orchestrator injects `PermissionPolicy` |
| **Hooks** | TUI notifications | the orchestrator callback subscription |
| **Session** | Auto-managed | the orchestrator controls lifecycle |
| **Subagents** | Human triggers `/task` | the orchestrator calls `spawn_subagent()` |

---

## 2a. REX API for Orchestrators

### 2.1 High-Level Interface

```python
class REX:
    """Main entry point for orchestrators (orchestrator)."""
    
    async def run_agent(
        self,
        task: str,
        role: AgentRole,
        tools: list[str],
        permissions: PermissionPolicy,
        context: dict | None = None,
    ) -> AgentResult:
        """Execute a single agent task. Blocks until completion."""
    
    async def spawn_subagent(
        self,
        parent_context: AgentContext,
        task: str,
        role: AgentRole,
        tools: list[str],
        permissions: PermissionPolicy,
    ) -> SubagentHandle:
        """Spawn isolated subagent. Returns handle for monitoring."""
    
    def create_worktree(self, agent_id: str) -> WorktreeHandle:
        """Create git worktree for agent isolation."""
    
    def commit_and_merge(self, worktree: WorktreeHandle, message: str) -> MergeResult:
        """Commit worktree changes and merge to main (orchestrator-only)."""
    
    def subscribe_hooks(
        self, 
        events: list[HookEvent], 
        callback: Callable[[HookContext], None]
    ) -> Subscription:
        """Subscribe to agent lifecycle events for observability."""
```

### 2.2 Data Structures

```python
@dataclass
class AgentRole:
    name: str                    # "CODER", "TESTER", "REVIEWER", etc.
    model_class: ModelClass      # OPUS, SONNET, HAIKU, etc.
    max_steps: int
    allowed_tools: list[str]

@dataclass
class AgentContext:
    agent_id: str
    role: AgentRole
    worktree: WorktreeHandle
    session_id: str
    parent_id: str | None
    depth: int                   # Recursion depth (0 = root)

@dataclass
class AgentResult:
    success: bool
    output: str                  # Final agent response
    tool_calls: list[ToolCallRecord]
    tokens_used: TokenUsage
    cost: float
    checkpoints: list[CheckpointRef]

@dataclass
class SubagentHandle:
    agent_id: str
    context: AgentContext
    # Async monitoring
    async def wait() -> AgentResult:
    def cancel() -> None:
```

---

## 3. Agent Loop (Single-Agent Execution)

### 3.1 ReAct Loop

```
User Task + Context
       │
       ▼
┌──────────────────┐
│  AgentLoop       │
│  (ReAct pattern) │
└────────┬─────────┘
         │
         ▼
    ┌─────────┐
    │ Think   │  ← LLM streaming (tool_choice=none first turn)
    └────┬────┘
         │
         ▼
    ┌─────────┐
    │ Act     │  ← ToolExecutor + PermissionPolicy + Sandbox
    └────┬────┘
         │
         ▼
    ┌─────────┐
    │ Observe │  ← ToolResult → conversation history
    └────┬────┘
         │
    ┌────┴────┐
    │ Continue│───No──▶ Return AgentResult
    └────┬────┘
         │Yes
         ▼ (next step)
```

### 3.2 Tool Choice Logic (Critical Fix)

```python
# First turn with simple question: NO tools
# Subsequent turns: tools enabled
enable_tools = len(messages) > 1 or step > 1
tool_choice = "auto" if enable_tools else "none"
```

### 3.3 Tools Provided

| Tool | Description | Operations |
|------|-------------|------------|
| `fileops` | File operations | read, write, edit, list, glob, grep |
| `bash` | Shell commands | exec (with description, timeout, cwd, env) |
| `git` | Git operations | status, diff, add, commit, log, branch, checkout |
| `websearch` | Web search | query, max_results |
| `task` | Subagent spawn | prompt, role, tools, max_steps |

---

## 4. Sandbox & Isolation

### 4.1 Docker Guard Container

- **Per-agent container** — `docker run --rm -i` fresh per agent
- **JSON-RPC 2.0 over stdio** — bash, fs.*, net.fetch, git.*
- **Resource limits** — cgroups v2 (memory, cpu, pids)
- **Network** — `none` default, `bridge` only for roles needing web
- **Filesystem** — `--read-only` root + explicit RW overlays

### 4.2 Git Worktree Isolation

```python
class SandboxManager:
    def create_worktree(self, agent_id: str) -> WorktreeHandle:
        # git worktree add -b {agent_id}-branch .worktrees/{agent_id} main
        # Returns handle with absolute paths
    
    def get_container_mounts(self, agent_id: str) -> list[MountSpec]:
        # Concrete absolute paths ONLY (no globs)
        # 1. Worktree RW: /workspace
        # 2. Main RO: /workspace/main  
        # 3. Session RW: /workspace/.runtime/sessions
```

### 4.3 Explicit Mount Resolution (No Globs)

```yaml
# Role config - resolved to absolute paths at container start
sandbox:
  read_only_root: true
  read_write_mounts:
    - source: ".worktrees/{agent_id}"           # Agent's worktree
      target: "/workspace"
    - source: ".runtime/sessions/{session_id}/agents/{agent_id}"
      target: "/workspace/.runtime/sessions/agents/{agent_id}"
  read_only_overlays:
    - source: ".git"
      target: "/workspace/main/.git"
    - source: ".github/workflows"
      target: "/workspace/main/.github/workflows"
  # Secrets NEVER mounted - accessed via env/secret manager
```

---

## 5. Permissions (Pluggable Policy)

### 5.1 Policy Interface (Orchestrator Implements)

```python
class PermissionPolicy(ABC):
    @abstractmethod
    def check(self, tool: str, operation: str, args: dict) -> PermissionResult:
        """ALLOW / DENY / ASK"""
    
    @abstractmethod
    def get_allowed_tools(self, role: str) -> list[str]:
        pass
```

### 5.2 Default Policy (Reference Implementation)

```python
class DefaultPermissionPolicy(PermissionPolicy):
    """Reference policy - the orchestrator provides its own."""
    
    ROLE_PERMISSIONS = {
        "CODER": {
            "allow": ["fileops:*", "bash:pytest *", "bash:cargo test *", "git:commit"],
            "deny": ["git:push", "bash:rm -rf *", "bash:sudo *"],
        },
        "TESTER": {
            "allow": ["fileops:read", "bash:pytest *", "bash:npm test *"],
            "deny": ["fileops:write", "git:*"],
        },
        # ...
    }
```

### 5.3 Integration in ToolExecutor

```python
class PermissionCheckingExecutor(ToolExecutor):
    def __init__(self, policy: PermissionPolicy, inner: ToolExecutor):
        self.policy = policy
        self.inner = inner
    
    def execute(self, tool: Tool, args: dict, ...) -> ToolResult:
        result = self.policy.check(tool.name, args.get("operation", ""), args)
        if result == PermissionResult.DENY:
            return ToolResult(success=False, error=f"Denied: {result.reason}")
        if result == PermissionResult.ASK:
            # Orchestrator handles ASK via hook/callback
            raise PermissionAskError(tool.name, args, result.reason)
        return self.inner.execute(tool, args, ...)
```

---

## 6. Sessions & Persistence

### 6.1 JSONL History

```jsonl
# .runtime/sessions/{session_id}/history.jsonl
{"ts": "2026-11-01T10:00:00Z", "type": "session_start", "session_id": "abc", "data": {"project_root": "..."}}
{"ts": "...", "type": "agent_spawn", "agent_id": "coder-1", "role": "CODER", "depth": 1, "task": "..."}
{"ts": "...", "type": "user_message", "agent_id": "coder-1", "data": {"prompt": "Fix login bug"}}
{"ts": "...", "type": "tool_call", "agent_id": "coder-1", "tool": "fileops", "args": {"op": "read", "path": "auth.py"}}
{"ts": "...", "type": "tool_result", "agent_id": "coder-1", "tool": "fileops", "success": true, "result": "..."}
```

### 6.2 Checkpoints

- **Interval:** Every N steps (configurable, default 10)
- **Location:** `.runtime/sessions/{session_id}/checkpoints/step-{N}.json`
- **Content:** Full `AgentState` (messages, tokens, cost, step, depth)

### 6.3 Recovery

```python
def resume_session(session_id: str, from_checkpoint: str | None = None) -> AgentLoop:
    """Restore AgentLoop from checkpoint or latest history."""
```

---

## 7. Hooks (Event Bus for Orchestrator)

### 7.1 Event Types

```python
class HookEvent(Enum):
    # Agent lifecycle
    AGENT_START = "agent_start"
    AGENT_STEP = "agent_step"
    AGENT_END = "agent_end"
    AGENT_ERROR = "agent_error"
    
    # Tool execution
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    TOOL_ERROR = "tool_error"
    PERMISSION_ASK = "permission_ask"
    
    # Session
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    CHECKPOINT = "checkpoint"
    
    # Subagent
    SUBAGENT_SPAWN = "subagent_spawn"
    SUBAGENT_COMPLETE = "subagent_complete"
```

### 7.2 Hook Context

```python
@dataclass
class HookContext:
    event: HookEvent
    session_id: str
    agent_id: str
    role: str
    depth: int
    timestamp: datetime
    # Event-specific data
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_result: ToolResult | None = None
    error: str | None = None
```

### 7.3 Orchestrator Subscription

```python
# The orchestrator subscribes to all events for observability
subscription = rex.subscribe_hooks(
    events=list(HookEvent),  # All events
    callback=orchestrator.handle_hook_event
)
```

---

## 8. Configuration

### 8.1 Project Config (`.runtime/config.yaml`)

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
  redis_url: "redis://localhost:6379/0"  # Optional distributed mode
  requests_per_minute: 60
  tokens_per_minute: 100000
  max_concurrent: 3

session:
  history_file: ".runtime/sessions/{session_id}/history.jsonl"
  checkpoint_interval: 10
```

### 8.2 Environment Variables (Secrets)

```bash
# LiteLLM API keys - passed to Guard container via env
LITELLM_API_KEY=...
GEMINI_API_KEY=...
NVIDIA_API_KEY=...
# Never mounted as files
```

---

## 9. Acceptance Tests (Definition of Done)

| Test | Description | Pass Criteria |
|------|-------------|---------------|
| **T1: Python Project** | Start in Python project, fix bug, add test, run pytest | Bug fixed, tests pass, diff shown |
| **T2: Java Project** | Start in Java project, make change, run Maven/Gradle | Change applied, tests pass |
| **T3: Model Switch** | Same task with `gemini/gemini-flash` vs `nvidia/nemotron-ultra` | Works without code changes |
| **T4: Session Recovery** | Interrupt task, restart runtime, resume | State restored, continues |
| **T5: Security** | Attempt `bash:rm -rf /` or `git:push` without permission | Blocked or ASK triggered |

---

## 10. Implementation Status (2026-11-23)

| Component | Status | Location |
|-----------|--------|----------|
| CLI (REPL, one-shot, `--jsonl`, `--acp`) | Done | `runtime/`, `runtimelib/cli/`, `runtimelib/acp/` |
| Config (YAML, `.env`, keystore) | Done | `runtimelib/config/` |
| LLM client (streaming, tool calls), model registry | Done | `runtimelib/llm/` |
| Agent loop (ReAct, context warning, auto-compact) | Done | `runtimelib/agent/` |
| Tools: fileops, edit, bash, git, websearch, webfetch, render_diagram, skills | Done | `runtimelib/tools/` |
| MCP stdio client | Done | `runtimelib/mcp/`, `runtimelib/tools/mcp_tool.py` |
| Permission rules, gate, hook commands | Done | `runtimelib/permissions/`, `runtimelib/agent/gate.py`, `runtimelib/hooks/` |
| Standing instructions (`AGENTS.md`), trust switch | Done | `runtimelib/agent/instructions.py`, `runtimelib/trust.py` |
| Skills (`SKILL.md`) | Done | `runtimelib/skills/` |
| Docker guard client and image | Done | `runtimelib/guard/`, `guard/`, `Dockerfile` |
| Claude CLI gateway with tool calling | Done | `scripts/claude_cli_gateway.py` |
| Session store (JSONL) | Library done, not wired to the front-ends | `runtimelib/session/` |
| SandboxManager (worktrees, merge lock) | Code exists, no acceptance test | `runtimelib/guard/sandbox.py` |
| REX API class | Skeleton: `task` tool does not spawn | `runtimelib/rex.py`, `runtimelib/tools/taskspawn.py` |
| Per-role guard profiles | Not done | - |

Open work is tracked in `docs/plans/`.

---

## 11. Non-Goals (v1)

- ❌ Multi-agent orchestration (the orchestrator owns this)
- ❌ PR creation/approval UI (the orchestrator owns this)
- ❌ DAG task scheduling (the orchestrator owns this)
- ❌ Human review workflow (the orchestrator owns this)
- ❌ Distributed worker management (future)

---

*End of Architecture Document v1.0.0*