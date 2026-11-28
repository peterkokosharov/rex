# lib_runtime_rex - positioning and first milestones (independent review, 2026-11-28)

> Written by an independent reviewer (claude-opus-5-5) who saw only the idea: no code, no files, no web search.
> Market knowledge runs up to mid-2026 and is unverified at the edges. Treat market claims as informed opinion,
> not as researched fact.

## Owner's architecture (context)

The library `lib_runtime_rex` sits at the centre. Everything else is a host or a module around it.

| Part | Role |
|---|---|
| `lib_runtime_rex` | The library at the centre of the design. |
| `rex/core` | Thin headless process. One-shot use: `rex -i "..." -m <model> --port 4000`. Also acts as a proxy. Embedded as a process in Layka, IntelliJ and VS Code. |
| `rex/cli` | py-vision TUI, in the style of Claude CLI and Gemini CLI. |
| `rex/ui` | GUI (Sisi). |
| `rex/web` | Web server (Lassie). |
| separate modules | context, MCP, bridge. |

## 1. Potential

**As a product sold to model vendors: weak.**

- Vendors ship their own runtimes: Claude Agent SDK, OpenAI Agents SDK, Google ADK, NVIDIA NeMo Agent toolkit.
- Vendors want lock-in. Few of them would buy a one-person library.

**As a general agent framework: crowded.**

- General frameworks: LangGraph, Pydantic AI, smolagents, LlamaIndex, CrewAI, Agno.
- Coding hosts: aider, Goose, opencode, Codex CLI, Gemini CLI, Claude Code.

**What is different is the combination:**

| Property | Why it matters |
|---|---|
| Pure stdlib, zero dependencies | Matters in Odoo virtualenvs, air-gapped and regulated sites, security appliances, Python embedded in C++ apps, IDE-bundled interpreters, and corporate desktops with pip blocked. Nobody owns this niche. |
| Embeddable in-process | "The host owns the loop, rex is a component." |
| Security model as a first-class feature | allow / ask / deny gate, sandbox, audit log. |
| Readable and auditable | Under about 10k lines, reviewable in a day. |

**Verdict.** Real potential as "the dependency-free, auditable agent core you can drop into any Python host".
No potential as "a better Claude Code" or as a vendor product.

## 2. Core promise, scope and gaps

**Core promise:**

> A zero-dependency, auditable Python agent loop you embed in your own app, where every tool call goes through a
> permission gate you control and every step is logged.

### Cut or move out of the core

- **UIs and hosts.** Qt UI, web server, TUI CLI and IDE plug-ins are separate hosts. Build them one at a time, and
  only when an embedding needs one.
- **Local gateway.** Optional, not required. Provider adapters are stdlib HTTP clients:
  - OpenAI-compatible, which covers NVIDIA NIM, vLLM, Ollama and llama.cpp;
  - native Anthropic;
  - native Gemini.
- **Transport.** No WebSocket in core. Start with stdio JSON-RPC, then HTTP + SSE.
- **Sandbox.** The container sandbox is a pluggable Executor. Ship a subprocess runner with a restricted directory
  and a timeout, plus a Docker/podman adapter.
- **Memory.** Long-term memory and RAG belong to the host. The library offers a hook, not a store.

### Missing, in priority order

1. Structured trace and audit log (JSONL): model call, tool call, decision, tokens, cost.
2. Cost and budget limits per session: tokens, money, steps, wall time, with a hard stop.
3. Resumable sessions, including asynchronous human approval hours later (key for ERP).
4. Structured output: JSON-schema validated, with retry.
5. Evaluation harness: replay recorded sessions against a new model or prompt and assert on tool calls.
6. Cancellation and streaming as first-class features.
7. Minimal multi-agent support: "agent as a tool".

## 3. Modules and public API

### Modules

| Module | Notes |
|---|---|
| providers | Model adapters. |
| loop | Knows only interfaces. |
| context | |
| tools | |
| policy | Gate plus audit. |
| mcp | Just a tool source. |
| session | Store interface, default SQLite. |
| trace | |
| bridge | Outside core, depends only on the public API. |

**Rule:** the loop knows only interfaces, and nothing imports bridge.

### Public API (eight objects)

| Object | Shape |
|---|---|
| `Agent` | `Agent(model, tools, policy, instructions, limits)`, with `.run(input) -> Result` and `.stream(input) -> events`. |
| `Model` | Protocol `complete(messages, tools)`, plus `connect("nemotron-ultra")`. |
| `Tool` | `Tool` and the `@tool` decorator. |
| `Policy` | Rules plus an ask callback that may return "pending". |
| `Session` | save/load via a `Store` protocol. |
| `Limits` | Per-session budgets. |
| `Event` | One dataclass family. |
| `MCPServer` | |

Guiding rule: if you cannot explain a new object in one line, it does not go in.

## 4. Risks

| Risk | Mitigation |
|---|---|
| **Provider drift.** | Support only OpenAI-compatible, Anthropic and Gemini. Use recorded-fixture contract tests. Other providers become community adapters. |
| **Stdlib pain.** Sync code and threads; no exact token counts. | Estimate token counts and correct them from provider usage. Never promise exact counts. |
| **Scope creep** across 4 hosts and 5 apps. Most likely cause of death. | Library plus exactly one real embedding until v1. No host without a user. |
| **Adoption:** "why not Pydantic AI or LangGraph?" | Market the niche: 0 dependencies, works inside Odoo, core is X lines, every action audited. Ship one impressive demo. |
| **Untrue security claims.** | Deny by default. Treat tool output as untrusted. Keep the gate outside the model's reach. Write a threat model document early. Get external review before using the word "secure". |

## 5. Milestones

**M1 - Odoo copilot, read-only.**

- The addon imports the library in-process; no pip in the Odoo environment.
- ORM search and read tools. Every write is denied.
- Real questions, for example "unpaid invoices over 30 days for customer X".
- Trace log and cost limits.
- Works with one cloud provider and one local provider.

**M2 - gated writes and resumable approval.**

- The agent proposes a draft invoice or a stock update.
- Policy is "ask"; the session is saved.
- The user approves later in the Odoo UI; the session resumes.
- Audit log view; structured proposals.

**M3 - a second, very different host via the bridge.**

- Headless stdio JSON-RPC from an editor or the C++ TUI under the same Policy, or a surveillance/chatbot host.
- Replay evaluation harness built from M1-M2 sessions, run across models.
- Then v1. Only after that consider CLI, Qt and web hosts.

## Bottom line

Worth building if it stays small, auditable, zero-dependency and embeddable, with a real security story, and if it
is proven inside Odoo first.

---

Sources: Independent review by claude-opus-5-5, 2026-11-28 (no file access); owner's architecture notes, 2026-11-28
