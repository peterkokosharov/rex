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
| **CLI Framework** | ✅ Done | REPL + one-shot CLI, ANSI renderer, slash commands, model switching, JSON/no-stream modes |
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

- Python 3.10+ (stdlib-only runtime; no pip/venv required for basic CLI)
- An OpenAI-compatible endpoint (kiko-lite, LiteLLM, etc.)

Example with a local kiko-lite endpoint:

```bash
curl http://127.0.0.1:5000/health
curl http://127.0.0.1:5000/v1/models
```

### Run CLI (REPL)

```bash
cd /tmp/rex   # or the repo root
RUNTIME_LITELLM__BASE_URL=http://127.0.0.1:5000 \
RUNTIME_LITELLM__API_KEY=dummy \
RUNTIME_LITELLM__MODEL=gemini-lite \
python3 -m runtime
```

REPL commands: `/help`, `/model <name>`, `/models`, `/context`, `/reset`, `/clear`, `/verbose on|off`, `/local on|off`, `/doctor`, `/exit` (`/quit`, `/q`).

#### Context between requests

The REPL keeps one `AgentLoop`, so every prompt sees the previous user and assistant messages. `/reset` starts a fresh
context; `/clear` only clears the screen. Step budgets and interrupt flags are per-request, so a long first task or a
Ctrl+C cannot block the next prompt.

Use `/context` to see the estimated token count, the selected model's window and the auto-compact threshold.

#### Shell escape in the prompt

A line starting with `!` runs directly in the local shell — it is never sent to the model:

```
! pwd
! git status --short
! /bin/bash -c 'ls -la && git log -1 --oneline'
! /bin/bash pwd          # shorthand for /bin/bash -c pwd
```

Exit codes are reported as `[shell: exit N]` on stderr. `/shell <command>` does the same thing as a slash command.
`cli.shell_timeout` (default 120 s) limits each command. For one-shot runs the form is the same:
`python3 -m runtime '! git status'`. With `--json` the result includes `stdout`, `stderr` and `exit`.

#### Auto-compact

When the shared context reaches 90% of the current model's input window, older turns are compacted automatically:
200k models compact at 180k tokens, 1M models at 900k. The compact pass first shortens old tool output, then drops
whole old turns at user boundaries, and keeps the most recent exchange intact.

Model windows come from the built-in registry (or a size hint in the model name, e.g. `our-1m-model`). Override them
in config when needed:

```yaml
agent:
  context_window: 1000000        # single override for the active model
  context_windows:               # or per model name
    gemini-lite: 1000000
cli:
  compact_ratio: 0.9             # 0.9 = compact at 90% of the window
```

### Run with Claude Code CLI (no API key, chat only, no tools)

`scripts/claude_cli_gateway.py` is a small stdlib-only OpenAI-compatible gateway that answers
`/v1/models` and `/v1/chat/completions` by running the locally logged-in `claude` CLI
(`claude -p --model <m> --output-format json --tools ""`, prompt on stdin). No API key is needed.

- Binds to `127.0.0.1` only; default port 4000. Requests whose `Host` header is not `127.0.0.1` or
  `localhost` (with or without this port) get 403, which stops DNS-rebinding attacks from a browser.
- POST bodies must be sent as `Content-Type: application/json` (else 415).
- Without a token any `Authorization` header is accepted. With a token `T` every request needs
  `Authorization: Bearer T` (constant-time compare); set `RUNTIME_LITELLM__API_KEY=T` in rex.
  The token comes from `--token-file F` (file content, whitespace stripped), else `--token T`, else the
  `REX_GATEWAY_TOKEN` environment variable. `--token` works but prints a warning: command-line
  arguments are visible to other users in the process list.
- Models: `haiku` (default), `sonnet`, `opus`, or a full id such as `claude-sonnet-4-6`.
- Chat only: Claude's own tools are disabled, request `tools` are ignored, `tool_calls` are never returned,
  so rex's agent tools will not run through this backend.
- `stream: true` is answered as SSE (one content chunk, a stop chunk, `[DONE]`).
- Logs only method, path, model, duration and status. Timeout per call: `--timeout` (default 300 s).
- Each call runs in a fresh private temporary directory (0700 on POSIX) that is removed afterwards, with
  `--safe-mode` when the installed CLI supports it: no `CLAUDE.md`, memory, hooks or settings (user or
  project) load, while the CLI login still works, so replies do not know your personal instructions.
  Older CLIs without `--safe-mode` fall back to `--setting-sources user` (no project or local settings, but
  your user-level `CLAUDE.md` and settings still load and can change replies); the gateway prints a note then.
- A second gateway on the same port fails with "port N already in use" and a non-zero exit (on Windows the
  port is bound exclusively).

```bash
python3 scripts/claude_cli_gateway.py --port 4000 --default-model haiku

# second terminal
RUNTIME_LITELLM__BASE_URL=http://127.0.0.1:4000 RUNTIME_LITELLM__API_KEY=dummy RUNTIME_LITELLM__MODEL=haiku python3 -m runtime
```

Or copy `scripts/rex-claude-cli.example.yaml` to `~/.config/runtime/config.yaml`.

### JSON-lines bridge for external UIs (`--jsonl`)

An external front-end (for example the C++ Turbo Vision UI `Layka`) can spawn rex as a background process and drive it
over stdin/stdout. One `AgentLoop` lives for the whole session, so conversation context survives between `chat` messages.
stdout is **only** JSON lines — no banners, no warnings. Diagnostics go to stderr.

```bash
RUNTIME_LITELLM__BASE_URL=http://127.0.0.1:5000 \
RUNTIME_LITELLM__API_KEY=dummy \
python3 -m runtime --jsonl --model gemini-lite
```

Incoming lines (UI → rex):

```json
{"type":"chat","text":"Create hello.c"}
{"type":"model","model":"gemini-lite"}
{"type":"command","name":"model","value":"gemini-lite"}
{"type":"reset"}
{"type":"stop"}
{"type":"bye"}
```

Outgoing lines (rex → UI):

```json
{"type":"ready","model":"gemini-lite"}
{"type":"token","text":"I'll "}
{"type":"tool_start","name":"fileops","args":{"operation":"write"}}
{"type":"tool_end","ok":true,"error":null}
{"type":"final","text":"Done."}
{"type":"error","message":"..."}
{"type":"reset_ok"}
{"type":"model_ok","model":"gemini-lite"}
{"type":"bye"}
```

`stop` interrupts the running request from the reader thread, so a Stop button works without waiting for the answer.
Changing `model` updates the live loop; changing the workspace is done by starting rex in that directory (the UI passes
it as the child process cwd). `--ui-jsonl` is an alias of `--jsonl`.

### Agent Client Protocol (`rex acp`)

Editors that speak [ACP](https://agentclientprotocol.com/) can add rex next to Claude, Codex or Gemini.
JetBrains AI Assistant (2026.1+) reads `agent_servers` from its AI Assistant settings:

```json
{
  "agent_servers": {
    "rex": {
      "command": "/usr/bin/python3",
      "args": ["-m", "runtime", "acp", "--model", "gemini-lite"],
      "env": [
        { "name": "PYTHONPATH", "value": "/usr/lib/python3/dist-packages" },
        { "name": "RUNTIME_LITELLM__BASE_URL", "value": "http://127.0.0.1:4000" },
        { "name": "RUNTIME_LITELLM__API_KEY", "value": "dummy" }
      ]
    }
  }
}
```

From a source checkout use `python3 -m runtime acp ...` with `PYTHONPATH` pointing at the repo root; the ACP server
adds it to the child's `PYTHONPATH` automatically. `rex acp` and `runtime --acp` are equivalent.

ACP sessions map to one rex `--jsonl` child each: `session/prompt` streams `agent_message_chunk` updates from rex
`token` events, `tool_start`/`tool_end` become `tool_call` updates, `session/cancel` sends `stop`, and
`session/close` sends `bye`. stdout is JSON-RPC only; diagnostics go to stderr.

Deliberately not advertised: `loadSession` (context lives in memory), image/audio prompts, MCP passthrough and
permission gating. Tools run in the Docker guard by default; `--local-tools` runs them on the host with **no**
permission prompts, so use it only for local demos. Cost reporting is absent because rex reports tokens, not prices.

### Run CLI (one-shot)

```bash
RUNTIME_LITELLM__BASE_URL=http://127.0.0.1:5000 \
RUNTIME_LITELLM__API_KEY=dummy \
python3 -m runtime --model gemini-lite "Create hello.c in current dir, compile and run it"
```

Useful flags:

- `--model <id>` — select model
- `--json` — machine-readable output
- `--no-stream` — print final answer only
- `--verbose` — show verbose tool results
- `--local-tools` / `--unsafe-local-tools` — **DEV ONLY**: run tools on host without Docker Guard

> Guard is the secure default. Use `--local-tools` only for local dev/smoke tests.

With `--local-tools` the bash tool refuses commands containing `git push` and runs commands with a scrubbed
environment: variables whose names contain `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, `CREDENTIAL`, `AUTH`,
`COOKIE` or `DATABASE_URL`, names ending in `_PAT`, `LITELLM*` variables, and proxy variables whose value contains
`@` (user:pass) are dropped. The `cwd` of the bash and git tools must stay inside the workspace. These are
**guard rails against accidents, not a security boundary**: a command can still reach the network through aliases or
scripts, and on Linux it can read the parent's full environment from `/proc/<ppid>/environ`. Real isolation is the
Docker guard.

### Docker Guard (secure mode)

Tool execution defaults to Docker Guard. Build the image from repo root:

```bash
docker build -t runtime/guard:latest .
```

If Docker permissions/image are missing, the CLI shows hints; for quick local dev you can use `--local-tools`.

### Diagrams

Ask for a flowchart, sequence, class, state, ER or gantt diagram and rex calls the `render_diagram` tool. It renders
Mermaid source with [merman](https://github.com/Latias94/merman) (v0.8.0, Apache-2.0 OR MIT) to
`<workspace>/diagrams/<name>.svg` (or `.png`) and answers with a short Unicode text sketch plus a Markdown image
line such as `![login](diagrams/login.svg)`. Terminal front-ends show the sketch and can open the image on demand;
GUI front-ends show the image.

merman is not bundled. Unpack it anywhere and set `MERMAN_HOME` to the folder that contains `merman-cli`
(`merman-cli.exe` on Windows); otherwise `merman-cli` is looked up on `PATH`. If it is missing the tool returns a
clear error. The tool runs on the host and only writes inside the workspace, in both `--local-tools` and Docker
mode; if merman is not installed on the host, Docker mode returns the same error.

### Skills

A skill is a folder with a `SKILL.md` file: YAML frontmatter (`name`, `description`; other keys are ignored)
followed by Markdown instructions. The folder may hold extra files (references, scripts) next to it.

```
.rex/skills/release-notes/
  SKILL.md
  references/style.md
```

```markdown
---
name: release-notes
description: Write release notes from the git log. Use when asked for a changelog or release notes.
---

1. Run `git log --oneline <last-tag>..HEAD`.
2. Group the commits (features, fixes, other) and write short bullet points.
3. Follow `references/style.md` for tone.
```

Skills are searched in `<workspace>/.rex/skills/`, `~/.config/runtime/skills/`, then the folders in the config key
`skills.paths` (a list). If two skills share a name the first one wins and a warning is logged. A missing `name`
falls back to the folder name; a skill without `description` is skipped with a warning.

At session start only a compact index (`- name: description`, about 2 KB at most) is added to the system prompt.
When a task matches a description the model calls `load_skill(name)`, which returns the `SKILL.md` body and the
list of extra files; `read_skill_file(name, relpath)` reads one text file inside the skill folder (no `..`, no
absolute paths, max 200 KB). `rex skills` lists the skills found (name, path, description) and
`rex skills show <name>` prints the body.

### Run Tests

```bash
python3 -m unittest tests.test_cli -v
# If pytest is installed:
# python3 -m pytest tests/ -v
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
