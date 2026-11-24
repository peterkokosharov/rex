<!--
Author: Peter Kokosharov <peter.kokosharov@gmail.com>
AI models: claude-opus-5-5
Licensed under MIT License
-->

# Process-wide shared state (orchestration step 0)

Date: 2026-11-24. Written for step 0 of `docs/DESIGN-orchestration.md`: before sub-agents run in one process,
every piece of state that is shared by the whole process is listed here with the decision taken for it.
"Thread" below means an agent loop running on a thread other than the main one (from step 4a on; a foreground
child in step 3 runs inline on its parent's thread).

## Decisions

| State | Where | Who writes it | Decision |
|---|---|---|---|
| Hook engine (`_engine`) | `runtimelib/hooks/__init__.py`, `get_hook_engine()` | `HookRegistry.register`/`unregister`, at start-up only | **Shared.** `trigger` builds a new `HookContext` per call and only iterates the registry lists, so concurrent triggers are safe. Rule: hooks are registered before any sub-agent starts; no lock. A child gets the parent's engine passed in (design 4.2). Command hooks (`CommandHookRunner`) are per gate, not changed after construction, and run one subprocess per call. |
| `READ_TRACKER` | `runtimelib/tools/edit.py` | `fileops` read/write, `edit` | **Not shared any more.** Step 0: every `AgentLoop` owns a `ReadTracker` (`loop.read_tracker`, or one passed in) and gives it to both `edit` and `fileops`, so a read in one agent never unlocks an edit in another. The global stays only as the default for tools built outside a loop (tests, the `tool` CLI command); it has its own lock. |
| Guard client (`_guard_client`) | `runtimelib/guard/__init__.py`, `get_guard_client()` | lazy creation by the first guard tool call; requests from every agent | **Shared on purpose** (one container per process). Step 0: a lock around lazy creation; a write lock around `stdin.write` + `flush`, so two agents never interleave bytes of two request lines, and a caller waits for that lock at most its own request timeout (`TimeoutError`); the reader thread looks up each answer once under a lock, so a late answer to a timed-out request is dropped instead of ending the reader with a `KeyError`; a timed-out request leaves nothing behind in `_results`; `_proc` is read once per request, so a concurrent `stop()` gives a clean error (tests: `tests/test_orchestration_groundwork.py`). Known limits: the guard server handles one request at a time, so a long `bash` call in one agent can make another agent's call hit its 30 s timeout (design section 11; revisit before background writers). The one unbounded case left: a writer that holds the write lock while blocked on a full pipe (the guard is busy and not reading) waits until the guard reads again; other callers are bounded by their timeout. |
| Config (`_config`) | `runtimelib/config/config.py`, `get_config()`; `Config.set` | the user only: `--model` and flags at start-up, REPL `/model`, `/local`, `/verbose`, bridge `model` | **Shared, no lock.** `get` walks nested dicts; `set` replaces one scalar value. In CPython a single dict read or write is atomic, so a reader sees the old or the new value, never a broken one. Rules: agents never call `Config.set`; a child gets its own LLM client with a fixed model and never re-reads `liteLLM.model` (design 4.2); `cli.unsafe_local_tools` is read live by every gate on purpose (a `/local` switch applies to all agents). |
| MCP manager (`_manager`) | `runtimelib/mcp/__init__.py`, `get_mcp_manager()` | nobody in the runtime | **Not used.** Each top-level loop starts its own servers through `load_mcp_tools` and owns that manager. Children get no MCP tools in v1 (design 4.2), so nothing is shared. |
| Model registry (`_registry`) | `runtimelib/llm/models.py`, `get_registry()` | its constructor (built-in defaults); `update_from_litellm` has no caller | **Shared, read-only.** Only `get` is used (context window lookup). A race on lazy creation would at worst build two identical registries; no lock. |

## Other module globals (checked, no change needed)

| State | Where | Why it is safe |
|---|---|---|
| `_REPL_LOOP`, `_ASKER`, `_MODEL_CACHE` | `runtime/__main__.py` | REPL main thread only. |
| SIGINT handler | `runtimelib/agent/loop.py`, REPL asker in `runtime/__main__.py` | Step 0: only a loop with `depth == 0` built on the main thread installs it; a child never takes Ctrl+C from its parent. `SESSION_START` fires only for `depth == 0`. |
| Permission request ids | `runtimelib/agent/gate.py` | Counter per gate; step 0 adds `id_prefix` (`t-3:perm-1`). Without a prefix ids stay `perm-<n>`, so the protocol is unchanged until step 3 sets a prefix. |
| Session history file | `runtimelib/session/__init__.py` | Step 0: `SessionManager.append` holds a lock around the in-memory list and the file append (test: 200 concurrent appends give 200 valid lines). The lock is **per instance**: `AgentLoop` builds its own manager when none is passed (`loop.py`, `create_session_manager()`), and two managers on the same history file are not protected. Rule: a child always gets the parent's `SessionManager` instance (design 4.2). `get_history` returns the live list and `new_session` swaps it without the lock; harmless in CPython, readers may see the list grow (see Notes). |
| `_quiet` | `runtimelib/config/paths.py` | Set once by the entry point. |
| `_WARNED_ALIAS` (`runtimelib/trust.py`), `_WARNED_MISSING` (`runtimelib/agent/instructions.py`), `_warned` (`runtimelib/config/paths.py`) | | Warn-once flags and sets; a race prints a warning twice at worst. |
| `_LAST_CONTEXT`, `_DEFAULT_WINDOW_HINTED` | `runtime/__main__.py` | Written by the REPL loop's `on_context` callback (context status line, one hint per model). Rule: children never get the REPL's callbacks, otherwise a child would overwrite the parent's status. |
| Logging context (`trace_id_var`, `session_id_var`) | `runtimelib/logging/__init__.py` | `ContextVar`s. A new `threading.Thread` starts with an empty context, so a worker would log without ids. Rule for step 4a: the task runner starts its thread with `contextvars.copy_context().run`. A foreground child (step 3) runs inline and keeps the caller's context. |
| `HookEngine.enable()` / `disable()` | `runtimelib/hooks/__init__.py` | One `_enabled` flag for every agent that shares the engine. Rule: only the top level (user or entry point) switches it; agents never do. |
| Process working directory | `Path.cwd()` in fileops, edit, bash, git, skills, guard mount | The workspace root for every agent. Nothing calls `os.chdir` today. Rule: no agent changes the cwd; a later per-task worktree must pass paths, not `chdir`. |
| `_sandbox_manager` | `runtimelib/guard/sandbox.py` | Used only by `REX` (`runtimelib/rex.py`), not by the runtime loop. |
| Tool registry (`_registry`) | `runtimelib/tools/registry.py` | Used only by the `tool` CLI command. |
| Native backend flags | `runtimelib/native/__init__.py` | Set once at import. |

## Notes: known issues outside step 0 (not fixed here)

- `/agent info` (`runtime/commands.py`) builds a second depth-0 loop on the main thread: it takes SIGINT from the
  REPL loop, fires `SESSION_START` again and starts MCP servers. Older than step 0. Fix later: restore the saved
  SIGINT handler afterwards, or read the info without building a loop.
- `REX.run_agent` (`runtimelib/rex.py`) builds its sub-agents with `create_agent_loop(...)` without `depth`, so they
  count as depth 0 (SIGINT on the main thread, `SESSION_START`). Pass `depth=agent_context.depth`, or leave it until
  step 7 deprecates `REX.spawn_subagent`.
- `AgentLoop.reset_context` (`/clear`) keeps the loop's read tracker: after a clear the model no longer has the file
  text but may still edit a file it read before. Now that the tracker is per loop, `reset_context` could call
  `self.read_tracker.clear()`. A behaviour change, so left for an explicit decision.
- `SessionManager.get_history` returns the live list and `new_session` swaps `_entries` and `session_id` without
  the append lock. No corruption in CPython; a copy under the lock would make it exact.

---

Sources: ai-rex working tree at commit `086fc5e` plus the step 0 changes and the review fixes, read 2026-11-24;
`docs/reviews/step0-code-review-opus.md` (2026-11-24);
`docs/DESIGN-orchestration.md` revision 2 (2026-11-24).
