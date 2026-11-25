<!--
Author: Peter Kokosharov <peter.kokosharov@gmail.com>
AI models: claude-opus-5-5
Licensed under MIT License
-->

# Orchestration: sub-agent tasks, a tasks panel and approval modes (design)

Date: 2026-11-24. Revision 2 (after the independent review
`docs/reviews/DESIGN-orchestration-review-fable.md`; the response to every finding is in Appendix A).
**Revision 3 (2026-11-25): sub-agents are OS processes, not threads — owner decisions recorded in section 1.2.
This changes step 0's thread model; the REX API names and D1-D4 are unchanged.**
Status: step 0 (groundwork) implemented; its globals list is `docs/architecture/process-globals.md`. Steps 1-7 not started.
Scope: ai-rex runtime plus the shared protocol that Layka, Sisi and
Lassie render. Front-end work is described only as far as the protocol needs it; each front-end repository gets
its own small plan later.

Builds on: `docs/plans/2026-11-23-rex-gaps.md` (permission gate, rules, hooks), Layka
`docs/REVIEW-2026-11-24.md` sections F2 (approval mode) and F3 (background tasks panel). Where this document
differs from F2/F3 it says so and why.

Conventions: **V** = verified in the code at the place given (ai-rex commit `5592aa3`; line numbers re-checked
for revision 2). **A** = assumed, not checked. Paths without a repo prefix are ai-rex paths.

---

## 1. Goal and non-goals

Goal, in four parts:

1. A real `task` tool: the agent starts a sub-agent with its own context, its own model and a tool whitelist; the
   sub-agent runs in the foreground (parent waits) or in the background; status, cancel and result hand-back.
2. A tasks table that every front-end shows the same way (id, title, agent, model, state, elapsed, tokens, last
   line), like the Claude Code background tasks table.
3. Approval modes (`ask`, `edits`, `auto`, `full`) with a clear permission model for writes and shell commands,
   safe defaults, and one way for a front-end to show a pending approval, also when it comes from a sub-agent.
4. One event protocol (the existing JSONL bridge, extended, opt-in) so Layka, Sisi and Lassie render tasks and
   approvals identically.

Non-goals for this feature: sub-agents that spawn sub-agents (depth > 1), background tasks that write files, git
worktree isolation per task, MCP tools in sub-agents, persistence of running tasks across an ai-rex restart,
waking an idle parent when a task ends, cost in money, agent profile files (`agents/<name>.md`), ACP
`session/request_permission` (a later step), a task queue (at the cap the tool returns an error), UI reconnect
snapshots, full task transcripts over the protocol.

### 1.1 Technical decisions (taken, not owner questions)

- **D1. Background tasks are read-only in v1.** Writes run only in foreground tasks, while the parent waits. Two
  writers in one workspace without worktrees produce conflicts neither agent sees.
- **D2. Every session starts in approval mode `ask`.** A mode change is never saved by itself.
- **D3. At most 2 background tasks at once** (`tasks.max_parallel`, default 2). The Claude CLI gateway runs at
  most 4 CLI calls (`scripts/claude_cli_gateway.py:84`, V); the parent needs one, and abandoned calls after a
  cancel or an LLM client timeout can hold slots for up to 300 s, so 2 leaves headroom.
- **D4. Depth 1.** Sub-agents never get task tools and `TaskManager.start` refuses a caller with `depth > 0`.

### 1.2 Revision 3 (2026-11-25): sub-agents are processes, per-role files, `spawn(role)`

Owner decisions of 2026-11-25. **What changes from step 0:** step 0 (section 3, section 4.4 and
`docs/architecture/process-globals.md`) ran a sub-agent as a *thread* — a foreground child inline on the caller's
thread, a background child on a daemon worker thread. This revision changes the **execution mechanism**: a
sub-agent is a separate **OS process**. The public REX API names (`spawn_subagent`, `run_agent`,
`create_worktree`, `commit_and_merge`), the approval gate (section 5) and decisions D1-D4 are unchanged; only what
runs *under* `spawn_subagent` changes from thread to process. (`subprocess` and `os.posix_spawn` are stdlib, so
the stdlib-only rule holds.)

- **R1. Sub-agents are processes, not threads.** Python cannot safely kill a thread: there is no `thread.kill`; a
  thread stuck in a C call or an infinite loop is unreapable and shares the conductor's memory, so it can corrupt
  the parent and its RAM is never freed. The scheduler does no preemption, so a reliable manual stop/cancel and a
  timeout (`RuntimeMaxSec`-style) **require** a killable child. A process is killed cleanly with
  `terminate()`/`kill()`, frees its RAM on exit, and cannot corrupt the parent. Each process also gets its own
  model, its own `<role>.md`, and its own sandbox/worktree. **This supersedes the step-0 thread model in both
  directions:** M3's inline foreground child and step 4a's worker thread both become child processes. (The inline
  trick existed only to dodge the unkillable-thread and the Windows-wait problems from section 2; a process
  removes the root cause, so those problems no longer apply.)

- **R2. Per-role instruction file.** Each role has its own `<role>.md` — its system prompt, its allowed tools and
  its rules together — e.g. `CODER.md`, `TESTER.md`, `REVIEWER.md`, `ORCHESTRATOR.md`. The main/top session reads
  the general instructions file (`AGENTS.md` / `CLAUDE.md`), never a role file. This pairs with the role-based
  permissions rex already has (ORCHESTRATOR/CODER/TESTER, README P1).
  - **Location:** `<workspace>/.ai-rex/roles/<role>.md` (project), then `~/.config/ai-rex/roles/<role>.md` (user).
    This sits beside the existing `~/.config/ai-rex/roles/<role>.yaml` role configs (README "Role Configs"): the
    `.yaml` carries permissions/config, the `.md` carries the prompt + tools + rules text.
  - **Precedence** with the existing instructions loading (README "Standing instructions"): a spawned role process
    loads, in order, (1) user `AGENTS.md`, (2) workspace `AGENTS.md` (trust-gated and 16 KB-capped as today),
    then (3) its `<role>.md` **last**, so the role text wins for that agent. The top session loads only (1)+(2)
    and no role file. A project `<role>.md` is trust-gated exactly like a project `AGENTS.md`.

- **R3. `spawn(role)` is the only public primitive** — not `process()`, `thread()` or `exec()` directly.
  - Spawning a process = `fork()` + `exec()` (`posix_spawn` wraps both): `fork()` duplicates the parent so the
    conductor survives, then the child `exec()`s the new program and keeps a PID the conductor can kill. A bare
    `exec()` is rejected because it **replaces** the current process image — the conductor itself would be gone.
  - In Python: `subprocess.Popen([sys.executable, "-m", "<rex agent module>", role, ...])` (or `os.posix_spawn`),
    returning a handle with `.terminate()`/`.kill()`. On Windows there is no `fork` — it is `CreateProcess` — but
    the facade is identical.
  - `spawn(role)` bundles: the model, the role's `<role>.md`, the role's tools, the role's permissions, and the
    sandbox/worktree.
  - **Child ↔ conductor channel:** the existing JSONL session bridge over the child's **stdio pipe** — the same
    mechanism `--jsonl` and the ACP server already use (README) — consistent with the existing REX API.

- **R4. D1-D4 and the approval gate still hold over the process model.** Background tasks are read-only (D1);
  every session starts in `ask` (D2); at most 2 background tasks run at once (D3); depth is 1 (D4). The two axes
  and the tables of section 5 are unchanged — a child **process** asks over the same JSONL `permission` messages.
  The REX API names stay; only the execution mechanism under `spawn_subagent` changes thread -> process.

- **R5. IDE integration is unaffected.** rex already plugs into IntelliJ/VS Code via ACP (README "Agent Client
  Protocol"), so this orchestration is internal: the IDE sees a single rex, and the process tree of sub-agents
  stays hidden behind it.

---

## 2. What exists today (checked)

| Piece | Where | State |
|---|---|---|
| Agent loop | `runtimelib/agent/loop.py` | V. Synchronous `AgentLoop.run(prompt)`; own `state.messages`; per-turn and session token counters (`_record_usage`, `:572`); `interrupt()` is thread-safe (`threading.Event`, `:288`) and is checked per stream chunk (`:536`) and before each tool call (`:602`, `:632`). `_run_request` **clears** the interrupt event on entry (`:340`). "Interrupted" and "max steps" end with a normal returned string (`:397-408`), not an exception. |
| Loop constructor | `loop.py:164`, `:167` | V. Calls `signal.signal(SIGINT, ...)` unconditionally (raises `ValueError` off the main thread; on the main thread the last constructed loop takes Ctrl+C from the earlier one) and always fires `SESSION_START`. |
| Tool filter | `loop.py:181-222` | V. `tool_names` are names or fnmatch globs; `["*"]` matches everything, and any pattern starting with `mcp__` starts a new set of MCP server processes for that loop (`:215-222`). |
| `task` tool | `runtimelib/tools/taskspawn.py` | V. Placeholder returning `{"status":"delegated"}`; already **not** registered (`loop.py:196-197`). |
| `REX.spawn_subagent` | `runtimelib/rex.py:182-229` | V. asyncio wrapper around the blocking `loop.run`; not used by `runtime/`. Hook names `SUBAGENT_SPAWN`/`SUBAGENT_COMPLETE` exist (`hooks/__init__.py:38-39`). |
| Permission gate | `runtimelib/agent/gate.py` | V. Order: deny rule > hook deny > ask (rule, mode default, hook) to the asker > allow. A hook `ask` sets the decision to ASK while `verdict.source` stays as it was (`:169-170`). Request ids are `perm-<counter>` per gate (`:181-183`). |
| Rules and kinds | `runtimelib/permissions/rules.py` | V. `classify` (`:882`): unknown tools (so also `task*`) are WRITE (`:896`). `mode_default` (`:999`): read allow; network/MCP ask; write/exec: guard allow, sandbox ask (configurable), local ask. The `unsafe` source is produced **only** when the verdict would otherwise be ALLOW (`:1200-1208`); in local/sandbox a pipe, too-deep nesting or extra `env` leaves `source == "default"`. `normalize_path` (`:862`) is lexical (no symlink resolution). |
| Workspace confinement | `tools/fileops.py:147-170`, `edit.py:270`, `bash.py:112-114`, `diagram.py:119` | V. `resolve_in_workspace` refuses paths outside the workspace for files and for the bash `cwd`. Bash command *arguments* are not confined (`cat /etc/x` is a valid command). |
| Where tools run | `gate.py:90-96` | V. `guard` (Docker container, writes/exec allowed by default), `sandbox` (same container, writes/exec ask by default), `local` (`--local-tools`, on the host). Layka always starts ai-rex with `--local-tools` (Layka review F2). |
| JSONL bridge | `runtimelib/cli/jsonl_bridge.py` | V. Main thread runs `loop.run`; a reader thread handles `stop`, `bye`, `permission_reply`. `JsonlAsker` keeps a dict of pending questions, so several blocked askers already work, but its interrupt check looks only at the parent loop (`:76`) and `stop` denies every open question (`:259-261`). `emit` holds a lock while writing to stdout. |
| Front-ends | Sisi `src/rexbridge.cpp`, Lassie `lassie/server.py`, Layka `src/main.cpp` | V (by grep). All three speak the JSONL bridge; Lassie relays it over SSE and tracks pending permission ids. Their rendering code was not read in detail. |
| ACP server | `runtimelib/acp/server.py` | V. One `--jsonl` child per ACP session; permission questions are denied at once. |
| Read tracker | `tools/edit.py:65`, `fileops.py:23`, `:80` | V. One global `READ_TRACKER` (locked); `EditTool` and `FileOpsTool` both accept a tracker, `loop.py:194` passes none to fileops. |
| Guard client | `guard/__init__.py:171-195`, `guard/entrypoint.py:106` | V. The lock covers only the request id; `stdin.write` + `flush` run **unlocked**, so two threads can interleave bytes. The guard server handles one request at a time: a long `bash` in one agent makes another agent's call hit its 30 s timeout. |
| Session history | `session/__init__.py:35-80`, `:317` | V. `SessionManager` reads the whole history file in its constructor and appends without a lock. Entries carry `agent_id`. |
| LLM client | `llm/client.py:146`, `:322` | V. Default timeout 60 s, with retries (`send_with_retry`). |
| Claude CLI gateway | `scripts/claude_cli_gateway.py:84` | V. At most 4 CLI processes, then waits, then 503. Streaming is "SSE after the CLI has finished": an LLM call cannot be interrupted midway from rex. |
| Windows waits | Python `threading` docs | A (documented stdlib behaviour, not tested here): on Windows a lock or `Event.wait()` without a timeout is not interrupted by Ctrl+C, so a main thread blocked on a worker would not react. |

---

## 3. Design overview

```
            UI (Layka / Sisi / Lassie)
                 |  JSONL; protocol 2 only after the UI's "hello"
          +------+----------------------------------+
          | JsonlBridge                              |
          |  reader thread: stop/bye/replies/task_*  |
          |  writer thread: one queue -> stdout      |
          |  SharedAsker (questions, owner per id)   |
          +------+------------------+----------------+
                 |                  |
        main thread            TaskManager (registry, cap, worker threads for background)
        parent AgentLoop  -- task (foreground: inline on the caller's thread) --> child AgentLoop
           gate --+-- one shared PermissionRules (locked) --+-- child gate (access cap)
```

- **Foreground child = inline.** A foreground task runs the child loop on the caller's own thread: no thread, no
  signal problem, no Windows wait problem. The parent's `interrupt()` forwards to its running child.
- **Background child = worker thread** (step 4a). Read-only, so it never asks a question in practice.

> **Revision 3 (2026-11-25):** both the inline foreground child and the worker-thread background child are
> replaced by a separate **OS process** per sub-agent (section 1.2, R1). The parent holds a process handle and
> cancels with `terminate()`/`kill()` instead of forwarding `interrupt()` to a thread.
- **Approval mode is a second axis** next to guard/sandbox/local, applied to the rule verdict before hooks.
- **One protocol**, opt-in by the UI. Front-ends keep one pure state model fed by the events.

---

## 4. The task tool

### 4.1 Tool schema (what the model sees)

Registered only in a loop with `depth == 0`. Children never get any of these.

```json
task {
  "title":      "short label for the tasks table (required, max 80 chars)",
  "prompt":     "full instructions; the child sees nothing else from the parent (required)",
  "agent":      "label shown in the table, e.g. scout, reviewer (default: task)",
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
- background: at once `{"task":"t-3","state":"running","note":"runs in the background; call task_wait for the result"}`;
- at the cap: `{"error":"2 background tasks already run; wait for one (task_wait) and try again"}`;
- failure: `state` `failed` with `error`, or `cancelled`. The parent's call itself succeeds; the model decides.

The result text is capped at 16 KB for the parent's context (the full text is in session history). It travels as
a tool result; the system prompt says task results are data, not instructions.

### 4.2 Child set-up (own context, model, tools)

`TaskManager.start(spec, parent)` refuses a parent with `depth > 0`, then builds the child with
`create_agent_loop(config, depth=1, ...)`:

- **Context:** fresh `AgentState`; the only user message is `prompt`. Same AGENTS.md instructions and system
  prompt builder, plus: "You are a sub-agent. Do the task, then answer with the result. You cannot ask the user
  questions." `max_steps` from the spec (default 30, hard cap `tasks.max_steps`, 100).
- **Model:** its own `LiteLLMClient` with `default_model = spec.model`; never re-reads `liteLLM.model`, so a model
  change in the parent does not move a running child. Allowed models: `tasks.models` (default: the parent's
  model plus `haiku`, `sonnet`, `opus` when the endpoint lists them); anything else is an error result.
- **Tools:** the spec's names/globs are expanded against the **parent's actual tool dict**, then `task`,
  `task_wait`, `task_cancel` and every `mcp__*` name are removed from the resolved list, and the child is built
  with that explicit name list (no globs reach the child, so no MCP servers start). Default read-only set:
  `fileops`, `git`, `load_skill`, `read_skill_file`.
- **Access cap:** `access: read` makes the child's gate **deny** (not ask) every call whose kind is not read, so a
  read task never blocks on a question and `fileops write` is impossible even though `fileops` is listed.
  `access: write` is accepted only with `background: false` (D1).
- **Loop flags for `depth > 0`:** no SIGINT handler, no `SESSION_START` hook (`AGENT_START`/`AGENT_END` stay).
- **Shared objects:** the parent's `SessionManager` (with a lock on `append`), hook engine, guard client and the
  **same `PermissionRules` object** (so an `allow_always` given to one agent holds for all; `add_allow_always`
  gets a lock). Own `PermissionGate` (access cap, `id_prefix`), own `ReadTracker` passed to both `EditTool` and
  `FileOpsTool` (a child's read must not unlock an edit in the parent).
- **Ids:** `t-<n>` (process-wide counter). `agent_id` of the child = the task id. `SUBAGENT_SPAWN` and
  `SUBAGENT_COMPLETE` fire.

> **Revision 3 (2026-11-25):** under the process model (section 1.2) the child cannot share the parent's Python
> objects in memory. The "shared objects" above (session, rules, guard client) are instead reached across the
> stdio JSONL channel (R3): the conductor owns them and the child requests through it. `spawn(role)` supplies the
> model, `<role>.md`, tools, permissions and worktree; the in-memory sharing details here apply only to the
> step-0 thread prototype.

### 4.3 Lifecycle and states

```
running <-> waiting -> done | failed | cancelled
```

- `waiting`: the child is blocked on an approval question (`waiting: "approval"`). Only foreground write tasks
  can reach it in v1.
- `failed`: an exception in the child loop, an unknown model, an LLM 503 or timeout (never retried silently by
  the task layer), or the step limit reached.
- `cancelled`: `task_cancel`, the UI's `task_stop`, `bye`, or the parent's `stop` for a **foreground** task.
  Background tasks survive a parent `stop` (the user stopped the answer, not the survey).
- The state is decided by the `TaskRunner` from its own `cancelled` flag, the loop's `state.interrupted` and
  `state.step >= max_steps`, **never** from the returned text.

### 4.4 Foreground and background

- **Foreground** (`background: false`, step 3): the `task` tool runs the child loop inline on the caller's thread
  and returns its result. The parent's `interrupt()` forwards to the registered running child.
- **Background** (`background: true`, step 4a): the child runs on a daemon worker thread; the call returns at once.
  Only `access: read` (D1). At most `tasks.max_parallel` at once (D3); beyond that the call returns an error.

> **Revision 3 (2026-11-25):** per section 1.2 (R1) the child here is an OS process, not a worker thread. The
> parent keeps its PID/handle; cancel and the `RuntimeMaxSec`-style timeout call `terminate()`/`kill()`, which a
> thread could not provide. D1-D3 (read-only, cap 2) are unchanged (R4).

### 4.5 Result hand-back for background tasks

- When a background task ends, the manager puts **one line without result text** in the parent's inbox:
  `[task t-3 "Archive survey" ended: done; call task_wait(["t-3"]) for the result]`.
- The parent loop drains its inbox at the start of every step and adds the lines as one user-role notice. A
  running parent sees them at its next step; an idle parent with the next user prompt. No wake-up in v1.
- The result itself reaches the parent only as a tool result of `task_wait` (lower authority than a user message).

### 4.6 Cancel

`TaskRunner.cancel()` sets its `cancelled` flag, calls `loop.interrupt()` and denies the child's open questions
(each with `permission_closed`). The runner checks the flag **before** calling `run` and, because `_run_request`
clears the interrupt event on entry (`loop.py:340`), calls `interrupt()` again once the loop has started when the
flag is set. Effects: a streamed LLM answer stops at the next chunk; the current tool call finishes, the next ones
are answered `cancelled`. Known limit: with the Claude CLI gateway the answer arrives only after the CLI is done,
and a running `bash` call runs until its own timeout, so a cancel can take up to 300 s. The task shows
`last: "stopping"` until then and its result is thrown away.

---

## 5. Approval modes and the permission model

### 5.1 Two axes and where the mode is applied

- **Where tools run** (exists): `guard` | `sandbox` | `local`.
- **Approval mode** (new, `permissions.approval`): `ask` | `edits` | `auto` | `full`.

Rules for `apply_approval(verdict, tool, args, tool_mode)`:

1. It runs on the **rule verdict, before hooks**. A hook `ask` or `deny` afterwards always wins.
2. It relaxes only a verdict with `source == "default"` and decision ASK. Deny rules and explicit ask rules are
   never relaxed.
3. `evaluate` returns the unsafe reason as its own field (`verdict.unsafe`) on **every** verdict, not only when it
   would otherwise allow. A verdict with `unsafe` set is never relaxed on the host (local) or in sandbox; in guard
   it is relaxed only by `full`.
4. Protected paths ask in every mode and every tool mode (a behaviour change for guard, where they are allowed
   today).

Names differ from Layka F2 (`default | accept-edits | auto`): `ask` says what it does, and F2 had no `full`.
Claude Code equivalents: `ask` = default, `edits` = acceptEdits, `full` = bypassPermissions.

### 5.2 Call classes

| Class | Calls |
|---|---|
| read | fileops read/list/glob/grep, git status/diff/log/show, `load_skill`, `read_skill_file`, `task` with `access: read`, `task_wait`, `task_cancel` (added to `classify`) |
| edit | `edit`, fileops write/edit (and delete), `render_diagram`, inside the workspace, not protected |
| protected | an edit-class call on a protected path (5.4) |
| git-write | git add/commit/checkout/branch/... (can run repository hooks) |
| simple-exec | bash: passes `allow_blocker`, no `unsafe`, no `env`, first word(s) in `permissions.auto_exec`, no argument that is absolute, contains `..`, or is an option from the deny list (`--output*`, `--pre*`, `--ext-diff`, `-delete`, `-exec*`, `-ok*`, `-fprint*`, `-fls`) |
| other-exec | every other bash command, and anything with `unsafe` set |
| network | webfetch, websearch |
| mcp | `mcp__*` |
| task-write | `task` with `access: write` |

Default `permissions.auto_exec` = `ls cat head tail wc grep git status git diff git log`. Test runners and build
tools (`pytest`, `make`, ...) run code from files the agent can write, so they are **not** in the default; a
user adds them through `allow_always` or an allow rule.

### 5.3 Tables per tool mode

Cells: today's verdict -> verdict per mode. "today" is the mode default without rules; `n/a` = the tool is not
registered today. Explicit allow/ask/deny rules and hooks apply on top of every cell as today.

**local (on the host; Layka's mode)**

| Class | today | ask | edits | auto | full |
|---|---|---|---|---|---|
| read | allow | allow | allow | allow | allow |
| edit | ask | ask | **allow** | **allow** | **allow** |
| protected | ask | ask | ask | ask | ask |
| git-write | ask | ask | ask | ask | ask (*) |
| simple-exec | ask | ask | ask | **allow** | **allow** |
| other-exec | ask | ask | ask | ask | ask (*) |
| network | ask | ask | ask | ask | ask (*) |
| mcp | ask | ask | ask | ask | ask (*) |
| task-write | n/a | ask | ask | ask | ask (*) |

(*) Pending owner question 1: until he decides otherwise, `full` on the host equals `auto` (no project code runs
without a question on the host).

**sandbox (container; defaults ask)**

| Class | today | ask | edits | auto | full |
|---|---|---|---|---|---|
| read | allow | allow | allow | allow | allow |
| edit | ask | ask | **allow** | **allow** | **allow** |
| protected | ask | ask | ask | ask | ask |
| git-write | ask | ask | ask | ask | **allow** |
| simple-exec | ask | ask | ask | **allow** | **allow** |
| other-exec | ask | ask | ask | ask | **allow** (not when `unsafe`) |
| network | ask | ask | ask | ask | **allow** |
| mcp | ask | ask | ask | ask | **allow** |
| task-write | n/a | ask | ask | ask | **allow** |

**guard (container; writes and exec already allowed by default)**

| Class | today | ask | edits | auto | full |
|---|---|---|---|---|---|
| read | allow | allow | allow | allow | allow |
| edit, git-write, simple-exec, other-exec | allow | allow | allow | allow | allow |
| protected | allow | **ask** | **ask** | **ask** | **ask** (behaviour change) |
| network | ask | ask | ask | ask | **allow** |
| mcp | ask | ask | ask | ask | **allow** |
| task-write | n/a | ask | ask | ask | **allow** |

In guard the approval mode changes only protected paths, network, MCP and write tasks: the container is the
boundary and the user chose it. Network stays `ask` in `auto` everywhere (a read followed by a fetch would
otherwise move data out without a question); `webfetch(domain:...)` allow rules cover the convenient case.

### 5.4 Protected paths

`permissions.protected`, default: `.git/**`, `.gitattributes`, `**/.env`, `**/.env.*`, `**/.ssh/**`, `**/*.pem`,
`**/*.key`, `.ai-rex/**`, `AGENTS.md`, `.claude/**`, and `~/.config/ai-rex/**` (config and allow-always store;
matched on the absolute resolved path). Matching runs on the lexical path **and** the symlink-resolved path,
case-folded on Windows and macOS (`.GIT/`, `Agents.md`).

### 5.5 Safe defaults

- Start mode `ask` (D2). One-shot runs without a terminal keep `DenyAsker`.
- Sub-agents use the parent's **current** mode, read live, capped by their `access`.
- Every allowed call records **why**: `approved` = `rule | mode | user | hook | default`, in `tool_start` and in
  session history.

### 5.6 Pending approvals with several agents

- **Unique ids:** `<agent>:perm-<n>` (`main:perm-4`, `t-3:perm-1`); the gate gets an `id_prefix`.
- **Owner per question:** the shared asker stores the asking loop with each pending question. Its interrupt check
  uses that loop, not the bridge's parent loop. `stop` denies the parent's questions and those of foreground
  children; questions of background tasks stay open. `bye` denies all.
- **`permission_closed`** is sent for every question that ends without a reply (timeout, stop, cancel). A
  `permission_reply` that crosses it is dropped silently (today it returns `unknown permission id`).
- **What the dialog shows:** the main line is the gate's own summary; the model-chosen task title is only a
  secondary label ("from task t-3: Run the tests"). One question at a time, in arrival order.
- The asker timeout (`permissions.ask_timeout`, 300 s) applies per question.

---

## 6. Tasks panel model (what every front-end shows)

| Field | Type | Meaning / display |
|---|---|---|
| `id` | string | `t-3`; stable for the process lifetime |
| `title` | string | from the tool call, max 80 chars |
| `agent` | string | label (`scout`, `task`); the owner's reference panel shows it |
| `model` | string | model name |
| `state` | enum | `running waiting done failed cancelled` |
| `background` | bool | foreground tasks are shown too, marked |
| `access` | enum | `read` / `write` |
| `started` | int | epoch ms; the UI computes elapsed time itself |
| `ended` | int or null | epoch ms |
| `tokens_in`, `tokens_out`, `estimated` | int, int, bool | the child's own usage |
| `steps` | int | loop steps so far |
| `last` | string | max 120 chars: `bash: git status`, `read src/x.py`, last non-empty answer line, `waiting for approval`, `stopping` |
| `summary` | string or null | first 300 chars of the result when ended |
| `error` | string or null | on `failed` |

Display rules: active tasks first, finished ones greyed until the next user prompt; elapsed `7m 44s` while
running, `since HH:MM` while waiting; tokens as `↓ 45.5k` (`~` when estimated); a status-line counter
`2 background tasks`, hidden at zero. Each front-end implements `apply(state, event) -> state` as a pure function
with unit tests, separate from drawing.

---

## 7. Protocol (JSONL bridge, opt-in protocol 2)

### 7.1 Opt-in

`ready` gains `"protocol": 2` (what ai-rex offers). A UI that understands it answers
`{"type":"hello","protocol":2}` (or starts ai-rex with `--protocol 2`). Without it the session behaves exactly as
today: no background tasks (the `task` tool rejects `background: true`), no events outside a request, no new
event types. Foreground tasks still work for an old UI; their child events are not sent, and child questions
arrive as ordinary `permission` events (the extra fields are ignored). A test checks that a v1 session's event
stream is unchanged.

### 7.2 ai-rex -> UI (protocol 2)

```json
{"type":"ready","model":"sonnet","protocol":2,"approval":"ask","approvals":["ask","edits","auto","full"],"tool_mode":"local"}
{"type":"approval_ok","approval":"edits"}

{"type":"tool_start","name":"edit","args":{...},"approved":"mode"}

{"type":"permission","id":"t-3:perm-1","task":"t-3","task_title":"Run the tests","tool":"bash","kind":"exec",
 "summary":"run rm -rf build","detail":"...","args":{...},"rule":null,"reason":""}
{"type":"permission_closed","id":"t-3:perm-1","reason":"timeout|stopped|cancelled"}

{"type":"task_start","id":"t-3","title":"Archive survey","agent":"scout","model":"haiku","background":true,
 "access":"read","state":"running","started":1764000000000}
{"type":"task_update","id":"t-3","state":"waiting","last":"waiting for approval","steps":4,
 "tokens_in":45500,"tokens_out":1200,"estimated":false}
{"type":"task_end","id":"t-3","state":"done","ended":1764000460000,"summary":"...","error":null,
 "steps":9,"tokens_in":46100,"tokens_out":1350,"estimated":false}
```

Rules:

- A child's `token`, `tool_start` and `tool_end` are **never** sent; `task_update.last` carries the gist. Only the
  parent's `tool_start`/`tool_end` reach the transcript, so start/end pairing stays as today.
- `task_update` only on a change, at most once per second per task (state changes at once).
- All output goes through one writer thread with a queue; the reader thread never writes, so a slow pipe cannot
  stop `stop` from being read.

### 7.3 UI -> ai-rex

```json
{"type":"hello","protocol":2}
{"type":"approval","approval":"auto"}     -> approval_ok, or error "unknown approval mode"
{"type":"task_stop","id":"t-3"}           -> task_end with state cancelled ("*" = all background tasks)
```

`approval` and `task_stop` are handled on the reader thread, like `stop`. `permission_reply` is unchanged except
for the longer ids. Later, when Lassie needs reconnects: `task_list` -> a snapshot event.

### 7.4 Other surfaces

- **REPL:** `/approval [ask|edits|auto|full]`, `/tasks`, `/task stop <id>`; permission prompts name the task.
- **ACP** (step 6): tasks become `tool_call` updates of the `task` tool; `permission` maps to
  `session/request_permission`; approval mode to ACP session modes (A: check the ACP spec then).
- **`REX.spawn_subagent`:** marked deprecated in step 7, not rebuilt.

---

## 8. Code changes (where)

| File | Change |
|---|---|
| `runtimelib/agent/loop.py` | SIGINT handler and `SESSION_START` only when `depth == 0` and on the main thread; `read_tracker` passed to `EditTool` and `FileOpsTool` (`:194`); `inbox` drained each step; running child registered for interrupt forwarding; `approved` recorded |
| `runtimelib/agent/gate.py` | `apply_approval` before hooks; `access` cap; `id_prefix`; shared `PermissionRules` |
| `runtimelib/permissions/rules.py` | `verdict.unsafe` on every verdict; `classify` for `task*`; helpers `is_protected` (lexical + resolved, case-folded), `simple_exec` (auto_exec list, argument checks); lock in `add_allow_always` |
| `runtimelib/tasks/__init__.py` (new, ~250 lines) | `TaskSpec`, `TaskRecord`, `TaskManager` (registry, cap, throttle, events, inbox), `TaskRunner` (inline or thread; cancel flag) |
| `runtimelib/tools/taskspawn.py` | replaced by `task`, `task_wait`, `task_cancel` bound to a `TaskManager` |
| `runtimelib/cli/jsonl_bridge.py` | `hello` opt-in; writer thread; asker with owner per question and `permission_closed`; new commands and events |
| `runtimelib/guard/__init__.py` | write lock around `stdin.write` + `flush` |
| `runtimelib/session/__init__.py` | lock around `append` |
| `README.md`, bridge docstring, `docs/INDEX.md` | protocol 2, approval modes, tasks |

---

## 9. Test strategy

All with pytest and the existing fakes; no network, no keys.

1. **Approval tables:** one parametrized test per cell of the three tables in 5.3, plus: deny rule beats `full`;
   ask rule beats `full`; hook `ask` and hook `deny` are not relaxed; crafted calls in `auto` ask
   (`{"command":"ls","env":{"PATH":"."}}`, `cat /etc/passwd`, `cat ../x`, `find . -delete`, `git diff --output=f`,
   `rg --pre x`, a pipe); protected paths by symlink and by case variant (`.GIT/config`, `Agents.md`); `access:
   read` cap; mode switched while a question is open (the open question is not re-decided).
2. **TaskManager** with a fake loop factory: finish, failure, cancel while running, **cancel before the child's
   `run` starts**, cap returns an error, foreground runs inline, background returns at once, background + write
   refused, `depth > 0` refused, `tools:["*"]` gives no task tool and no `mcp__*`, result cap, state from the
   flag not from the text, inbox line without result text.
3. **Threads and process:** a child built at depth 1 does not touch the SIGINT handler; two children asking at
   once get distinct ids and replies reach the right thread; `stop` leaves a background question open and closes
   foreground ones; cancel with an open question gives exactly one `permission_closed`; `bye` with a child stuck
   in a fake 300 s LLM call exits in bounded time; two threads through the guard client (fake guard process) get
   whole lines; 200 concurrent session appends give 200 valid lines; one `allow_always` holds for parent and child.
4. **Bridge protocol:** golden event sequences (foreground task, background task, cancelled task, child question
   answered, question timed out, approval change mid-request) and **a v1 session without `hello` produces an
   unchanged stream**; `task_update` throttle with an injected clock.
5. **End-to-end with a scripted fake LLM:** the parent calls `task`, the child reads a file and answers, the
   parent gets the result and finishes.
6. **Shared protocol fixtures:** the golden sequences are **generated by the bridge tests** into
   `tests/fixtures/protocol/*.jsonl` (a regenerate flag; never edited by hand). Each front-end copies them and
   tests its `apply(state, event)` on the same files.
7. **Live check (manual, in TESTING.md):** Claude CLI gateway with `haiku`, one foreground write task with one
   approval, two background read tasks, one cancel, in the first front-end chosen (owner question 3).

---

## 10. Steps (each one shippable and tested on its own)

0. **Groundwork** (S): SIGINT/`SESSION_START` only at depth 0 on the main thread; per-loop `ReadTracker` for edit
   and fileops; session `append` lock; guard client write lock; gate `id_prefix`. Ends with a written list of
   process globals and the decision for each: hook engine, `READ_TRACKER`, guard client, config (`set` from the
   model command), MCP manager, model registry. No visible change.
1. **Approval modes in the runtime** (S-M): `verdict.unsafe`, `classify` for `task*`, `apply_approval` before
   hooks, protected paths, `simple_exec`, config, REPL `/approval`; the table tests.
2. **Approval in the protocol** (S): `hello` opt-in, writer thread, `approval`/`approval_ok`, `ready` fields,
   `tool_start.approved`, `permission_closed`. Then each front-end adds its mode switch and mark.
3. **Foreground task** (S-M): `TaskManager`, `task` tool with `background:false`, inline child, interrupt
   forwarding, `task_start`/`task_end`, child questions tagged with `task`, shared rules. (Owner question 2:
   pause here?)
4a. **Background start** (M): worker thread, `background:true` read-only, cap with error, `task_end`, inbox line.
4b. **Background control** (S): `task_wait`, `task_cancel`, UI `task_stop`, asker owner per question for `stop`.
4c. **Live panel data** (S): `task_update` with throttle, `last`, tokens, steps.
5. **Front-end panels** (per repo, S-M each): reducer on the fixtures first, then the panel; first front-end per
   owner question 3.
6. **ACP mapping** (S-M).
7. **Clean-up** (S): `REX.spawn_subagent` deprecated; placeholder removed; docs index.

Each step: tests green, docs in the same commit, review by a second model before the next step.

---

## 11. Risks

| Risk | Effect | Answer |
|---|---|---|
| Cancel latency with the CLI gateway | a stopped task holds a CLI slot up to 300 s | `stopping` shown; result discarded; cap 2 leaves headroom |
| Gateway capacity (4 CLI calls) and client retries | 503 or long waits | cap 2; a 503/timeout in a child is `failed`, not retried by the task layer |
| One long guard call blocks every agent | another agent's guard call times out after 30 s | documented; background tasks are read-only and short; tested with a fake guard; revisit before background writers |
| Crafted shell calls in `auto` | code runs without a question | `unsafe` never relaxed on the host; short `auto_exec`; argument checks; tests per trick |
| Hidden shared state between threads | wrong model or races | children own their client; the globals list of step 0; thread tests |
| Writes by two agents at once | silent conflicts | D1 |
| Prompt injection through a task result | parent follows text from a file the child read | result only as a tool result; inbox line has no child text; deny rules still apply |
| Old front-ends confused by new events | garbage in the transcript | protocol 2 only after `hello` |
| Token blow-up | budget gone | depth 1, step cap, tokens per task visible |

---

## 12. Checked vs assumed

Checked in code (V): everything in section 2, re-checked for revision 2 (gate order and line numbers, unsafe only
on ALLOW, `classify` default, guard client lock scope and single-threaded guard server, `_run_request` clearing
the interrupt, returned strings for interrupted/max steps, asker interrupt check and `stop` behaviour,
`resolve_in_workspace` in fileops/edit/bash/diagram, fileops tracker parameter, LLM client timeout and retry,
lexical `normalize_path`).

Assumed (A): Windows waits without timeout ignore Ctrl+C (stdlib documentation); LiteLLM/kiko-lite endpoints
accept parallel requests; hook engine and `Config.get` are safe from several threads (step 0 checks and writes it
down); front-ends ignore unknown fields (true for the bridge contract, not verified per front-end); ACP session
modes.

---

## 13. Owner questions

1. May `full` (and `auto`) run project code on the host without a question, that is tests, builds, any shell
   command, network and MCP, when tools run without Docker? 1) no: on the host `full` equals `auto` (only edits
   and a short list of read-only commands run unasked), 2) only after an explicit config switch, 3) yes, like in
   Docker. (recommended: 1)
2. Stop after step 3 (foreground tasks and approval modes) and use it for a while before building background
   tasks? (yes/no; recommended: yes)
3. Which front-end gets the tasks panel and the approval switch first? 1) Layka, 2) Sisi, 3) Lassie.
   (recommended: 1, the review already designed its F4 panel)

---

## Appendix A. Review response

Review: `docs/reviews/DESIGN-orchestration-review-fable.md`. Every finding was checked against the code
(read-only) before the decision.

| Id | Decision |
|---|---|
| B1 | Applied: `verdict.unsafe` on every verdict, never relaxed on the host or in sandbox (5.1); short `auto_exec` without test runners plus argument checks (5.2); network stays `ask` in `auto` (5.3). Confirmed at `rules.py:1200-1208`. |
| B2 | Applied: three tables per tool mode with today's verdict (5.3); guard stated plainly; `task*` added to `classify`; outside-workspace row deleted (confinement confirmed in fileops/edit/bash/diagram); protected paths ask in every mode, guard change listed. |
| M1 | Applied: SIGINT and `SESSION_START` only at depth 0 on the main thread (4.2, 8, step 0). |
| M2 | Applied: runner-owned `cancelled` flag, checked before `run`, interrupt re-applied after start; state never from the text (4.3, 4.6). Confirmed at `loop.py:340`, `:397-408`. |
| M3 | Applied: foreground child runs inline on the caller's thread; threads only from step 4a (3, 4.4). Windows wait behaviour marked A. |
| M4 | Applied: globs expanded against the parent's tools, `task*` and `mcp__*` removed from resolved names, task tools only at depth 0, `start` refuses depth > 0, no MCP in children (4.2, D4). |
| M5 | Applied: section 2 corrected; write lock in step 0; "one long guard call blocks all agents" documented in risks and tested. Confirmed at `guard/__init__.py:176-195`, `entrypoint.py:106`. |
| M6 | Applied: `apply_approval` runs on the rule verdict before hooks; test added. Confirmed at `gate.py:169-170`. |
| M7 | Applied: `hello` opt-in; without it the stream is unchanged; child `tool_start`/`tool_end` dropped entirely; test for an unchanged v1 stream (7.1, 7.2). |
| M8 | Applied: owner per question, interrupt check on the asking loop, `stop` scoped; one shared `PermissionRules` with a lock (4.2, 5.6). Confirmed at `jsonl_bridge.py:76`, `:259-261`. |
| Minor 1 | Applied: symlink-resolved and case-folded matching, `.gitattributes` added, `normalize_path` marked V lexical (5.4). |
| Minor 2 | Applied: `git-write` is its own class, not in `edits` (5.2, 5.3). |
| Minor 3 | Applied: inbox line without result text (4.5). |
| Minor 4 | Applied: fileops tracker listed; gate line numbers corrected to `:181-183` (2, 8). |
| Minor 5 | Applied: a reply crossing `permission_closed` is dropped silently (5.6). |
| Minor 6 | Applied: one writer thread, the reader never writes (7.2). |
| Minor 7 | Applied: default cap 2; 503/timeout in a child is `failed`, not retried by the task layer (D3, 4.3). |
| Minor 8 | Applied: dialog main line = gate summary, task title secondary (5.6). |
| Minor 9 | Applied: `~/.config/ai-rex/**` protected (5.4). |
| Cut: `queued` and queue | Applied: at the cap the tool returns an error. |
| Cut: `lost`, `waiting` for `task_wait` | Applied. |
| Cut: free `agent` label | Rejected: the owner's reference panel (Layka review F3) has an agent column; it is one string with no logic. |
| Cut: `task_get`/`task_result`, `tasks` snapshot | Applied: both removed; snapshot noted as "later, when Lassie needs reconnects". |
| Cut: `tasks.max_tokens`, `pending` counter | Applied: both removed. |
| Cut: `REX.spawn_subagent` rebuild | Applied: deprecated in step 7. |
| Steps: split step 4 | Applied: 4a, 4b, 4c; step 0 ends with a written globals list. |
| Missing tests | Applied: all listed tests added to section 9 (fixtures generated by the bridge tests). |
| Owner questions | Applied: old Q1, Q3, Q4, Q5 are decisions D1-D4; old Q2 widened to "project code on the host"; two scope questions added. |
