<!--
Author: Peter Kokosharov <peter.kokosharov@gmail.com>
AI models: claude-opus-5-5
Licensed under MIT License
-->

# Orchestration: sub-agent tasks, a tasks panel and approval modes (design)

Date: 2026-11-24. Status: design only, nothing implemented. Scope: ai-rex runtime plus the shared protocol that
Layka, Sisi and Lassie render. Front-end work is described only as far as the protocol needs it; each front-end
repository gets its own small plan later.

Builds on: `docs/plans/2026-11-23-rex-gaps.md` (permission gate, rules, hooks), Layka
`docs/REVIEW-2026-11-24.md` sections F2 (approval mode) and F3 (background tasks panel). Where this document
differs from F2/F3 it says so and why.

Conventions: **V** = verified in the code at the place given (ai-rex commit `5592aa3`, Layka `bd8924a`).
**A** = assumed, not checked. Paths without a repo prefix are ai-rex paths.

---

## 1. Goal and non-goals

Goal, in four parts:

1. A real `task` tool: the agent starts a sub-agent with its own context, its own model and a tool whitelist; the
   sub-agent runs in the foreground (parent waits) or in the background; status, cancel and result hand-back.
2. A tasks table that every front-end shows the same way (id, title, agent, model, state, elapsed, tokens, last
   line), like the Claude Code background tasks table.
3. Approval modes (`ask`, `edits`, `auto`, `full`) with a clear permission model for writes and shell commands,
   safe defaults, and one way for a front-end to show a pending approval, also when it comes from a sub-agent.
4. One event protocol (the existing JSONL bridge, extended) so Layka, Sisi and Lassie render tasks and approvals
   identically.

Non-goals for this feature: sub-agents that spawn sub-agents (depth > 1), background tasks that write files, git
worktree isolation per task, persistence of running tasks across an ai-rex restart, cost in money, agent profile
files (`agents/<name>.md`), ACP `session/request_permission` (listed as a later step).

---

## 2. What exists today (checked)

| Piece | Where | State |
|---|---|---|
| Agent loop | `runtimelib/agent/loop.py` | V. Synchronous `AgentLoop.run(prompt)`; own `state.messages`; per-turn and session token counters (`_record_usage`, `:572`); `interrupt()` is thread-safe (`threading.Event`, `:288`) and is checked per stream chunk (`:536`) and before each tool call (`:602`, `:632`). |
| Loop constructor | `loop.py:164` | V. Calls `signal.signal(SIGINT, ...)` unconditionally. Python raises `ValueError` when this runs outside the main thread, so **an AgentLoop cannot be built on a worker thread today**. |
| Tool filter | `loop.py:181-222` | V. `tool_names` (names or fnmatch globs) already selects tools; MCP servers start only when a name starts with `mcp__`. Reused as the task tool whitelist. |
| `task` tool | `runtimelib/tools/taskspawn.py` | V. Placeholder returning `{"status":"delegated"}`; already **not** registered (`loop.py:196-197`). Schema has `prompt`, `tools`, `model_class`, `max_steps`. |
| `REX.spawn_subagent` | `runtimelib/rex.py:182-229` | V. asyncio-based, creates a worktree per agent, calls the blocking `loop.run` inside `async def run_agent` (so it does not run in parallel). Not used by `runtime/` (Layka review, V). Hook names `SUBAGENT_SPAWN`/`SUBAGENT_COMPLETE` exist (`hooks/__init__.py:38-39`), only SPAWN is triggered. |
| Permission gate | `runtimelib/agent/gate.py` | V. Order: deny rule > hook deny > ask (rule, mode default, hook) to the asker > allow. `allow_always` stores a per-workspace rule. Request ids are `perm-<counter>` **per gate** (`:186-188`): two gates would produce the same ids. |
| Rules and tool kinds | `runtimelib/permissions/rules.py` | V. Kinds read/write/exec/network/mcp (`classify`, `:882`); `mode_default` (`:999`): read allow, network/MCP ask, write/exec: guard allow, sandbox ask, local ask. `allow_blocker` (`:804`) decides if a bash command is "one simple command of safe characters". `normalize_path` (`:862`). Verdict has `source` (`deny/ask/allow/unsafe/default`). |
| Where tools run | `gate.py:90-96` | V. guard (Docker container), sandbox, local (`--local-tools`). Layka always starts ai-rex with `--local-tools` (Layka review F2, V there). |
| JSONL bridge | `runtimelib/cli/jsonl_bridge.py` | V. Main thread runs `loop.run`; a reader thread handles `stop`, `bye` and `permission_reply` while a request runs. `JsonlAsker` keeps a dict of pending questions, so several blocked askers at once already work. Events: `ready token tool_start tool_end final usage context error permission reset_ok model_ok bye`. |
| Front-ends | Sisi `src/rexbridge.cpp`, Lassie `lassie/server.py`, Layka `src/main.cpp` | V (by grep). All three speak the JSONL bridge; Lassie relays it to the browser over SSE and tracks pending permission ids. |
| ACP server | `runtimelib/acp/server.py` | V. One `--jsonl` child per ACP session; permission questions are denied at once; `task` mapped to kind `other`. |
| Read-before-edit tracker | `tools/edit.py:65` | V. One global `READ_TRACKER` for the process (it has a lock). |
| Guard client | `guard/__init__.py:23-45`, `:326` | V. One process-wide client with a lock: tool calls from several threads are serialised, not broken. |
| Session history | `session/__init__.py:35-80`, `:317` | V. `SessionManager` reads the whole history file in its constructor and appends without a lock. Entries already carry `agent_id`. |
| Claude CLI gateway | `scripts/claude_cli_gateway.py:84` | V. At most 4 `claude` processes at once, then requests wait, then 503. Streaming is "SSE after the CLI has finished", so an LLM call cannot be interrupted midway from rex. |
| kiko-lite endpoint | README `:119` | A. Treated as any OpenAI-compatible endpoint; nothing in this design depends on it. |

---

## 3. Design overview

```
            UI (Layka / Sisi / Lassie)
                 |  JSONL (one protocol)
          +------+--------------------------------+
          | JsonlBridge                            |
          |  reader thread: stop/bye/replies/task_*|
          |  SharedAsker  (questions of all agents)|
          +------+------------------+--------------+
                 |                  |
        main thread            TaskManager (registry, worker threads, cap)
        parent AgentLoop  ---- task tool ----> TaskRunner (thread) -> child AgentLoop
           gate(mode, approval)                   gate(mode, approval, access cap)
```

- **One process, one thread per task.** A task is a child `AgentLoop` on a worker thread, owned by a
  `TaskManager`. No new processes, no asyncio. This reuses the loop, the gate, the guard client and the bridge
  unchanged in spirit and stays stdlib-only.
- **Approval mode is a second axis** next to guard/sandbox/local. It only changes verdicts that came from the
  mode default; deny rules, explicit ask rules and hooks always win.
- **One protocol.** The JSONL bridge gets new events; every event that can come from a sub-agent may carry a
  `task` field. Front-ends keep one pure state model ("reducer") fed by these events.

---

## 4. The task tool

### 4.1 Tool schema (what the model sees)

One tool `task` to start work, plus two small tools for background work. Children never get any of these three.

```json
task {
  "title":      "short label for the tasks table (required, max 80 chars)",
  "prompt":     "full instructions; the child sees nothing else from the parent (required)",
  "agent":      "free label shown in the table, e.g. scout, reviewer (default: task)",
  "model":      "model name from tasks.models; default: the parent's current model",
  "tools":      ["tool names or globs; default: the read-only set"],
  "access":     "read | write   (default read)",
  "background": false,
  "max_steps":  30
}
task_wait   { "ids": ["t-3"], "timeout": 600 }   -> results of finished tasks, states of the others
task_cancel { "id": "t-3" }                      -> {"state":"cancelled"}
```

Results returned to the parent:

- foreground: `{"task":"t-3","state":"done","result":"<child's final answer>","tokens_in":..,"tokens_out":..,"steps":..}`;
- background: at once `{"task":"t-3","state":"running","note":"runs in the background; you will be told when it ends"}`;
- failure: `state` `failed` with `error`, or `cancelled`. The parent's call itself succeeds; the model decides.

The result text is capped at 16 KB for the parent's context (the full text stays in session history and is
available via `task_get`, section 7.2). It is wrapped like a tool result and the system prompt says task results
are data, not instructions (same rule the gateway uses for tool results).

### 4.2 Child set-up (own context, model, tools)

`TaskManager.start(spec, parent)` builds the child with `create_agent_loop(config, ...)`:

- **Context:** fresh `AgentState`; the only user message is `prompt`. Same AGENTS.md instructions and system
  prompt builder as the parent, plus one line: "You are a sub-agent. Do the task, then answer with the result.
  You cannot ask the user questions." `max_steps` from the spec (default 30, hard cap `tasks.max_steps`, 100).
- **Model:** its own `LiteLLMClient` with `default_model = spec.model`. It never reads `liteLLM.model` from the
  config again, so a model change in the parent (`_handle_model`, `jsonl_bridge.py:301`) does not move running
  children. Allowed models: `tasks.models` (default: the parent's model plus `haiku`, `sonnet`, `opus` when the
  endpoint lists them); anything else is an error result, not a silent fallback.
- **Tools:** `tool_names` = the spec's list intersected with the parent's tools, minus `task*`. Default
  read-only set: `fileops`, `git`, `load_skill`, `read_skill_file`. `access: read` additionally makes the child's
  gate **deny** (not ask) every call of kind write/exec/network/mcp, so a read task never blocks on a question and
  `fileops write` is impossible even though `fileops` is in the list. `access: write` is allowed only for
  foreground tasks (section 4.4).
- **Shared objects:** the parent's `SessionManager` (with a new lock on `append`), hook engine and guard client;
  its own `ReadTracker` (a read by a child must not unlock an edit in the parent); its own `PermissionGate` built
  from the same config and workspace, with the bridge's shared asker and the task id.
- **Ids:** `t-<n>` (process-wide counter, short for the UI). `agent_id` of the child = the task id, so session
  history entries and hooks name it. `SUBAGENT_SPAWN` and `SUBAGENT_COMPLETE` hooks fire.

### 4.3 Lifecycle and states

```
queued -> running <-> waiting -> done | failed | cancelled
                                  (lost: only in a snapshot after a crash; not produced in v1)
```

- `queued`: the concurrency cap (`tasks.max_parallel`, default 3) is reached. Reason for 3: the Claude CLI
  gateway runs at most 4 CLI calls at once and the parent needs one.
- `waiting`: the child is blocked on an approval question (`waiting: "approval"`), or in `task_wait` (parent only).
- `failed`: an exception in the child loop, an unknown model, or the step limit reached without an answer.
- `cancelled`: `task_cancel`, the UI's `task_stop`, `bye`, or the parent's `stop` for a **foreground** task.
  Background tasks survive a parent `stop` (the user stopped the answer, not the survey); `task_stop` with
  `"id":"*"` stops all.

### 4.4 Foreground vs background, and who may write

- **Foreground** (`background: false`): the parent's `task` call blocks until the child ends; the parent's
  interrupt cancels the child. This is the simple, common case and it is the first step shipped.
- **Background** (`background: true`): the call returns at once. The child must be `access: read`.
- **Decision: background tasks are read-only in v1.** Two writers in one workspace without worktrees produce
  conflicts that neither agent can see. A foreground writer is safe because the parent is blocked meanwhile.
  `SandboxManager` worktrees (`guard/sandbox.py`) are the later route to background writers (owner question 1).

### 4.5 Result hand-back for background tasks

- When a background task ends, the manager puts a notice in the parent's inbox:
  `[task t-3 "Archive survey" done: <first 2 KB of result>; use task_wait(["t-3"]) for the full result]`.
- The parent loop drains its inbox at the start of every step (`_get_llm_response`, before compaction) and
  adds the notices as one user-role message marked as a system notice. So a running parent sees results at its
  next step; an idle parent sees them with the next user prompt.
- v1 does **not** wake an idle parent by itself (Claude Code does). The UI shows `task_end`, the user decides.
- `task_wait` gives the full (capped) result and removes the notice for that id.

### 4.6 Cancel

`TaskRunner.cancel()` calls `loop.interrupt()` of the child and denies its open questions. Effects (V from the
loop code): a streamed LLM answer stops at the next chunk; the current tool call finishes, the next ones are
answered `cancelled`. Known limit: with the Claude CLI gateway the HTTP answer arrives only after the CLI is done,
and a running `bash` call runs until its own timeout, so a cancel can take up to the gateway timeout (300 s). The
task shows `state:"running"`, `activity:"stopping"` until then, and its result is thrown away.

---

## 5. Approval modes and the permission model

### 5.1 Two axes

- **Where tools run** (exists): `guard` | `sandbox` | `local`. Says how dangerous a call is.
- **Approval mode** (new, `permissions.approval`): `ask` | `edits` | `auto` | `full`. Says how much the user
  wants to be asked. It is applied in `PermissionGate.check` **only** when `verdict.source == "default"` and the
  decision is ASK. Deny rules, explicit ask rules, the `unsafe` source (complex bash) and hook decisions are
  never relaxed by a mode.

Names differ from Layka F2 (`default | accept-edits | auto`): `ask` says what it does, and F2 had no `full`.
Claude Code equivalents: `ask` = default, `edits` = acceptEdits, `full` = bypassPermissions.

### 5.2 The table (rules.py kinds)

| Call | ask | edits | auto | full |
|---|---|---|---|---|
| read: fileops read/list/glob/grep, git status/diff/log/show, skills | allow | allow | allow | allow |
| write inside the workspace (edit, fileops write, git add/commit, render_diagram) | ask | **allow** | **allow** | **allow** |
| write outside the workspace or to a protected path | ask | ask | ask | ask (deny in local mode) |
| exec: one simple command (passes `allow_blocker`) whose first word(s) are in `permissions.auto_exec` | ask | ask | **allow** | **allow** |
| exec: anything else (pipes, `;`, `$()`, redirects, sudo, rm, package managers, git push) | ask | ask | ask | **allow** in guard/sandbox; ask in local |
| network: webfetch, websearch | ask | ask | **allow** | **allow** |
| MCP tools | ask | ask | ask | **allow** in guard/sandbox; ask in local |
| `task` with `access: write` | ask | ask | ask | **allow** |
| `task` with `access: read` | allow | allow | allow | allow |

Definitions:

- **Inside the workspace:** `normalize_path(path, workspace)` (`rules.py:862`) is relative and does not start with
  `..`; symlinks resolved before the check (A: verify `normalize_path` does not already resolve them).
- **Protected paths** (`permissions.protected`, default): `.git/**`, `**/.env`, `**/.env.*`, `**/.ssh/**`,
  `**/*.pem`, `**/*.key`, `.ai-rex/**`, `AGENTS.md`, `.claude/**`. A write there always asks.
- **`permissions.auto_exec`** default (from Layka F2): `ls cat head tail wc grep rg find git status git diff
  git log pytest make ctest mvn -q test`. Matched with the same prefix logic as allow rules, so a command that fails
  `allow_blocker` is never auto-allowed.
- **`full` on the host:** in `local` mode `full` behaves like `auto` plus inside-workspace writes; everything
  else still asks, and protected or outside writes are denied. Running everything without a question on the
  host without a container is the one setting that can lose a machine; owner question 2.

### 5.3 Safe defaults

- Every session starts in `ask` unless the start config says otherwise (`permissions.approval` in the config or
  a front-end start option). A mode change is never saved by itself.
- One-shot runs without a terminal keep `DenyAsker`: every remaining ask is a deny (unchanged).
- Sub-agents use the parent's **current** mode, read live (like the guard mode is read live today,
  `gate.py:117-120`), capped by their `access`: a read task can never write, whatever the mode.
- `allow_always` answers keep working and are stored per workspace as today; an answer given to a sub-agent's
  question stores the same rule (it is the same user and workspace).
- Every allowed call records **why**: `approved` = `rule | mode | user | hook | default`. It goes into
  `tool_start` and the session history, so auto-approved calls are visible afterwards.

### 5.4 Pending approvals with several agents

- **Unique ids:** request ids become `<agent>:perm-<n>` (`main:perm-4`, `t-3:perm-1`); the gate gets an
  `id_prefix`. Today two gates would both send `perm-1` (V, `gate.py:186`).
- **One shared asker:** the existing `JsonlAsker` already supports several waiting questions; it gets the task
  id and title into the `permission` event and a new `permission_closed` event when a question ends without a
  reply (timeout, stop, task cancelled), so a front-end can close a dialog it is still showing.
- **Front-end rule:** questions are shown one at a time in arrival order, with "1 of 3" when more wait and the
  task title when it is not the main agent. Every `permission` must be answered or closed, as today.
- **Question storms:** at most one open question per task (the child is blocked anyway); the asker timeout
  (`permissions.ask_timeout`, 300 s) applies per question.

---

## 6. Tasks panel model (what every front-end shows)

One record per task, kept by the front-end from the events in section 7. No polling, no ticks from ai-rex.

| Field | Type | Meaning / display |
|---|---|---|
| `id` | string | `t-3`; stable for the process lifetime |
| `title` | string | from the tool call, max 80 chars |
| `agent` | string | label (`scout`, `task`) |
| `model` | string | model name |
| `state` | enum | `queued running waiting done failed cancelled` |
| `background` | bool | foreground tasks are shown too, marked |
| `access` | enum | `read` / `write` (write shown with a mark) |
| `parent` | string | `main` in v1 |
| `started` | int | epoch ms; the UI computes elapsed time itself |
| `ended` | int or null | epoch ms |
| `tokens_in`, `tokens_out` | int | the child's own usage (`state.total_tokens_*`); `estimated` bool |
| `steps` | int | loop steps so far |
| `last` | string | last activity line, max 120 chars: `bash: pytest -q`, `read src/x.py`, last non-empty answer line, `waiting for approval: run rm build`, `stopping` |
| `waiting` | string or null | `approval` or null |
| `summary` | string or null | first 300 chars of the result when ended |
| `error` | string or null | on `failed` |

Display rules (same for all three front-ends): running and waiting tasks first, then finished ones greyed until
the next user prompt; elapsed `7m 44s` while active, `since HH:MM` while waiting; tokens as `↓ 45.5k` (`~` when
estimated); a status-line counter `2 background tasks`, hidden at zero. Layka's F4 panel from the review fits this
model unchanged.

Each front-end implements the model as a pure function `apply(state, event) -> state` with unit tests, separate
from drawing (Layka `core.h`, Sisi a plain C++ class, Lassie a JS module).

---

## 7. Protocol (JSONL bridge, extended)

All additions are new event types or new optional fields; old front-ends that ignore unknown types and fields
keep working. `ready` gains `"protocol": 2` so a front-end can tell.

### 7.1 ai-rex -> UI

```json
{"type":"ready","model":"sonnet","protocol":2,"approval":"ask","approvals":["ask","edits","auto","full"],"tool_mode":"local"}
{"type":"approval_ok","approval":"edits"}

{"type":"tool_start","name":"edit","args":{...},"approved":"mode"}
{"type":"tool_start","task":"t-3","name":"bash","args":{"command":"pytest -q"},"approved":"rule"}
{"type":"tool_end","task":"t-3","ok":true,"error":null}

{"type":"permission","id":"t-3:perm-1","task":"t-3","task_title":"Run the tests","tool":"bash","kind":"exec",
 "summary":"run rm -rf build","detail":"...","args":{...},"rule":null,"reason":"","pending":2}
{"type":"permission_closed","id":"t-3:perm-1","reason":"timeout|stopped|cancelled"}

{"type":"task_start","id":"t-3","title":"Run the tests","agent":"tester","model":"haiku","background":true,
 "access":"read","parent":"main","state":"running","started":1764000000000}
{"type":"task_update","id":"t-3","state":"waiting","waiting":"approval","last":"waiting for approval: run rm build",
 "steps":4,"tokens_in":45500,"tokens_out":1200,"estimated":false}
{"type":"task_end","id":"t-3","state":"done","ended":1764000460000,"summary":"All 712 tests pass.",
 "error":null,"steps":9,"tokens_in":46100,"tokens_out":1350,"estimated":false}
{"type":"tasks","items":[{...task record as in section 6...}]}
{"type":"task_result","id":"t-3","state":"done","result":"<full text, max 256 KB>","log":["last 50 activity lines"]}
```

Rules:

- `task_update` only on a change, at most once per second per task (state changes are sent at once).
- A child's `token` events are **not** sent (they would mix into the main transcript); `last` carries the gist.
- A child's `tool_start`/`tool_end` are sent with `task`; a front-end may show them only inside the panel.
- `permission_closed` is sent for every question that ends without a `permission_reply`.

### 7.2 UI -> ai-rex

```json
{"type":"approval","approval":"auto"}         -> approval_ok, or error "unknown approval mode"
{"type":"task_list"}                         -> tasks (snapshot; for a UI restart or reconnect)
{"type":"task_stop","id":"t-3"}              -> task_end with state cancelled ("*" = all)
{"type":"task_get","id":"t-3"}               -> task_result
```

`approval`, `task_list`, `task_stop` and `task_get` are handled on the reader thread (like `stop`), so they work
while the parent is busy. `permission_reply` is unchanged except for the longer ids.

### 7.3 Other surfaces

- **REPL:** `/approval [ask|edits|auto|full]`, `/tasks`, `/task stop <id>`, `/task show <id>`; permission prompts
  print the task title.
- **ACP** (later step): tasks become `tool_call` updates of the `task` tool; `permission` maps to
  `session/request_permission`; approval mode maps to ACP session modes (A: check the ACP spec version then).
- **`REX` Python API:** `spawn_subagent` is reimplemented on `TaskManager` (same signature, worktree kept as an
  option) or marked deprecated; decided in step 7.

---

## 8. Code changes (where)

| File | Change |
|---|---|
| `runtimelib/agent/loop.py` | install the SIGINT handler only on the main thread; accept `read_tracker`, `inbox`, `id_prefix`; drain the inbox each step; record `approved` |
| `runtimelib/agent/gate.py` | `approval` (live from config), `access` cap, `id_prefix`; mode logic in one function `apply_approval(verdict, tool, args)` |
| `runtimelib/permissions/rules.py` | helpers `inside_workspace`, `is_protected`, `auto_exec_match` (reuse `normalize_path`, `allow_blocker`) |
| `runtimelib/tasks/__init__.py` (new, ~300 lines) | `TaskSpec`, `TaskRecord`, `TaskManager` (registry, cap, queue, throttle, events), `TaskRunner` (thread) |
| `runtimelib/tools/taskspawn.py` | replaced by `task`, `task_wait`, `task_cancel` bound to a `TaskManager` |
| `runtimelib/cli/jsonl_bridge.py` | new commands and events; `permission_closed`; asker with task fields |
| `runtimelib/session/__init__.py` | lock around `append` |
| `runtimelib/tools/edit.py` | `ReadTracker` passed in, global stays as the default |
| `README.md`, protocol docstring, `docs/INDEX.md` | protocol 2, approval modes, tasks |

---

## 9. Test strategy

All with the existing pytest suite and fakes; no network, no keys.

1. **Gate table tests:** one parametrized test per row and column of section 5.2 (4 modes x 3 tool modes x ~10
   calls), plus "deny rule beats full", "ask rule beats full", "hook deny beats full", "unsafe bash never
   auto-allowed", protected path and `..` path cases, `access: read` cap.
2. **TaskManager unit tests** with a fake loop factory (no LLM): start/finish, failure, cancel while running,
   cancel while queued, cap and queue order, foreground blocks, background returns at once, background + write
   refused, children get no `task*` tools, result cap, inbox notices.
3. **Threads:** an `AgentLoop` built on a worker thread does not raise; two children asking at once get distinct
   ids and replies reach the right thread; stop denies only the right questions; `bye` ends all tasks and the
   process exits within a bounded time (no leaked non-daemon threads).
4. **Bridge protocol tests** in the style of `tests/test_jsonl_bridge.py`: golden event sequences for a
   foreground task, a background task, a cancelled task, a task question answered, a question timed out
   (`permission_closed`), `approval` change mid-request, `task_list` snapshot; `task_update` throttle with an
   injected clock.
5. **End-to-end with a scripted fake LLM** (as in `tests/test_context_compaction.py`): the parent calls `task`,
   the child reads a file and answers, the parent gets the result and finishes.
6. **Shared protocol fixtures:** the golden sequences are saved as `tests/fixtures/protocol/*.jsonl`; each
   front-end copies them and tests its `apply(state, event)` against the same files, so all three render the same
   table from the same stream.
7. **Live check (manual, documented in TESTING.md):** Claude CLI gateway with `haiku`, one foreground and two
   background tasks, one cancel, one approval from a child, in Layka.

---

## 10. Steps (each one shippable and tested on its own)

0. **Groundwork** (S): SIGINT handler only on the main thread; per-loop `ReadTracker`; session `append` lock;
   gate `id_prefix`. No visible change.
1. **Approval modes in the runtime** (M): gate + rules helpers + config + REPL `/approval`; table tests.
2. **Approval in the protocol** (S): `approval`/`approval_ok`, `ready` fields, `tool_start.approved`,
   `permission_closed`. Then each front-end adds its mode switch and mark (separate repos).
3. **Foreground task** (M): `TaskManager`, `task` tool with `background:false`, `task_start`/`task_end`, child
   questions tagged with `task`. The biggest value for the smallest risk.
4. **Background tasks** (M): `background:true` (read-only), cap and queue, `task_update` throttle, inbox notices,
   `task_wait`, `task_cancel`, UI `task_list`/`task_stop`/`task_get`.
5. **Front-end panels** (per repo, S-M each): reducer + fixtures first, then the panel.
6. **ACP mapping** (S-M): tasks as tool calls, permission questions, modes.
7. **Clean-up** (S): `REX.spawn_subagent` on `TaskManager` or deprecated; old placeholder removed; docs index.

Each step: tests green, docs in the same commit, review by a second model before the next step.

---

## 11. Risks

| Risk | Effect | Answer |
|---|---|---|
| Cancel latency with the CLI gateway (no mid-call abort) | a stopped task keeps a CLI slot up to 300 s | show `stopping`; result discarded; cap counts it until it really ends |
| Gateway capacity (4 CLI calls) | parent waits or gets 503 | `tasks.max_parallel` 3; tasks queue in rex, not in the gateway |
| Token and cost blow-up from loops of sub-agents | budget gone | depth 1, step cap, per-task tokens visible in the table; optional `tasks.max_tokens` per task |
| Hidden shared state between threads (config `set` on model change, globals, hook engine) | wrong model or races | children own their client; audit globals in step 0; thread tests |
| Writes by two agents at once | silent conflicts | background read-only; foreground writer blocks the parent |
| Prompt injection through a task result | parent follows text from a file the child read | results marked as data in the prompt; children cannot ask the user; deny rules still apply |
| Question storms from parallel tasks | user approves blindly | read tasks never ask; at most one open question per task; "1 of N" in the UI |
| `full` mode on the host | destructive command without a question | `full` in local mode stays limited (section 5.2) |
| Model claims delegation without the tool working | wrong answers | the tool is registered only from step 3, when it really runs |

---

## 12. Checked vs assumed

Checked in code (V): everything in section 2; the gate decision order and `source` values; the asker's
multi-question support; bridge threading (main thread runs the loop, reader thread handles stop/replies); the
gateway's concurrency limit and non-interruptible streaming; that `task` is not registered; that Sisi, Lassie and
Layka all use the JSONL bridge (by grep of their sources; their rendering code was not read in detail).

Assumed (A): `normalize_path` behaviour with symlinks; that LiteLLM/kiko-lite endpoints accept parallel requests
like the gateway; that the hook engine and `Config.get` are safe to read from several threads (to be checked in
step 0); ACP session-mode support; that front-ends ignore unknown event types (true for the bridge contract, not
verified per front-end).

---

## 13. Owner questions

1. Background tasks may only read in the first version; writes run only as foreground tasks (the main agent
   waits). Agree? (yes/no; recommended: yes)
2. `full` approval mode when tools run on the host without Docker: 1) never full there (behaves as described in
   5.2), 2) full only with an explicit config switch, 3) full like in Docker. (recommended: 1)
3. Start mode of every new session: 1) `ask`, 2) `edits`. (recommended: 1)
4. Background tasks running at the same time: 1) two, 2) three, 3) four. (recommended: 2, three)
5. Sub-agents may start their own sub-agents (depth 2) in the first version? (yes/no; recommended: no)
