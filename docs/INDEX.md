# Documentation index

All documentation except `README.md`, `RELEASE.md` and `AGENTS.md` (repository root) lives here.

## Reading order for a new contributor

1. `README.md` - what rex is, how to run it, configuration, protocols (JSONL bridge, ACP).
2. `AGENTS.md` - working rules for agents and contributors in this repository.
3. `docs/architecture/role-based-agent-architecture.md` - the design; read the status block at the top first,
   then section 10 for what exists.
4. `docs/plans/2026-11-23-rex-gaps.md` - design record for permissions, hooks, edit, MCP, sessions, webfetch,
   context warning; open work is listed at its end.
5. `docs/plans/2026-11-23-gateway-tool-calling.md` - how the Claude CLI gateway does tool calling.
6. `RELEASE.md` - how a release is built (Debian package, Windows zip).
7. `docs/history/` - only when you need the background; nothing there is authoritative.

## All documents

| Path | What | Status |
|------|------|--------|
| `README.md` | Overview, usage, configuration | current |
| `RELEASE.md` | Release and packaging procedure | current |
| `AGENTS.md` | Rules for agents working on rex | current |
| `docs/architecture/role-based-agent-architecture.md` | Original v1.0 design with a current status block and implementation table | current (design text partly superseded, see its status block) |
| `docs/architecture/gemini-review-procedure.md` | How to run `scripts/review_architecture.py` (LLM review of the architecture doc) | current |
| `docs/plans/2026-11-23-rex-gaps.md` | Plan and design record: permissions, hooks, edit, MCP, sessions, webfetch, context warning | current (mostly implemented, sessions wiring open) |
| `docs/plans/2026-11-23-gateway-tool-calling.md` | Plan and status notes: tool calling through `scripts/claude_cli_gateway.py` | current (implemented, live run pending) |
| `docs/guard-docker.md` | Run the guard in Docker on Linux: build, docker group, uid, network | current |
| `docs/history/original-task.md` | The original task statement | historical |
| `docs/history/model-classes.md` | 30-class model taxonomy design, never built | historical |
| `docs/history/gemini-review.md` | One-off review of an early draft of the architecture doc | historical |
| `docs/history/research/aider-deep-research.md` | Aider analysis (Bulgarian) | historical |
| `docs/history/ocs/01-research.html` | Early research note on open-source coding agents | historical |
| `docs/history/ocs/02-plan.html` | Early implementation plan (Bulgarian) | historical |
| `docs/history/ocs/03-architecture.html` | Early architecture note with diagrams (Bulgarian) | historical |
| `docs/history/ocs/architecture-single-file.html` | HTML rendering of an early draft of the architecture doc | historical |
| `docs/history/ocs/*-diagram*.svg` | Diagrams used by the three HTML notes above | historical |
