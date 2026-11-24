<!--
Author: Peter Kokosharov <peter.kokosharov@gmail.com>
AI models: claude-opus-5-5
Licensed under MIT License
-->

# Code review: orchestration step 0 (commit 14a1a6b)

Date: 2026-11-24. Reviewer: independent, did not write the code. Scope: `git show 14a1a6b` (parent `086fc5e`)
against `docs/DESIGN-orchestration.md` rev 2, section 10 step 0, plus the modules it touches.

## Verdict: OK

Step 0 does what the design asks, changes nothing visible for today's single-agent use, and every claimed fix is
covered by a test that fails without it (checked by mutation, below). No blocker. Three medium items should be
fixed before step 3 (foreground task) at the latest; the guard reader race before step 4a (threads).

Counts: blocker 0, medium 3, minor 8.

## What was checked

- Suite in a scratch worktree at `14a1a6b` via `tools/run_tests.py`: headers PASS, pytest 721 passed / 16 skipped,
  C++ not run (no g++). OVERALL PASS.
- Mutation (scratch worktree, repo untouched):
  - guard write lock disabled: `test_two_threads_send_whole_lines` fails.
  - session append lock disabled: `test_200_concurrent_appends...` fails 5 of 5 runs (Windows).
  - `loop.py` reverted to `086fc5e`: 5 new tests fail plus `test_tool_wiring`.
  - `gate.py` reverted: both gate tests fail.
  - `test_top_level_on_main_thread_takes_sigint` passes either way; it is a regression guard (correct by intent).
  So 9 of 10 new tests discriminate; the 10th guards existing behaviour.
- Lock analysis (guard): `_lock` (id allocation, `start`, `stop`) and `_write_lock` are never nested, and
  `event.wait` runs outside both, so there is no deadlock and no lock is held across the response wait.
  `_guard_client_lock` only wraps `GuardClient.from_config` (no Docker call), cheap.
- Single-agent behaviour: the REPL keeps one `_REPL_LOOP` for the whole session (`runtime/__main__.py:497`), never
  rebuilt, so moving from the global `READ_TRACKER` to a per-loop tracker changes nothing for a user. SIGINT and
  `SESSION_START` unchanged for depth 0 on the main thread. Gate ids unchanged without a prefix.
- Headers: all touched code files carry `claude-opus-5-5` (`LLM:` or `AI models:`), MIT, personal author; no A1.

## Medium

**M1. Guard reader thread can die on a timeout race; then every agent's guard call times out.**
`runtimelib/guard/__init__.py:169-172`: `resp["id"] in self._responses` is checked, then `self._responses[req_id]`
is indexed. If the requesting thread times out between the two, its `finally` (`:213`) pops the id, the index
raises `KeyError`, the unhandled exception ends `_read_loop`, and from then on no response is ever delivered.
Pre-existing, but the doc now declares the client "shared on purpose" and itself predicts timeouts under several
agents (one long `bash` blocks the single-threaded guard), so one agent's timeout can break the guard for all.
Fix:
```python
req_id = resp.get("id")
event = self._responses.get(req_id)
if event is not None:
    self._results[req_id] = resp
    event.set()
```
and in `_send_request`'s `finally` also `self._results.pop(req_id, None)`. Add a test: fake guard that answers
after the client's timeout, then a second request must still succeed.

**M2. `_write_lock` is held across a pipe write that has no timeout.**
`runtimelib/guard/__init__.py:196`. When the guard is busy with a long call it does not read stdin; a large
request (`fs_write` of a big file) fills the pipe and the writer blocks inside the lock. Every other agent then
waits on `_write_lock` with no bound; their `timeout` only covers `event.wait`, so the documented "30 s timeout"
does not fire, they hang until the long call ends. Fix: `if not self._write_lock.acquire(timeout=timeout): raise
TimeoutError(...)` (then `try/finally release`), and say in `process-globals.md` that a writer blocked on a full
pipe is the remaining unbounded case.

**M3. `docs/architecture/process-globals.md` misses some process-wide state.** The six items the design names are
all there; a grep of module-level mutable state and `global` statements finds these not listed:
- `_LAST_CONTEXT`, `_DEFAULT_WINDOW_HINTED` (`runtime/__main__.py:241-242`). `_LAST_CONTEXT` is written by the
  loop's `on_context` callback; if a child ever gets the REPL's callbacks it overwrites the parent's status. Rule
  to write down: children never get the REPL callbacks.
- Logging `ContextVar`s `trace_id_var`, `session_id_var` (`runtimelib/logging/__init__.py:26-27`). A new
  `threading.Thread` starts with an empty context on the GIL build, so a step-4a worker logs without ids. Rule:
  the task runner starts the thread with `contextvars.copy_context().run`.
- `_WARNED_MISSING` (`runtimelib/agent/instructions.py:76`) and `_warned` (`runtimelib/config/paths.py:21`):
  warn-once sets, harmless (double warning at worst), but list them with `_WARNED_ALIAS`.
- `HookEngine.enable/disable` (`runtimelib/hooks/__init__.py:123-127`) flip `_enabled` for every agent.
- Process cwd: `Path.cwd()` is the workspace root for fileops/edit and the guard mount; nothing calls `os.chdir`
  today. Rule: no agent changes cwd (worktrees must pass paths, not chdir).
- Session row: the lock is per `SessionManager` instance. `AgentLoop` builds its own manager when none is passed
  (`runtimelib/agent/loop.py:147`), and two managers on the same history file are not protected. Rule: a child
  always gets the parent's instance (already in design 4.2; say it in the table).

## Minor

1. `tracker or READ_TRACKER` (`runtimelib/tools/edit.py:173, 223, 265`, `runtimelib/tools/fileops.py:80`) and
   `read_tracker or ReadTracker()` (`runtimelib/agent/loop.py:118`) rely on truthiness. Safe today
   (`ReadTracker` has no `__len__`), but an added `__len__` would silently fall back to the shared global for an
   empty tracker, the exact leak step 0 closes. Use `if tracker is None`.
2. `/agent info` (`runtime/commands.py:48`) builds a second depth-0 loop on the main thread: it takes SIGINT
   from `_REPL_LOOP` and fires `SESSION_START` again (and starts MCP servers). Pre-existing, not covered by "only
   the top-level agent". Fix: save `signal.getsignal(SIGINT)` before and restore after, or read the info without
   building a loop.
3. `REX` builds its sub-agents with `create_agent_loop(...)` without `depth` (`runtimelib/rex.py:145`), so they
   are depth 0: SIGINT (when on the main thread) and `SESSION_START`. Pass `depth=agent_context.depth`, or note it
   in process-globals until step 7 retires `REX.spawn_subagent`.
4. `reset_context` (`runtimelib/agent/loop.py:315`) keeps the read tracker. After `/clear` the model no longer has
   the file text but may still edit without re-reading. Now that the tracker is per loop, add
   `self.read_tracker.clear()` there (behaviour change; owner's call, optional).
5. `SessionManager.get_history` (`runtimelib/session/__init__.py:95`) returns the live list, `new_session`
   (`:123`) swaps `_entries` and `session_id` without the lock. No corruption in CPython, but readers see a list
   that grows under them. Return `list(...)` under `_append_lock`; take the lock in `new_session`.
6. `GuardClient.stop` can set `_proc = None` while another thread is between the `if not self._proc` check and
   `self._proc.stdin.write` (`runtimelib/guard/__init__.py:193-198`): `AttributeError` at shutdown. Read
   `proc = self._proc` once and use `proc` throughout.
7. Tests: no behavioural read-isolation test (only `assertIs` on the tracker objects); add "fileops read in loop
   A, edit in loop B is refused". No test for `_guard_client_lock` or for `from_config(id_prefix=...)`. The
   session test discriminates on Windows (verified) but probably not on Linux, where one `write()` with
   `O_APPEND` to a regular file does not interleave; say so in the test docstring. `tempfile.mkdtemp()` dirs are
   never removed; use `TemporaryDirectory` with `addCleanup`.
8. `test-results.txt` in the commit records `git commit: 086fc5e` (the run happened before the commit). Fine as
   a convention, but the file then never names the commit it belongs to; consider "tested on top of" wording.

## Top 3

1. M1: guard reader `KeyError` race kills the shared client for every agent; 5-line fix plus one test.
2. M2: bound the `_write_lock` acquire by the request timeout.
3. M3: add the missing rows (`_LAST_CONTEXT`, logging ContextVars, cwd, session-per-instance rule) to
   `process-globals.md`.

---

Sources: ai-rex commit `14a1a6b` and parent `086fc5e` (git, read 2026-11-24); scratch worktree test run and
mutation runs 2026-11-24; `docs/DESIGN-orchestration.md` rev 2 (2026-11-24).
