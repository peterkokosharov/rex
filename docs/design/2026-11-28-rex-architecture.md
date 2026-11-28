<!--
Author: Peter Kokosharov <peter.kokosharov@gmail.com>
AI models: claude-opus-5-5, claude-sonnet-5-5
Licensed under MIT License
-->

# rex architecture: one library, thin apps (design, plan only)

Date: 2026-11-28. Status: **design for review; no code has moved.** Owner's target architecture (2026-11-28) is
authoritative; this document maps today's code onto it and plans the way there in small green steps.

## Owner decisions 2026-11-28 (override the naming below)

Made by the owner on 2026-11-28 after reading this design. The command names below are final; the rest of the
document is adjusted to them.

**Final naming (one consistent family)**

| Name | What it is |
|---|---|
| `ai-rex` | the **CORE / engine command**: fast, thin shell client for unix pipes (`cmd1 \| ai-rex -i "..." \| grep xx`, also `ai-rex -i "how are you" -m nemotron-ultra --port 4000`), server/proxy mode (`--port`, `--jsonl`, `--acp`), embedded as a process in Layka, IntelliJ and VS Code. Configuration from **environment variables only**; it never writes or edits configuration files. Python: thin app `ai_runtime_rex.core` over `lib_runtime_rex` |
| `rex` | the **CLI app**: py_tvision TUI to work with the whole rex system in a terminal (IDE terminals too): panels and dialogs for all settings, including the full kiko control (what the kiko tray does today; the tray becomes optional), status line with context % and a kiko health indicator (green = ok, orange = degraded, red = down). Models via kiko or LiteLLM. Python: `ai_runtime_rex.cli` (formerly Strelka) |
| `rex-desktop` | the **GUI** (`ai_runtime_rex.ui`); reserved, empty for now. Sisi stays a separate C++/Qt repo |
| `rex-web` | the **web server** (Lassie, `ai_runtime_rex.web`) |

Rationale: the product face (what a person types and uses) gets the short brand name, like `claude` or `gemini`; the
engine gets a stable, script-safe name that pipelines and embedding hosts can depend on; the four names read as one
family.

**kiko bundled, one folder.** kiko (gateway + kikolib) is BUNDLED inside `rex` (the TUI app). The whole app installs
into ONE folder; deleting the folder uninstalls it completely (code, settings, sessions, logs). Settings written by
`rex` live in that folder, not in `~/.config` or AppData. `ai-rex` reads the environment only, so `rex` passes its
settings to the `ai-rex` processes it starts as environment variables.

**Decided answers to the open questions (section 8.2)**

| Question | Decision |
|---|---|
| Strelka home | becomes the `rex` CLI inside the rex repo (`ai_runtime_rex.cli`); py_tvision copied in unchanged |
| Lassie home | web server `rex-web` inside the rex repo (`ai_runtime_rex.web`) |
| Sisi / `ui` package | Sisi stays a separate C++/Qt repo; `ai_runtime_rex.ui` (`rex-desktop`) stays empty for now |
| Orchestrator and `REX` facade | go to `ai_runtime_rex.extras` |
| Layka | stays private and separate (process boundary, section 5) |
| Debian name clash | Debian's Perl `rex` package also installs `/usr/bin/rex`. Our Debian package for the TUI is named e.g. `rex-tui`; the command conflict is handled at packaging time (alternatives or diversion). The portable one-folder install is unaffected |
| First real embedding | decided, see "Embedding order" below (the independent review in `docs/design/2026-11-28-rex-positioning.md` had recommended the Odoo copilot first; the owner chose another order) |

**Embedding order (owner, 2026-11-28).** 1) Layka runs `ai-rex` as a shell/child process (stdio JSONL bridge);
2) the IntelliJ plug-in, also through the `ai-rex` process (used for testing); 3) the Odoo copilot uses
`lib_runtime_rex` directly, in-process, as a library: "it makes no sense to call a shell process from a Python
script". So the stdio bridges (JSONL, ACP) come first in the migration, the HTTP bridge later, and the in-process
public API (`Agent` etc., section 3) must be solid by the Odoo step.

Consequence: the engine commands of sections 3-8 keep the name `ai-rex` as written; the TUI (formerly "Strelka") is
`rex`. The Windows zip and the Debian packages ship both commands.

---

Target in one picture:

```
                 embedding apps (Odoo copilot, surveillance, IDE plug-ins, your library ...)
                                     |  import (in-process)
   +-----------------------------------------------------------------------------------+
   |  lib_runtime_rex  (the LIBRARY; stdlib + vendored kikolib only; imports no UI)     |
   |   api  |  agent  |  context  |  llm  |  tools  |  mcp  |  permissions  |  sandbox   |
   |   sessions  |  hooks  |  skills  |  config  |  security  |  bridge (B)  |  native    |
   +-----------------------------------------------------------------------------------+
        ^ import            ^ import            ^ import              ^ import
   ai_runtime_rex.core  ai_runtime_rex.cli  ai_runtime_rex.web   ai_runtime_rex.ui
   (thin: ai-rex        (py_tvision TUI)    (Lassie: HTTP +       (GUI slot; Sisi
    one-shot, REPL,                          plain JS)             stays C++, see 5)
    --jsonl/--acp/--port)
        ^  process boundary: bridge B (JSONL / ACP over stdio, HTTP+SSE/WebSocket)
   Layka (C++, private repo), Sisi (C++/Qt), IntelliJ, VS Code, Zed, any ACP client
```

---

## 1. Inventory: today's modules -> target modules

Today the engine lives in `ai_runtime_rex/core/` (13 238 lines incl. `runtime/embed.py`). `runtimelib/` and
`rex_runtime_lib/` are alias shims to it; the command-line app is still the old top-level `runtime/` package
(`runtime/__main__.py` 659 lines, `runtime/commands.py` 125); `orchestrator/` (1 026 lines) and `guard/entrypoint.py`
(runs inside the Docker guard) are separate top-level folders; `orchestrator/` is not even in the wheel
(`pyproject.toml` `include` lists only `ai_runtime_rex`, `runtime`, `runtimelib`, `kikolib`).

### 1.1 File -> target module

Target module names are sub-packages of `lib_runtime_rex` unless the column says `app:`.

| Current file (under `ai_runtime_rex/`) | Target | Note |
|---|---|---|
| `core/__init__.py` | `lib_runtime_rex/__init__.py` (small, lazy) | today a 185-line re-export of everything incl. CLI classes |
| `core/agent/loop.py` | `agent/loop.py` | ReAct loop; loses tool wiring, signals, atexit, prints (see 1.2) |
| `core/agent/state.py` | `agent/state.py` (+ `sessions/checkpoint.py` for `CheckpointManager`) | one `AgentState` only |
| `core/agent/callbacks.py` | `agent/events.py` | merges with `AgentEvent` from `runtime/embed.py` |
| `core/agent/context.py` | **A** `context/compaction.py`, `context/tokens.py`, `context/window.py` | token estimate, window lookup, compaction |
| `core/agent/instructions.py` | `context/instructions.py` | AGENTS.md loading = what goes into the context |
| `core/agent/gate.py` | `permissions/gate.py` | the permission gate + `Asker` protocol = the permission callback |
| `core/permissions/rules.py`, `base.py` | `permissions/rules.py`, `permissions/base.py` | the live rule engine |
| `core/permissions/policy.py`, `executor.py` | `permissions/legacy.py` (deprecated) | second, older permission system, used only by `REX` |
| `core/hooks/__init__.py`, `commands.py` | `hooks/` | |
| `core/llm/client.py`, `messages.py`, `models.py` | `llm/openai_http.py`, `llm/messages.py`, `llm/models.py` | `LiteLLMClient` is really a stdlib OpenAI-compatible HTTP client |
| `runtime/embed.py` (`GatewayLLM`) | `llm/gateway.py` | any `chat(body)` callable, e.g. kiko in-process |
| `runtime/embed.py` (`Session`, `AgentEvent`, `serve_acp`) | `api.py` (+ `bridge/acp.py`) | seed of the public API |
| `core/mcp/__init__.py` | **MCP** `mcp/client.py`, `mcp/transport.py` | |
| `core/tools/mcp_tool.py` | `mcp/tools.py` | MCP tools as agent tools |
| `core/tools/base.py`, `registry.py`, `executor.py` | `tools/base.py`, `tools/registry.py`, `tools/executor.py` | |
| `core/tools/fileops.py`, `edit.py`, `bash.py`, `git.py`, `diagram.py` | `tools/builtin/` | the coding tool set (opt-in for embedders) |
| `core/tools/webfetch.py`, `websearch.py`, `engines/*` | `tools/builtin/web/` | |
| `core/tools/skills.py`, `core/skills/__init__.py`, `core/trust.py` | `skills/` | `trust.py` = workspace trust, used by skills and instructions |
| `core/skills/cli.py` | `app: ai_runtime_rex.core` (`ai-rex skills` sub-command) | |
| `core/tools/taskspawn.py` | `tools/builtin/task.py` (hidden today) | duplicate of `orchestrator/taskspawn.py` |
| `core/guard/__init__.py` (`GuardClient`) | `sandbox/guard.py` | Docker guard client |
| `core/guard/sandbox.py` | `sandbox/worktree.py` | worktrees for sub-agents (used only by `REX`) |
| `guard/entrypoint.py` (repo root) | `sandbox/container/entrypoint.py` (package data) | runs inside the container |
| `core/session/__init__.py` | `sessions/` (`SessionManager` history, `SessionStore` resumable JSONL) | `SessionStore` exists but is not wired |
| `core/security/sanitizer.py` | `security/sanitizer.py` | **unused** by the runtime today |
| `core/config/*` | `config/` | `Config`, sources, schema, paths; keys via kikolib |
| `core/logging/__init__.py` | `logging/` (rename `log/` to avoid shadowing stdlib in IDEs) | |
| `core/native/*` | `native/` | optional C++ helper via ctypes, Python fallback |
| `core/procutil.py` | `util/procutil.py` | |
| `core/cli/jsonl_bridge.py` | **B** `bridge/jsonl.py` | the JSONL wire protocol of Layka, Sisi, Lassie |
| `core/acp/server.py` | **B** `bridge/acp.py` | ACP over stdio for editors |
| (new) | **B** `bridge/http.py` | HTTP + SSE + WebSocket, on `kikolib.server` / `websocket` / `sse` |
| `core/cli/ansi.py`, `render.py`, `readline.py`, `parser.py`, `registry.py`, `shell.py` | `app: ai_runtime_rex.core` (plain REPL) | UI code, must leave the library |
| `core/rex.py` (`REX` facade) | `app: ai_runtime_rex.extras` or removed | "entry point for orchestrators"; superseded by `api.Agent` + design rev. 3 (sub-agents as processes) |
| `runtime/__main__.py`, `runtime/commands.py` (repo root) | `app: ai_runtime_rex.core.main`, `.repl`, `.commands` | `runtime` stays a shim: front-ends run `-m runtime` |
| `orchestrator/*` (repo root) | `app: ai_runtime_rex.extras.orchestrator` (owner decision pending since RENAME-MAP) | |
| `vendor/kikolib/` (repo root) | stays vendored, unmodified, pinned 0.2.x | single source = kiko repo |

### 1.2 Coupling problems found (file:line, all under `ai_runtime_rex/` unless stated)

These are what make the code a program rather than a library. Each one has a migration step in section 7.

| # | Where | Problem | Why it matters for embedding |
|---|---|---|---|
| C1 | `core/__init__.py:42` | the engine package eagerly imports `cli` (readline, registry, shell, jsonl_bridge) | the library imports UI; verified: `import ai_runtime_rex.core` loads 6 `core.cli.*` modules, ~0.42 s total import |
| C2 | `core/__init__.py:22`, `:36` | import side effects: appends `vendor/` to `sys.path`, pins the kikolib HTTP backend process-wide | an embedding app (Odoo) gets its `sys.path` and kikolib factory changed by `import` |
| C3 | `core/agent/loop.py:172` | `AgentLoop.__init__` installs a SIGINT handler (depth 0, main thread) | a host app's own Ctrl+C / signal handling is replaced by constructing an agent |
| C4 | `core/agent/loop.py:231` | `atexit.register(self.close_mcp)` | lifetime belongs to the host; needs `close()` / context manager instead |
| C5 | `core/agent/loop.py:306`, `:510`; `core/agent/instructions.py:137-139`; `core/hooks/__init__.py:109`; `core/tools/mcp_tool.py:108`, `:115`; `core/guard/__init__.py:84`; `core/trust.py:35` | `print(..., file=sys.stderr)` in library code; `loop.py:306` prints a hard-coded Bulgarian "[Прекъснато]" | a daemon / Odoo worker gets stray stderr text; must be `logging` + a `notice` event |
| C6 | `core/agent/loop.py:205-214`, `:218`, `:226` | the loop hard-wires the coding tool set, skill discovery and MCP start-up | an embedder must be able to say "only my tools"; today `tools=[]` falls into `_init_default_tools` (the `if tools:` test at `loop.py:134` treats an empty list as "defaults") |
| C7 | `Path.cwd()` in `core/agent/loop.py:218,226`, `core/agent/gate.py:145`, `core/agent/instructions.py:89`, `core/tools/fileops.py:130`, `edit.py:271`, `bash.py:114`, `git.py:120`, `diagram.py:85`, `skills.py:31`, `core/guard/__init__.py:37,63`, `core/session/__init__.py:244,314`, `core/rex.py:89` | the workspace is the process cwd | one process cannot host two sessions in two workspaces; this is why ACP spawns one child process per session |
| C8 | `get_config()` global in `core/agent/loop.py:743`, `core/llm/client.py:406`, `core/session/__init__.py:147,326`, `core/guard/__init__.py:372`, `core/acp/server.py:587`, `runtime/embed.py:104` | hidden global config read from `~/.config/ai-rex` | an embedder that passes nothing still gets the user's personal config file and `.env` |
| C9 | `core/llm/client.py:35-137` vs `core/llm/messages.py:26-150` | `ChatMessage`, `ToolCall`, `ChatCompletionChunk`, `ChatCompletion` defined twice; verified `client.ChatMessage is messages.ChatMessage` is **False** | `isinstance` across the two fails; `runtime/embed.py:21` imports the `client` copies, the loop the `messages` ones |
| C10 | `core/agent/loop.py:67` vs `core/agent/state.py:21` | two different `AgentState` classes (loop's has `turn_tokens_*`, `turn_estimated`; state's has `session_id`, `metadata`, `to_dict`); `agent/__init__.py:11-12` imports both, the second wins | hooks receive a different `AgentState` type than the loop uses |
| C11 | `core/hooks/__init__.py:16-17` | hooks import `tools.base` and `agent.state`; `agent/loop.py:35` imports hooks | layering upside down (hooks should be below agent) |
| C12 | `core/agent/callbacks.py:77` | callbacks import `ToolResult` from `loop` (bottom-of-file import) | event types depend on the loop module |
| C13 | `core/agent/gate.py:33-42` | the gate (agent layer) imports `hooks.commands`, `permissions.rules`, `tools.base` | fine as a dependency, but the gate belongs in `permissions`, not `agent` |
| C14 | `core/permissions/policy.py`, `executor.py:13` vs `core/permissions/rules.py` + `core/agent/gate.py` | two permission systems; the old one is used only by `core/rex.py:22` | one permission model for the public API |
| C15 | `core/tools/bash.py:17-18`, `fileops.py:16-17`, `git.py:15-16`, `edit.py:22,274` | every tool imports `guard` and `config` and decides guard-vs-local itself from `cli.unsafe_local_tools` | the execution backend (guard / local / host-provided) should be injected once |
| C16 | `core/acp/server.py:59`, `:259`, `:559` | ACP finds the source tree by `parents[3]`, spawns `python -P -m runtime --jsonl`, imports from `cli` | breaks the moment files move; must resolve the child command in one place |
| C17 | `core/guard/sandbox.py:133` | `import redis.asyncio` (lazy) | third-party import in production code; stdlib-only rule |
| C18 | `runtime/embed.py:19-21`, `:222` | the new embed facade imports through the old `runtimelib` shim | shim cannot be removed until fixed |
| C19 | `orchestrator/taskspawn.py:26-29` (repo root), `:40-43` | fallback `from ..runtimelib...` (relative import beyond the top package) | dead path; orchestrator not packaged |
| C20 | `core/security/sanitizer.py` | not used by anything in the runtime | keep as an optional helper for embedders or drop; do not pretend it protects the loop |
| C21 | versions: `pyproject.toml` 0.3.0, `ai_runtime_rex/__init__.py` 0.1.0, `rex_runtime_lib` 0.1.0, `runtimelib` 0.3.0 | four version strings | one source of truth needed before two wheels |
| C22 | front-ends `lassie/lassie/rexproc.py:82`, `sisi/src/rexbridge.cpp:42`, `layka/src/main.cpp:995` | all spawn `python -P -m runtime ... --jsonl` with `PYTHONPATH=<rex checkout>` | the module name `runtime` is a public contract today; keep a `runtime` shim until all three move to `ai-rex --jsonl` |

What is already good and stays: the `Asker` protocol (`core/agent/gate.py:65`) is exactly the permission callback
an embedder needs; `GatewayLLM` (`runtime/embed.py:27`) is a clean model seam; `context.py` is pure functions;
`SessionStore` is a clean JSONL store; the JSONL bridge protocol is documented in its module docstring and used by
three front-ends; `process-globals.md` already lists every process-wide global with a decision.

---

## 2. Target layout of the monorepo and dependency rules

### 2.1 Names (decided)

- **Library:** top-level package `lib_runtime_rex` (the owner's name), distribution `lib-runtime-rex`. Top-level so
  that an embedder writes `import lib_runtime_rex` and installs one wheel without any app code.
- **Apps:** stay in the existing umbrella `ai_runtime_rex` (the branch's investment is kept): `ai_runtime_rex.core`,
  `.cli`, `.web`, `.ui`, `.extras`; distribution `ai-rex`, commands `ai-rex` (core/engine) and `rex` (TUI) - see "Owner
  decisions 2026-11-28".
- The name clash is real: today `ai_runtime_rex.core` *is* the engine; in the target it is the thin app. The plan
  (section 7) moves the engine out first, leaves alias shims for the old engine paths, and only then puts app
  modules under `ai_runtime_rex.core`. App module names (`main`, `repl`, `oneshot`, `serve`, `commands`) never
  collide with engine sub-package names (`agent`, `llm`, `tools`, ...); `core.cli` is emptied before reuse.
- Rejected: a third Python namespace (`ai_rex`, `rex`); `ai_rex` would be the fourth Python name for the same
  project. (Owner decision 2026-11-28: the Python/distribution names stay `ai_runtime_rex` / `ai-rex`; the *commands*
  are `ai-rex` for the engine and `rex` for the TUI. The Debian clash on `rex` is handled at packaging time, see the
  decisions section.)

### 2.2 Layout

```
rex/                                  (repo root, public, MIT)
  lib/
    pyproject.toml                    -> wheel lib-runtime-rex (lib_runtime_rex + kikolib)
    lib_runtime_rex/
      __init__.py                     public API re-exports only (lazy, no side effects)
      api.py                          Agent, Event, ContextPolicy, function_tool, openai_endpoint, gateway_llm, MCPServer
      agent/        loop.py state.py events.py
      context/      tokens.py window.py compaction.py instructions.py          (module A)
      llm/          messages.py openai_http.py gateway.py models.py
      tools/        base.py registry.py executor.py builtin/{fileops,edit,bash,git,diagram,task,web/}
      mcp/          client.py transport.py tools.py                           (module MCP)
      permissions/  gate.py rules.py base.py legacy.py
      sandbox/      guard.py worktree.py local.py container/entrypoint.py
      sessions/     history.py store.py checkpoint.py
      hooks/        engine.py commands.py
      skills/       discover.py tools.py trust.py builtin/...
      bridge/       protocol.py jsonl.py acp.py http.py openai_facade.py      (module B)
      config/       config.py sources.py schema.py paths.py keys.py
      security/     sanitizer.py
      log/          (today core/logging)
      native/       (ctypes helper + fallback)
      util/         procutil.py
      _vendor_path.py                 the one place that makes vendored kikolib importable
  vendor/kikolib/                     unmodified, pinned 0.2.x (copied into the lib wheel)
  ai_runtime_rex/                     apps (wheel ai-rex, depends on lib-runtime-rex == same version)
    core/   main.py oneshot.py repl.py serve.py commands.py render.py         (thin)
    cli/    the py_tvision TUI (Strelka)                                      (thicker)
    web/    Lassie                                                             (web)
    ui/     slot for a Python GUI; empty until decided (see 5)
    extras/ orchestrator, REX facade (deprecated)
  runtime/  runtimelib/  rex_runtime_lib/   back-compat shims (removed in the last step)
  native/  packaging/  docs/  tests/  scripts/
  pyproject.toml                      -> wheel ai-rex (apps; entry points)
```

`tests/` stays flat and unchanged; a `conftest.py` puts `lib/` and the repo root on `sys.path` so the suite runs from
a checkout without installing either wheel (as today).

### 2.3 Dependency rules (enforced by a test, not by goodwill)

1. `lib_runtime_rex` imports only the stdlib, `kikolib`, and itself. **Never** `ai_runtime_rex`, `runtime`,
   `py_tvision`, `readline`, `tkinter`, or anything third-party.
2. Inside the library, layers import downward only:
   `util, log, native` -> `config, security` -> `llm, context` -> `hooks, sessions, sandbox` -> `tools, mcp,
   skills, permissions` -> `agent` -> `api` -> `bridge`. (`bridge` sits on `api` so every transport drives the same
   public object an embedder uses.)
3. No module-level side effects in the library: no `sys.path` edits outside `_vendor_path.py` (and that one only
   when kikolib is not importable already), no signal handlers, no `atexit`, no `print`, no reading
   `~/.config/ai-rex` unless the caller asked (`Config.from_user_files()`), no `os.chdir`, no `Path.cwd()` except
   as a default resolved in `api.py`.
4. Apps may import `lib_runtime_rex` (public API first; internal modules allowed for `core` only, which is the
   reference app). Apps never import each other, except `cli` and `web` may import `core.commands` (the shared
   slash-command table).
5. Out-of-process clients (Layka, Sisi, IDEs) depend on nothing but the bridge protocol document and the `ai-rex`
   command.

Enforcement: `tests/test_layering.py` parses every `.py` with `ast` and checks rules 1-3 (forbidden imports,
upward imports, `signal.signal`/`atexit.register`/`print(` in the library). It starts with an explicit allow-list
of today's violations (C1-C18) so it is green on day one; every migration step deletes lines from the allow-list.

---

## 3. Public API of the library

Small on purpose: an embedder needs about ten names. Everything else is reachable but not promised.

```python
from lib_runtime_rex import (
    Agent,            # one conversation: send / stream / interrupt / reset / close; context manager
    Event,            # streamed event: kind in token|tool_start|tool_end|permission|notice|context|final|error
    ContextPolicy,    # window, compact_at, keep_recent, warn_percent, compaction strategy
    PermissionRequest, ALLOW_ONCE, ALLOW_ALWAYS, DENY,   # the callback contract (today's Asker)
    Tool, function_tool,                                  # register tools: subclass, or wrap a typed function
    builtin_tools,    # the coding tool set (fileops, edit, bash, git, webfetch, websearch, diagram), opt-in
    MCPServer,        # name, command, args, env, cwd
    SessionStore,     # resumable JSONL sessions (save / list / load)
    openai_endpoint, gateway_llm,                         # model backends: HTTP endpoint or any chat(body) callable
    Config,           # optional: load the ai-rex config files when an app wants the user's setup
)
```

`Agent` constructor (keyword-only; every argument optional except `llm`):

| Argument | Meaning | Default |
|---|---|---|
| `llm` | `openai_endpoint(base_url, api_key=None, model=...)` or `gateway_llm(chat_callable, model)` or any object with `chat_completion(...)` | required |
| `instructions` | system prompt text from the host | `""` |
| `tools` | list of `Tool`; an empty list means **no tools** (fixes C6) | `[]` |
| `workspace` | `Path` for file tools, skills, AGENTS.md; `None` = no workspace | `None` |
| `load_agents_md`, `skills` | read AGENTS.md / discover skills in `workspace` | `False` |
| `context` | `ContextPolicy(...)` | window from model table, compact at 0.9 |
| `permission` | `Callable[[PermissionRequest], str]` returning `ALLOW_ONCE` / `ALLOW_ALWAYS` / `DENY` | deny every "ask" |
| `rules` | permission rule strings (today's `permissions.allow/deny/ask`) | none |
| `mcp_servers` | list of `MCPServer`; started lazily, stopped by `close()` | `[]` |
| `session` | `SessionStore` (or `SessionStore.resume(id)`) | in-memory only |
| `sandbox` | `"local"`, `"guard"` (Docker), or an object with `run(cmd, cwd, timeout)` | `"local"` |
| `hooks` | a `HookEngine` | private empty engine |
| `on_event` | callback for every `Event` (alternative to `stream`) | none |

Methods: `send(text) -> str`, `stream(text) -> Iterator[Event]`, `interrupt()` (thread-safe), `reset()`,
`context_usage() -> dict`, `close()`; `with Agent(...) as a:` closes MCP servers and sessions.

Rules behind the API: the library reads no files and no environment unless an argument says so; it never prints
(all notices are `Event("notice")` and `logging`); it never touches signals; one `Agent` = one workspace, so two
agents in two workspaces can live in one process once C7 is fixed.

### 3.1 Embedding example: an Odoo copilot (not a chat app, no files, no shell)

```python
from lib_runtime_rex import Agent, ContextPolicy, function_tool, openai_endpoint, ALLOW_ONCE, DENY

def make_copilot(env):                          # env: the Odoo environment of the current user
    def overdue_customers(name: str) -> list:
        """Customers whose name contains `name` and who have overdue invoices."""
        partners = env["res.partner"].search([("name", "ilike", name)], limit=10)
        return [{"id": p.id, "name": p.name, "due": p.total_due} for p in partners if p.total_due > 0]

    return Agent(
        llm=openai_endpoint("http://127.0.0.1:4000/v1", model="nemotron-ultra"),
        instructions="You help the sales team. Read-only. Answer in the user's language.",
        tools=[function_tool(overdue_customers)],   # only this tool: no files, no shell, no web
        context=ContextPolicy(window=32_000, compact_at=0.8),
        permission=lambda req: ALLOW_ONCE if req.tool == "overdue_customers" else DENY,
    )

with make_copilot(env) as copilot:
    print(copilot.send("Which Kraft customers still owe us money?"))
```

`function_tool` builds the JSON schema from the signature and the docstring (stdlib `inspect` + `typing`; str, int,
float, bool, list, dict, `Optional`, `Literal`); anything else must use a `Tool` subclass with an explicit schema.

---

## 4. Bridge protocol B (module `lib_runtime_rex.bridge`)

One internal event model (`Event` from section 3), three transports. Every transport drives `api.Agent`, so a
feature added to the library reaches all clients at once. All transports are served by the **`ai-rex`** command
(owner decision 2026-11-28; this is the original naming, now final: `ai-rex --jsonl`, `ai-rex --acp`,
`ai-rex --port`, `ai-rex -i`). `ai-rex` takes its settings from environment variables only.

### 4.1 Messages (shared by all transports)

Client -> agent: `chat{text, attachments?}`, `stop`, `reset`, `model{model}`, `command{name, value}`,
`permission_reply{id, decision}`, `bye`. Agent -> client: `ready{model, protocol, instructions?}`, `token`,
`tool_start{name, args}`, `tool_end{ok, error, diff?}`, `permission{id, tool, kind, summary, detail, args, rule,
reason}`, `context{used, limit, percent, model, source, warning?}`, `notice{severity, text}`, `final{text}`,
`error{message}`, `reset_ok`, `model_ok`, `bye`. This is today's JSONL wire format (module docstring of
`core/cli/jsonl_bridge.py`) plus two additions: `protocol` (integer, starts at 1) in `ready`, and `notice`
(replaces stderr text, C5). Old clients ignore unknown fields and types, so both additions are compatible.

### 4.2 Transport 1: JSONL over stdio (`ai-rex --jsonl`)

For Layka, Sisi, Lassie (today) and any process-embedding host. Unchanged wire format; one process = one
conversation; the process cwd is the workspace. Stays the simplest contract and the one Layka depends on.

### 4.3 Transport 2: ACP, JSON-RPC 2.0 over stdio (`ai-rex --acp`)

For IntelliJ (JetBrains AI Assistant speaks ACP), VS Code (through an ACP client extension) and Zed. Today
`core/acp/server.py` maps ACP onto JSONL children (one child process per session). What editors need, with today's
state (ACP details from memory of the spec; check each against the current ACP spec before implementing):

| Editor need | ACP | Today | Plan |
|---|---|---|---|
| handshake, capabilities | `initialize` | yes | keep |
| new session in the project dir | `session/new{cwd, mcpServers}` | yes, `mcpServers` ignored | pass `mcpServers` to `Agent(mcp_servers=...)` |
| prompt with editor context | `session/prompt` with text / resource / resource_link blocks | text + embedded resource | add `resource_link` (read through the client, below) |
| streaming answer and tool cards | `session/update` agent_message_chunk, tool_call, tool_call_update (diff, locations) | chunks + tool cards, no diff | send `diff` from the edit tool, file `locations` |
| ask the user before a write | `session/request_permission` | **missing**: every question answered deny (`server.py` docstring) | map `permission` events to it; first priority |
| cancel | `session/cancel` | yes (`stop`) | keep |
| reopen a past chat | `session/load` + `loadSession` capability | no | wire `SessionStore` (already written, not connected) |
| edit unsaved editor buffers | client `fs/read_text_file`, `fs/write_text_file` | no | a `ClientFiles` file backend for the file tools, used when the client advertises it |
| run commands in the editor's terminal | client `terminal/*` | no | optional `sandbox` backend later |

Target structure: in-process sessions (one `Agent` per ACP session, each with its own `workspace`) instead of child
processes. Precondition: C7 fixed (no `Path.cwd()`), C3 fixed (no signal handler per agent), C8 fixed (no shared
global config writes). Until then ACP keeps child processes, but builds the child command in one function (C16).

### 4.4 Transport 3: HTTP + SSE + WebSocket (`ai-rex --port 4000`)

For Lassie, remote front-ends, and hosts that prefer HTTP. Built on what kikolib already ships (checked in
`vendor/kikolib`: `server.py` threaded HTTP/1.1 router with streaming, `websocket.py` RFC 6455 server, `sse.py`
encoder) - nothing new to write at the HTTP level, and no third-party server.

- `POST /v1/sessions` `{workspace?, model?}` -> `{id}`; `DELETE /v1/sessions/{id}`.
- `POST /v1/sessions/{id}/messages` `{text}` -> SSE stream of the 4.1 events; `POST .../stop`; `POST
  .../permissions/{pid}` `{decision}`.
- `GET /v1/sessions/{id}/ws` -> WebSocket carrying exactly the JSONL messages of 4.1 (one JSON object per text
  frame), so a JSONL client becomes a WebSocket client by changing only its pipe.
- **OpenAI-compatible facade** `POST /v1/chat/completions` + `GET /v1/models`: rex as a "model" (the agent loop with
  its tools behind a chat-completions endpoint), so kiko can route a model name to rex the same way it routes to
  `scripts/claude_cli_gateway.py` on port 4000 today. Tool events are not exposed there; only the final text (or a
  token stream).
- Security: bind 127.0.0.1 by default; a random token (header or `?t=` as Lassie does today) is required; a
  non-loopback bind requires an explicit `--host` and a token file; WebSocket `Origin` checked (kikolib supports an
  origin list).

### 4.5 One-shot and pipes (`ai-rex -i "how are you" -m nemotron-ultra`)

Not a protocol but the same path: `core.oneshot` builds one `Agent`, prints `final` as plain text to stdout,
notices to stderr, exits 0 / non-zero. Pipe use (`cmd1 | ai-rex -i "..." | grep xx`): piped stdin is passed to the
model as context for the `-i` instruction; startup must stay fast (lazy imports; no TUI, no kiko control code
loaded). Settings come from environment variables only; `ai-rex` never writes configuration files. Today the prompt is positional (`runtime/__main__.py:48`); `-i/--input` is
added as an alias and `-m` as a short form of `--model`. `--port` with `-i` is meaningless and rejected.

---

## 5. Where the front-ends go

| Front-end | Today (checked) | Goes to | Why |
|---|---|---|---|
| **rex/core** (command `ai-rex`) | `runtime/__main__.py` readline REPL + one-shot + `--jsonl` + `--acp` | `ai_runtime_rex.core` | the thin headless app and pipe client; the plain line REPL stays here (no py_tvision needed, works over SSH and in a dumb terminal); env-only configuration |
| **Strelka -> `rex` CLI** (rex/cli) | `~/py-vision (the py-vision repo)`: `src/py_tvision` toolkit (~17k lines in the repo), `src/strelka` = only `config.py`; agent front-end not started | `ai_runtime_rex.cli`, command `rex`; `py_tvision` copied in unchanged (decided 2026-11-28), pinned | the TUI for the whole rex system: settings panels, full kiko control, status line with context % and kiko health (green/orange/red). kiko is bundled; one install folder. In-process by default (`api.Agent` on a worker thread), `--connect` to a running `rex --port` as an option |
| **Lassie** (rex/web) | `~/lassie`: stdlib Python (1 414 lines) + plain HTML/JS/CSS; spawns one `python -m runtime --jsonl` per page, SSE to the browser | `ai_runtime_rex.web` | same stack as rex (stdlib + plain JS), MIT, same author. Its server becomes a thin layer over `bridge/http.py`; keep one process per page until C7 is fixed, then in-process sessions |
| **Sisi** (Qt GUI) | `~/sisi`: C++/Qt 6, CMake, spawns `python -m runtime --model M --jsonl --local-tools` (`src/rexbridge.cpp:42`) | **stays its own repo**, client of bridge B (JSONL, later WebSocket) | C++ with its own build, packaging and Qt licence questions; a Python Qt GUI would need PySide (not stdlib, breaks the owner's rule). `ai_runtime_rex.ui` stays an empty slot unless the owner wants a stdlib `tkinter` GUI |
| **Layka** (C++ TUI) | `~/layka`: C++ Turbo Vision, spawns `python -P -m runtime --model M --jsonl` (`src/main.cpp:995`) | **stays a separate private repo** | see below |
| **IntelliJ / VS Code / Zed** | ACP via `ai-rex --acp` | no code in rex beyond `bridge/acp.py` (plus, later, an optional thin VS Code extension that only launches `ai-rex --acp`) | editors already speak ACP |
| **kiko** | `~/kiko-src`: gateway, `kiko_runtime_lib.Gateway.chat`; kikolib vendored in rex | unchanged | rex uses kiko as a model backend (`gateway_llm`), kiko may use rex as a model (4.4 facade) |

**Why Layka stays out (process boundary).** Layka embeds rex only as a child process speaking JSONL over stdio.
Nothing is linked or imported across that line: rex needs no Layka file, Layka needs only the `ai-rex` command and
the protocol document (today `python -m runtime`, later the `ai-rex` command). So the public MIT repo never contains, references or builds Layka, and Layka can change,
stay private or carry another licence without touching rex. The protocol `protocol` number in `ready` is the
only contract; rex keeps old message types working within a protocol major. Two notes from the read: Layka's
`LICENSE` file says MIT although the repo is meant to be private (harmless while unpublished; owner to decide), and
`layka/py_port/src/py_tvision/` holds only `__pycache__` (stale bytecode of an old copy, no sources) - it should be
deleted so py_tvision has one source, as with kikolib.

---

## 6. Packaging and versioning

- **Wheel 1: `lib-runtime-rex`** built from `lib/pyproject.toml`: `lib_runtime_rex` + the vendored `kikolib`
  (copied unchanged at build time, as today), no dependencies, no entry points, package data = built-in skills and
  the guard container entrypoint. This is what Odoo / an IDE plug-in host / a surveillance service installs.
- **Wheel 2: `ai-rex`** built from the root `pyproject.toml`: `ai_runtime_rex` apps + vendored `py_tvision` (for
  `cli`), depends on `lib-runtime-rex==<same version>`. Entry points, all thin (owner decision 2026-11-28):
  `ai-rex = ai_runtime_rex.core.main:main` (one-shot, pipes, REPL, `--jsonl`, `--acp`, `--port`; env-only config),
  `rex = ai_runtime_rex.cli.main:main` (the py_tvision TUI), `rex-web = ai_runtime_rex.web.main:main` (Lassie web
  server), `rex-desktop` reserved for the GUI (`ai_runtime_rex.ui`, empty for now).
  The back-compat shims (`runtime`, `runtimelib`, `rex_runtime_lib`) ship in this wheel until removed.
- **kiko is bundled in `rex` (the TUI app).** The TUI distribution carries the kiko gateway next to the vendored `kikolib`
  and installs into ONE folder (code, settings, sessions, logs); deleting the folder removes everything. The
  `ai-rex` engine command alone (library + thin app) stays usable without the kiko gateway (any OpenAI-compatible endpoint).
- **Debian package and Windows zip** (existing `packaging/`): one bundle, portable folder, delete = gone; both
  commands `ai-rex` and `rex` inside. Debian's Perl `rex` package also installs `/usr/bin/rex`: our Debian package
  for the TUI is named e.g. `rex-tui` and the command conflict is handled at packaging time; the portable
  one-folder install is unaffected.
- **Versioning:** one version for both wheels (lockstep), single source in `lib_runtime_rex/__init__.py`, read by
  both `pyproject.toml` files (`dynamic = ["version"]`) and by the consistency test; the other three strings (C21)
  become imports of it. SemVer: `0.x` until the public API (section 3) has one real external embedder; then `1.0`.
  Public = `lib_runtime_rex.__all__` only; a snapshot test of `__all__` and of `Agent`'s signature makes any change
  visible in review. The bridge protocol has its own integer `protocol` version (JSONL / WebSocket) next to ACP's
  `PROTOCOL_VERSION = 1`; a protocol major change needs a release note for Layka / Sisi / Lassie.
- Python floor: the root `pyproject.toml` says `>=3.11` while ruff/mypy and the front-ends target 3.12; set
  `>=3.12` everywhere (owner's platform rule) in the packaging step.

---

## 7. Migration plan (small steps, each green on the existing suite)

Ground rules for every step: behaviour unchanged unless the step says otherwise; the whole suite (the owner counts
~936 tests; the last `test-results.txt`, commit 7990882, shows 743 passed + 37 skipped for pytest plus the header
suite) stays green; one commit per step; old import paths keep working through shims until step 20; existing
tests are not edited except where a step moves a monkeypatch target (then the old path is kept as an alias in the
same commit).

**Phase 0 - safety nets**

1. Add `tests/test_layering.py` (section 2.3) with today's violations allow-listed, and `tests/test_public_api.py`
   (snapshot of the names in `ai_runtime_rex.runtime.__all__` and the `Session` signature). Green by construction.
2. Add a smoke test that `python -P -m runtime --jsonl` answers `ready` and `bye` with a fake LLM (protects the
   contract of Layka, Sisi, Lassie through every later move, C22). `tests/test_jsonl_bridge.py` and
   `tests/test_acp_child_isolation.py` already start `-m runtime`; extend them rather than add a new file.

**Phase 1 - clean-ups inside today's tree (no moves)**

3. C9: `llm/client.py` imports the four message classes from `llm/messages.py` (delete the copies). C10: one
   `AgentState` in `agent/state.py` with the union of both field sets; `loop.py` imports it.
4. C1, C2: `core/__init__.py` exports become lazy (`__getattr__`), the `cli` names stay reachable but are no
   longer imported; vendor-path handling moves to one `_vendor_path.py` that appends only when `kikolib` is not
   importable; the HTTP backend pin is done by the entry points, not by `import`.
5. C11, C12: hooks and callbacks stop importing `agent`/`loop` (types via `typing.TYPE_CHECKING`); event types get
   their own module.
6. C5: library `print` calls become `logging` records plus a `notice` callback; the REPL/one-shot install a stderr
   handler that prints the same text (tests that assert stderr text stay green). The Bulgarian interrupt text moves
   to the app.
7. C3, C4: `AgentLoop(handle_sigint=True)` and `close()` / `__enter__`/`__exit__`; the default stays `True` and the
   atexit registration stays in this step; the apps switch to explicit `install_sigint(loop)` and `close()`; the
   defaults flip to off in step 16.
8. C6: tool wiring moves out of the loop into `tools/defaults.py: builtin_tools(config, workspace, read_tracker,
   names)`; the loop accepts `tools=[]` as "no tools" behind a keyword (`default_tools=True` keeps today's
   behaviour for existing callers).
9. C7: a `workspace: Path` parameter threaded through tools, gate, instructions, skills, guard, sessions; every
   default is `Path.cwd()` resolved at the outer edge only. One tool per commit if the diff grows.
10. C8: library functions take `config` explicitly; `get_config()` is called only by apps and by
    `Session`/`Agent` when the caller passes nothing (unchanged behaviour for the CLI).
11. C14: `agent/gate.py` moves to `permissions/gate.py` (alias left); `permissions/policy.py` + `executor.py` marked
    deprecated. C15: one injected execution backend (`sandbox.local` / `sandbox.guard`) instead of per-tool
    `cli.unsafe_local_tools` checks. C17: the redis lock becomes optional behind a feature check or is removed
    (it serves only the `REX` worktree path).

**Phase 2 - create the library package (mechanical moves)**

12. Create `lib/lib_runtime_rex/` and move sub-packages **leaf first, one per commit**, each with an alias finder
    entry so `ai_runtime_rex.core.<sub>` and `runtimelib.<sub>` resolve to the same module object (the trick
    `runtimelib/__init__.py` already uses): `log`, `native`, `util/procutil`, `security`, `config`, `llm`,
    `context` (split from `agent/context.py` + `instructions.py`), `hooks`, `sessions`, `sandbox` (+
    `guard/entrypoint.py` as package data; Dockerfile path updated), `mcp`, `skills`, `permissions`, `tools`,
    `agent`. Moves use `git mv` so history follows.
13. Bridge: `core/cli/jsonl_bridge.py` -> `bridge/jsonl.py`, `core/acp/server.py` -> `bridge/acp.py`; the child
    command for ACP is built by one function that prefers the installed `ai-rex` and falls back to
    `sys.executable -P -m runtime` with the package's own parent on `PYTHONPATH` (C16).
14. `runtime/embed.py` -> `lib_runtime_rex/api.py`: `Agent` = today's `Session` grown to the section 3 signature;
    `Session`, `GatewayLLM`, `serve_acp` stay as aliases; C18 fixed (no `runtimelib` imports in the library).
    Public API snapshot test switched to `lib_runtime_rex.__all__`.

**Phase 3 - apps**

15. `ai_runtime_rex/core/` becomes the app: `runtime/__main__.py` and `runtime/commands.py` move to
    `ai_runtime_rex/core/main.py`, `repl.py`, `commands.py`; `core/cli/{ansi,render,readline,parser,registry,shell}`
    move with them; `runtime/__main__.py` stays as a 5-line shim (`-m runtime` keeps working). Add `-i/--input`,
    `-m`, piped-stdin handling. Entry point `ai-rex` (the engine command) repointed here. Its configuration is read from
    environment variables only: the config-file writing paths are removed from `core` (they move to the `rex`
    TUI in step 19). The new `rex` entry point appears only with the TUI (step 19).
16. Library defaults flip: no SIGINT handler and no atexit in `AgentLoop` (apps already install them since step 7);
    the layering allow-list should now be empty for rules 1 and 3.
17. (LATER: after the Odoo embedding, see the embedding order above) `bridge/http.py` + `bridge/openai_facade.py` on kikolib `server`/`websocket`/`sse`; `ai-rex --port`. New tests
    with an in-process server and a fake LLM.
18. ACP: `session/request_permission` mapping; then `session/load` via `SessionStore`; then in-process sessions
    (needs steps 9-10 and 16). Each a separate commit with its own tests.
19. `ai_runtime_rex.cli` (the `rex` TUI, formerly Strelka) and `ai_runtime_rex.web` (Lassie) brought in, each
    from its own repo with history if wanted, each on the public API only (decided 2026-11-28). `py_tvision` is
    copied in unchanged. The TUI gets the settings panels and dialogs, the full kiko control (replacing the tray as
    the main control; the tray becomes optional), the status line (context %, kiko health green/orange/red), and
    the bundled kiko gateway with the one-folder install; it starts `ai-rex` processes with settings passed as
    environment variables. `orchestrator/` and `core/rex.py` move to `ai_runtime_rex/extras/` (decided).

**Phase 4 - packaging and shim removal**

20. Two `pyproject.toml` files, single version source, Python `>=3.12`, deb/zip scripts updated (one install
    folder for `rex` with kiko bundled; Debian package for the TUI e.g. `rex-tui`, decided here); a test builds
    both wheels and imports `lib_runtime_rex` from wheel 1 alone (proves the library has no app dependency).
21. After Layka, Sisi and Lassie start `ai-rex --jsonl` instead of `-m runtime` (one-line change in each repo,
    owner's timing): remove `runtimelib/`, `rex_runtime_lib/`, the `ai_runtime_rex.core.<engine>` aliases and,
    last, `runtime/`. This is the only step that can break an outside caller, so it goes into a minor release
    with a note.

**Owner's embedding order (2026-11-28) applied to the steps.** Priority order: (a) steps 1-2, 13 and 15 first
(JSONL bridge hardened and the `ai-rex` command stable: Layka, step 21 later); (b) step 18 first half next
(ACP `session/request_permission`, then `session/load`) for the IntelliJ plug-in through the `ai-rex` process;
(c) steps 3-11 and 14 (clean, no-side-effect, workspace-explicit in-process `Agent`) must be finished and covered
by the public API snapshot test before the Odoo copilot starts, since Odoo imports the library with no process in
between; (d) step 17 (HTTP + WebSocket bridge, `ai-rex --port`) is moved later, after the Odoo step, and is no
longer a precondition for anything. Step 18's in-process sessions part still waits for steps 9, 10, 16.

Order matters only where stated: 3-11 are independent of each other and can be done in any order; 12 needs 4;
13 needs 12 for `cli`; 14 needs 12; 15 needs 13 and 14; 16 needs 7 and 15; 18 needs 9, 10, 16.

---

## 8. Risks and open questions

### 8.1 Risks (technical; decided or mitigated here)

| Risk | Mitigation |
|---|---|
| Alias shims hide a double import (two module objects for one file): `isinstance` and globals split | the alias finder returns the already imported module object (proven pattern in `runtimelib/__init__.py`); a test asserts `a is b` for every alias |
| Tests monkeypatch module attributes by path (`"runtimelib.native.glob"`, `"runtimelib.agent.loop.atexit.unregister"`, 47 test files import `runtimelib`) | same-object aliases make old-path patches hit the real module; moves never rename attributes |
| Front-ends hard-code `-m runtime` and `PYTHONPATH=<rex checkout>` (C22) | `runtime` shim kept until step 21; smoke test from step 2 |
| ACP `parents[3]` path guessing breaks on the move (C16) | step 13 resolves the child command in one function before files move further |
| In-process multi-session (ACP, Lassie, `--port`) before C7 is fully fixed would run tools in the wrong directory | keep child processes until steps 9, 10, 16 are done and a test runs two agents in two temp workspaces in one process |
| Guard client is shared per process on purpose (`process-globals.md`); several in-process sessions share one container and its 30 s timeout | documented limit; per-session guard is a later decision, not part of this refactor |
| Scope creep: the refactor turns into a rewrite | every step is a move or a seam; no behaviour change outside steps 16-18; owner rule "if it works leave it" |

### 8.2 Open questions for the owner (ownership, scope, priorities only)

**Status 2026-11-28: questions 1-5 and 7 are decided (see "Owner decisions 2026-11-28" at the top); only question 6
remains open.** The original wording is kept below for the record.

1. **Strelka home. DECIDED: moves into rex as the `rex` CLI, py_tvision copied in unchanged.** Move the Strelka app from `~/py-vision (the py-vision repo)` into rex as `ai_runtime_rex.cli` (py_tvision
   stays in py-vision and is vendored), or keep Strelka in py-vision as an outside client? Recommendation: move.
2. **Lassie home. DECIDED: web inside rex.** Move Lassie into rex as `ai_runtime_rex.web`, or keep it a separate repo using the HTTP bridge?
   Recommendation: move (same stack, same licence).
3. **`ai_runtime_rex.ui`. DECIDED: empty for now; Sisi stays a separate C++/Qt repo.** Leave empty (Sisi stays C++/Qt in its own repo), or build a small stdlib `tkinter` GUI
   there? Recommendation: leave empty.
4. **Orchestrator and the `REX` facade. DECIDED: go to extras.** Keep as `ai_runtime_rex.extras`, or drop? (Pending since `RENAME-MAP.md`.)
5. **Command name. DECIDED: `ai-rex` is the engine command, `rex` the TUI app (`rex-desktop` GUI, `rex-web`
   web server); Debian package for the TUI e.g. `rex-tui` (Debian's Perl `rex` also installs `/usr/bin/rex`).**
   Original question: the command is `ai-rex` (Debian/Homebrew clash with Perl Rex). Is `ai-rex -i ... -m ... --port 4000` fine, or should the Windows
   zip also ship a `rex.cmd` alias?
6. **Priority / first embedding. DECIDED (owner, 2026-11-28): order of embedding = 1) Layka runs `ai-rex` as a
   child process (stdio JSONL bridge); 2) the IntelliJ plug-in, also through the `ai-rex` process (used for
   testing); 3) the Odoo copilot uses `lib_runtime_rex` DIRECTLY in-process as a library (no shell process from
   Python).** Consequence for the plan: the stdio bridges (JSONL, ACP) come first, the HTTP bridge later, and the
   in-process public API (`Agent` etc.) must be solid by the Odoo step. The independent review
   (`docs/design/2026-11-28-rex-positioning.md`) recommended the Odoo copilot as first; the owner chose this order.
   Original question: is an Odoo copilot or an IDE plug-in the first real embedder?
7. **Layka licence file** (Layka itself stays private and separate - decided) says MIT while the repo is private: keep as is (unpublished), or change?

---

## 9. Self-review (one critical pass)

- **Naming is the weakest point.** Reusing `ai_runtime_rex.core` for the thin app after it held the engine creates
  a period where one path prefix means two things. I kept it because the owner's layout says `rex/core` and the
  branch already uses `ai_runtime_rex`; the alternative (apps under a new name) adds a fourth namespace. The
  layering test plus same-object aliases keep it safe; if the owner prefers clarity over continuity, the cheapest
  change is to name the app package `ai_runtime_rex.agentd` and leave `core` only as a shim.
- **Two wheels may be more than needed.** If no embedder appears soon, one `ai-rex` wheel containing both packages
  works too; the layering test is what really guarantees "the lib imports no UI". Two wheels are listed because the
  owner asked for a lib wheel; step 20 is cheap to postpone.
- **ACP table is from memory** of the spec, flagged as such; it must be checked against the current ACP spec before
  step 18. JetBrains and VS Code specifics (configuration UI, extension needs) were not verified here.
- **The migration has many steps (21).** Phase 1 (steps 3-11) is where the value is; Phase 2 is mechanical; Phase 3
  and 4 can wait for owner decisions. Nothing requires a big-bang commit; the largest is a single sub-package move.
- **Line numbers** were taken from the working tree at commit `addf29f`; two facts (C1 import set, C9 class
  identity) were verified by running Python, the rest by reading. The test count of ~936 is the owner's figure;
  the newest file in the repo shows 743 + 37 skipped and is three days older.
- **Not covered:** async API (the loop is synchronous and threads are used for streaming; an `async` facade can
  wrap `stream` later), the native C++ helper's packaging per platform, and the security model of
  `--port` beyond loopback + token.

---

Sources: rex working tree `C:\private\rex`, branch `refactor/ai-runtime-rex`, commit `addf29f` (read 2026-11-28);
`docs/RENAME-MAP.md`, `docs/RENAME-AI-REX.md`, `docs/architecture/process-globals.md` (2026-11-24),
`docs/DESIGN-orchestration.md` (status lines, revision 3 of 2026-11-25), `test-results.txt` (2026-11-25, commit
7990882); `vendor/kikolib` 0.2.x (`server.py`, `websocket.py`, `sse.py`, `tokens.py`, `keystore.py`); front-end
repos read-only on 2026-11-28: `~/lassie` (README, `lassie/rexproc.py`),
`~/sisi` (README, `src/rexbridge.cpp`), `~/layka` (README, `src/main.cpp`,
`py_port/`, `LICENSE`), `~/py-vision (the py-vision repo)` (README, `pyproject.toml`, `src/`, last commit `96f432b`),
`~/kiko-src` (`gateway/`, `embedded/README.md`). ACP details: from memory, not re-checked. Owner decisions
(commands `ai-rex` / `rex`, bundled kiko, one-folder install, answers to open questions, Debian `rex` package
clash): owner, 2026-11-28; Debian package name clash noted from the owner's statement, not re-checked against the
Debian archive.
