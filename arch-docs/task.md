# Task: Build a Claude-like Runtime for OpenAI-compatible Models via LiteLLM Proxy

## Objective

Create a runtime environment (similar to Claude Code) that works with AI models supporting the OpenAI API format, accessed through a LiteLLM proxy.

## Requirements

### Core Functionality
- **Runtime CLI**: Interactive command-line interface similar to Claude Code
- **Model Agnostic**: Works with any model exposing OpenAI-compatible API (via LiteLLM proxy)
- **Streaming Support**: Real-time token streaming from model responses
- **Tool Use / Function Calling**: Support for tool definitions and execution (OpenAI function calling format)
- **Conversation Management**: Multi-turn conversations with context retention
- **Session Persistence**: Save/load conversation history

### LiteLLM Proxy Integration
- Configure base URL for LiteLLM proxy endpoint
- Authentication via proxy (API keys, etc.)
- Model routing through proxy (specify model name, proxy handles routing)
- Support for proxy-specific headers/configuration

### Orchestration & Agent System (Claude Code parity)
- **Hierarchical Agent Orchestration**: Chat creates agents, agents spawn subagents recursively
- **Agent Types**: General-purpose, specialized (research, code, review, etc.), background/daemon agents
- **Inter-agent Communication**: Message passing, shared context, delegation patterns
- **Agent Lifecycle**: Spawn, monitor, wait-for-idle, terminate, resume

### Built-in Tools (available to all agents)
- **WebSearch**: Search the web, fetch and extract content from URLs
- **File Operations**: Read, Write, Edit, List, Glob, Grep files in workspace
- **Bash Execution**: Run shell commands with timeout, background, permission controls
- **Task/Subagent Spawning**: Launch new agents with specific prompts and tool access
- **MCP Integration**: Connect to Model Context Protocol servers

### Skills System (Standard Format)
- **Standard Skill Format**: YAML/JSON skill definition (name, description, version, entrypoint, schema, hooks, permissions, auto_mode)
- **Skill Registry**: Local + remote skill discovery, installation, versioning, dependency resolution
- **Hooks**: Pre/post tool execution, pre/post agent spawn, pre/post slash command, session start/end, error handling
- **Auto Mode**: Skills can declare `auto_mode: true` to run without interactive confirmation (with policy/permission gates)
- **Skill Composition**: Skills can chain, nest, or delegate to other skills
- **Skill Marketplace**: Optional remote index (GitHub, HTTP) for sharing skills

### Features (Claude Code parity)
- **Slash Commands**: Extensible command system (`/help`, `/config`, etc.)
- **Agent/Subagent System**: Spawn specialized agents for tasks
- **File Operations**: Read, write, edit, search files in workspace
- **Bash Execution**: Run shell commands with permission controls
- **MCP Support**: Model Context Protocol server integration
- **Hooks**: Pre/post tool execution hooks
- **Permission System**: Granular tool permission management
- **Status Line**: Configurable status display

### Sandbox / Guard (Docker-based, inspired by Codex)
- **Docker Container**: Runs in the same folder as the project (`/home/petark/runtime-ai/`)
- **Guard Role**: All bash execution, file operations, and network calls go through the container
- **Isolation**: Filesystem isolation, network policies, resource limits (CPU, memory, pids)
- **Persistence**: Workspace mounted read-write; config/secrets mounted read-only
- **Entrypoint**: Minimal init that exposes a JSON-RPC or stdio interface for tool execution
- **Image**: Built from `Dockerfile` in project root; base: `python:3.11-slim` + `kikolib` + runtime code

### Technical Stack (Required)
- **Language**: Python 3.10+
- **Dependencies**: stdlib ONLY — no external pip packages
- **Foundation**: `kikolib` from `~/kiko/kiko/kikolib/` (already available locally)
  - `http_client.py` — HTTP/1.1 client with keep-alive pool, proxy, SSE streaming, TLS via `ssl` (OpenSSL)
  - `keystore.py` — OS secret store integration (libsecret via `secret-tool`, kwallet via `kwallet-query`, etc.)
  - `factory.py` — Thread-safe pluggable implementation registry with probes
  - `retry.py` — Exponential backoff, jitter, Retry-After support
  - `posix_utils.py` — Signals, RSS monitoring, systemd notify/watchdog, private file ops
  - `server.py` — Threaded HTTP/1.1 server (`ThreadingHTTPServer`), routing, SSE, systemd integration
  - `yaml_lite.py` — YAML parser (stdlib only)
  - `dotenv.py` — .env parser (stdlib only)
  - `tokens.py` — Token counting (stdlib only)
  - `sse.py` — SSE parsing (stdlib only)
- **Native Linux libraries** (stable, pre-installed):
  - OpenSSL via Python's `ssl` module
  - libsecret via `secret-tool` CLI (GNOME Keyring / KDE)
  - kwallet via `kwallet-query` CLI (KDE)
  - systemd via `sd_notify` / `WATCHDOG_USEC`
  - `/proc` filesystem for RSS monitoring
- **Configuration**: YAML (via `yaml_lite`) + .env (via `dotenv`)
- **Containerization**: Docker (guard/sandbox for all tool execution)

## Research Findings: Open Source vs Claude Code

### Projects Analyzed
| Project | Stars | Language | Key Strengths |
|---------|-------|----------|---------------|
| **Aider** (Aider-AI/aider) | 25k+ | Python | Multi-model routing, Repomap (ctags+tree-sitter), Git integration, Voice/Whisper, Benchmark suite |
| **learn-claude-code** (shareAI-lab) | 79k+ | Python | Modular harness (17 steps), Hooks, Permissions, Subagents, MCP, Background tasks, Skills |
| **CoreCoder** (he-yufeng) | 1.8k+ | Python | Minimal (~1k LOC), Clean LLM abstraction, Checkpoints, Any OpenAI-compat model |
| **pydantic-deepagents** (vstorm-co) | 1.1k+ | Python | PydanticAI tool-calling, Sandboxed exec, Multi-agent teams, Skills, Unlimited context, Docker |
| **Nano** (pnegahdar) | 180 | Python | Single file (<200 LOC), Single tool (execute_shell), Auto-approve, Session persistence |

### Feature Coverage Matrix

| Feature | Aider | learn-claude-code | CoreCoder | pydantic-deepagents | Nano | **Claude Code** |
|---------|-------|-------------------|-----------|---------------------|------|-----------------|
| Multi-model support | ✅ Excellent | ✅ OpenAI API | ✅ Any OpenAI-compat | ✅ PydanticAI | ✅ OpenAI only | ✅ Anthropic only |
| ReAct Loop | ✅ | ✅ (s01) | ✅ | ✅ | ✅ Minimal | ✅ Advanced |
| Subagents | ❌ | ✅ (s06, s13) | ❌ | ✅ | ❌ | ✅ **Hierarchical** |
| Skills/Plugins | ❌ | ✅ (s07, skills/) | ❌ | ✅ (bundled_skills) | ❌ | ✅ **Rich ecosystem** |
| Hooks | ❌ | ✅ (s04) | ✅ | ✅ | ❌ | ✅ **Extensive** |
| Permission System | ✅ Basic | ✅ (s03) | ✅ | ✅ | ✅ (y/a/n) | ✅ **Granular** |
| MCP Support | ❌ | ✅ (s14) | ✅ | ✅ | ❌ | ✅ Native |
| Context Compaction | ✅ Repomap | ✅ (s08) | ❌ | ✅ Unlimited | ❌ | ✅ **Advanced** |
| Background Tasks | ❌ | ✅ (s10,s11,s12) | ❌ | ✅ | ❌ | ✅ |
| Session Persistence | ✅ Git-based | ✅ | ✅ Checkpoints | ✅ | ✅ JSON | ✅ |
| Repo Intelligence | ✅ **Repomap** | ❌ | ❌ | ❌ | ❌ | ✅ **Semantic AST** |
| Slash Commands | ✅ /commands | ✅ | ❌ | ✅ | ❌ | ✅ **Extensible** |
| Status Line | ✅ | ❌ | ❌ | ✅ | ❌ | ✅ **Configurable** |
| Docker Sandbox | ✅ (docker/) | ❌ | ❌ | ✅ | ❌ | ❌ (local only) |
| Web Search | ❌ | ❌ | ✅ | ❌ | ❌ | ✅ **Native** |
| Voice Input | ✅ Whisper | ❌ | ❌ | ❌ | ❌ | ❌ |

### What Open Source Projects HAVE (We Can Adopt)
- **Aider**: Multi-model routing, Repomap for repo intelligence, Git integration, Benchmarks, Voice input
- **learn-claude-code**: Modular agent harness, Hooks system, Permission system, Subagent spawning, MCP plugin, Background tasks/cron, Workflow runtime, Skills structure
- **CoreCoder**: Minimalist architecture, Clean LLM abstraction, Checkpoint system, MCP integration, Any OpenAI-compat model
- **pydantic-deepagents**: PydanticAI-based tool calling, Sandboxed execution, Multi-agent teams, Skills bundling, Unlimited context, Docker deploy
- **Nano**: Radical simplicity, Single-tool philosophy (execute_shell), Auto-approve mode, Session persistence

### What Claude Code Has MORE (Our "MORE" to Invent/Implement)

#### 1. **Native Anthropic Integration** → **Our Equivalent: LiteLLM Proxy Native**
- First-class LiteLLM support: model routing, fallback chains, cost tracking, load balancing, per-model configs

#### 2. **Semantic Code Intelligence** → **Our Invention: kikolib-based AST Indexer**
- In-process AST parsing (Python `ast` module), symbol navigation, call graphs, refactoring tools — NOT just grep/ctags
- Language-aware: Python, JS/TS, Go, Rust via tree-sitter bindings (stdlib-compatible subset)

#### 3. **Advanced Context Management** → **Our Invention: Priority-Based Context Engine**
- Automatic summarization of old turns, priority-based retention (user messages > tool results > system), cross-session memory via embeddings (local, stdlib-only)

#### 4. **Rich UI/UX** → **Our Invention: ANSI-Rich Terminal UI (stdlib only)**
- Syntax highlighting via ANSI (pygments-like but pure Python), diff rendering, progress spinners, streaming thinking blocks, markdown rendering to terminal

#### 5. **Built-in Skills Marketplace** → **Our Invention: Standard Skills Format + Registry**
- Unified YAML schema (name, version, entrypoint, schema, hooks, permissions, auto_mode, depends)
- Local + remote index (GitHub releases, HTTP JSON), dependency resolution (semver), signed packages

#### 6. **Enterprise Features** → **Our Invention: Policy Engine + Audit Log**
- SSO-ready auth hooks, audit logging (JSONL), policy enforcement (OPA-like but stdlib), data residency config, admin CLI

#### 7. **IDE Integration** → **Our Invention: LSP Bridge + VS Code Extension (separate)**
- Language Server Protocol bridge for runtime tools, VS Code extension for bidirectional sync (out of scope for core, but API ready)

#### 8. **Multi-modal** → **Our Invention: Image Input via Base64 + LiteLLM Vision Models**
- Screenshot capture (via guard), image analysis, diagram generation (Mermaid → PNG via headless)

#### 9. **Custom Model Fine-tuning** → **Our Invention: Fine-tune Data Export Pipeline**
- Export conversation + tool traces in OpenAI fine-tune format, integrate with LiteLLM fine-tune endpoints

#### 10. **Managed Infrastructure** → **Our Invention: Self-Hosted Control Plane (Single Binary)**
- Single binary (`runtime`) includes: CLI, Guard, Control Plane API, Metrics, Auto-update

#### 11. **Docker Guard for ALL Tools (Codex Model)** → **Our Core Differentiator**
- EVERY tool (bash, file ops, network) goes through guard container via JSON-RPC over stdio
- chroot, cgroups, network policies, read-only rootfs — host stays clean

#### 12. **Hierarchical Agent Orchestration** → **Our Invention: Full Agent Tree**
- Chat → Agent → Subagent → Sub-subagent with message passing, shared context (immutable views), wait-for-idle, delegation patterns, result aggregation

#### 13. **Orchestrator-Driven Dev Workflow Engine** → **Our Invention: Built-in Pipeline**
- Opus→Sonnet→Haiku→Sonnet pipeline as SKILLS (plan, research, implement, test-gen, test-fix, review)
- Context isolation per step, SKILLS.md handoff, Plan-to-HTML with Mermaid UML, Human-in-the-loop approval

#### 14. **Bulgarian-First UI** → **Our Unique Differentiation**
- All user-facing strings in Bulgarian (Cyrillic), errors, help, prompts — internal code stays English

#### 15. **Kikolib Foundation (Zero Deps, Stable Linux Native)**
- stdlib-only, wraps OpenSSL, libsecret, kwallet, systemd, `/proc` — no supply chain risk, fast startup, tiny Docker image

## Development Workflow (Orchestrator-Driven, Context-Efficient)

### Standard Task Execution Flow
1. **Orchestrator (Opus)** creates a **Sonnet** agent, delegates research task
2. **Research** output written to a single `SKILLS.md` file (skills + task combined)
3. **Implementation Pipeline** (each step by a different model tier, context-isolated):
   - **Opus/Sonnet**: Write implementation code
   - **Another LLM**: Write tests against the code
   - **Haiku**: Fix tests (make them pass)
   - **Sonnet**: Review code + tests (correctness, style, completeness)
4. **Orchestrator** receives completion signal — **does not review details**, preserves context
5. **Every agent** operates with minimal context — only what it needs for its step

### Module-Level Planning Flow (for larger features)
1. **Orchestrator (Opus)** writes a **Plan** for the module
2. **Plan rendered as HTML doc** with **UML diagrams in Mermaid/SWG format**
3. **Human reviews** the HTML doc — approves or requests corrections
4. **Once approved**, the plan is frozen and executed via the standard pipeline above

### Principles
- **Context isolation per agent** — no agent sees another agent's full context
- **Single-source SKILLS.md** — research + task definition in one file
- **Model-tier specialization** — Opus (planning/orchestration), Sonnet (implementation/review), Haiku (test fixing)
- **Orchestrator stays high-level** — tracks completion, not implementation details
- **Human-in-the-loop at plan stage** — visual UML review before code starts

## Documentation Artifacts (in `/ocs/`)
- `01-research.html` — Research findings with comparison tables and Mermaid diagrams
- `02-plan.html` — Implementation plan with 5 phases, Gantt chart, milestones, decisions, risks
- `03-architecture.html` — System architecture with component diagrams, data flows, directory structure, config schema, JSON-RPC protocol, security model

## Deliverables
1. Working CLI runtime (`runtime` command)
2. Configuration system for LiteLLM proxy connection
3. Core agent loop with tool execution
4. Hierarchical agent orchestration (chat → agents → subagents)
5. Built-in tools: WebSearch, FileOps, Bash, TaskSpawn, MCP
6. Docker guard container (`Dockerfile`, `docker-compose.yml`, entrypoint)
7. Basic slash commands and help system
8. Documentation for setup and usage
9. **SKILLS.md template** for research+task handoff
10. **Plan-to-HTML generator** with Mermaid/SWG UML diagrams
11. **Skills system**: standard format parser, registry, hooks engine, auto-mode runner
12. **Built-in skills**: code-review, simplify, test-gen, doc-gen, security-review, fewer-permission-prompts
13. **Semantic Code Indexer** (AST-based, stdlib-only)
14. **Priority-Based Context Engine** with cross-session memory
15. **ANSI-Rich Terminal UI** (syntax highlight, diff, markdown, streaming thinking)
16. **Policy Engine + Audit Log** for enterprise features
17. **Fine-tune Data Export Pipeline** (OpenAI format)
18. **Self-Hosted Control Plane** (single binary with API, metrics, auto-update)
19. **Multi-modal Support** (image input, screenshot analysis, diagram generation)
20. **Bulgarian-First UI** (full localization)

## Non-Goals (v1)
- Custom model training/fine-tuning (export pipeline only)
- Built-in model hosting
- GUI/Web interface (CLI only, but API ready for frontend)
- Multi-user server mode (single-user, but sessions isolated)
- External pip dependencies
- Kubernetes/orchestration beyond single Docker container
- VS Code extension (separate project, API stable)