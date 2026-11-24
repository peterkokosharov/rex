<!--
Author: Peter Kokosharov <peter.kokosharov@gmail.com>
AI models: claude-fable-5-2
Licensed under MIT License
-->

# Review: DESIGN-orchestration.md (sub-agent tasks, tasks panel, approval modes)

Date: 2026-11-24. Reviewer: independent (did not write the design). Method: the design was read in full and its
claims were checked against the ai-rex sources (`loop.py`, `gate.py`, `rules.py`, `jsonl_bridge.py`,
`session/__init__.py`, `tools/edit.py`, `tools/bash.py`, `guard/__init__.py`, `guard/entrypoint.py`,
`config/config.py`, `scripts/claude_cli_gateway.py`). Not checked: the three front-end repositories (outside my
read scope), `rex.py`, `acp/server.py`, `taskspawn.py`. Nothing was run.

## Verdict: READY WITH FIXES

Step 0 can start now. Step 1 (approval modes) must not start before B1 and B2 are fixed in the document; step 3
not before M1-M3. The default (`ask`) is safe; the holes are in `auto`/`full` and in the spec of the table.

Counts: 2 Blocker, 8 Major, 9 Minor.

## Blockers

**B1. `auto` can be escalated by a crafted `bash` call (section 5.1, 5.2).** The design relaxes a verdict "only
when `source == "default"`" and says `unsafe` is never relaxed. In the code the `unsafe` source is produced only
when the decision would otherwise be ALLOW (`rules.py:1201-1208`). In local and sandbox mode the default for exec
is already ASK, so a command with pipes, a too-deep nesting, or **extra `env`** leaves `evaluate` with
`source == "default"`. Then `{"command":"ls","env":{"PATH":"."}}` or `git diff` with `GIT_EXTERNAL_DIFF` passes
`allow_blocker`, matches `auto_exec` and runs without a question. The default list itself is also unsafe:
`find . -delete` and `find -fprint f` consist of safe characters; `git diff --output=f` writes a file; `rg --pre`
runs a program; `pytest`, `make`, `ctest`, `mvn` run code from files the same mode lets the agent write silently;
`cat`/`head` accept absolute paths outside the workspace, and `auto` also allows network, so "read a secret, then
webfetch it away" needs no question.
Fix: (1) `evaluate` returns the unsafe reason as its own field on every verdict (not only on ALLOW);
`apply_approval` never relaxes a verdict with that field set, in any mode on the host. (2) Default `auto_exec` =
`ls cat head tail wc grep git status git diff git log` only; any argument that is absolute, contains `..`, or is
an option from a small deny list (`--output`, `--pre`, `-delete`, `-exec`, `-fprint*`, `--ext-diff`) asks. Test
runners enter through the user's own `allow_always`, not the default. (3) Network stays `ask` in `auto`; the
existing `webfetch(domain:...)` allow rules cover the convenient case.

**B2. The table in 5.2 does not match the code it sits on, so step 1 cannot be implemented or tested from it.**
(a) In guard mode the default for write/exec is ALLOW (`mode_default`, `rules.py:1011`), and the mode logic only
touches ASK verdicts: in guard the `ask` column never asks for writes, and "a write to a protected path always
asks" is false. (b) `classify` returns WRITE for every unknown tool (`rules.py:896`), so `task`, `task_wait` and
`task_cancel` ask on the host in every mode, contradicting the last two rows. (c) "write outside the workspace"
cannot happen: `fileops`/`edit`/`bash cwd` already refuse it (`resolve_in_workspace`), the row is dead.
(d) `full` in local **denies** protected writes while `ask` asks: the widest mode is the strictest.
Fix: one table per tool mode (three small tables), each cell stating today's verdict and the new one; state
plainly "in guard, approval mode changes only network and MCP" (or decide that protected paths ask there too and
list it as a behaviour change). Add `task*` to `classify` (`task_wait`/`task_cancel` read; `task` by `access`).
Delete the outside-workspace row. Protected writes: ask in every mode.

## Major

**M1. SIGINT fix is not enough (section 2, 8, step 0).** "Install only on the main thread" still lets a child
built on the main thread (foreground case) replace the parent's handler; today the last constructed loop owns
Ctrl+C (`loop.py:164`). Fix: install only when `depth == 0` and on the main thread; the same flag skips the
`SESSION_START` hook for children.

**M2. Cancel can be lost (section 4.6).** `_run_request` clears the interrupt event on entry (`loop.py:340`). A
cancel that arrives between `TaskManager.start` and the child's `run` (queued task, slow start) is erased and the
task runs to the end. Fix: `TaskRunner` owns a `cancelled` flag, checked before `run`, and re-applies
`interrupt()` after the loop has started; the task state comes from that flag and `state.interrupted`, never from
the returned text (the loop returns a normal string for "interrupted" and "max steps", `loop.py:397-408`).

**M3. Foreground wait on Windows (section 4.4).** A main thread blocked in `Event.wait()`/`join()` without a
timeout does not run Python signal handlers on Windows, so Ctrl+C in the REPL would be dead during a foreground
task. Simplest fix, also smaller: run a **foreground child inline on the caller's thread** (no thread at all in
step 3); `AgentLoop.interrupt()` forwards to a registered running child. Threads then enter only with step 4.

**M4. Depth limit is by name filter only (section 4.2).** `tool_names` are globs (`loop.py:190`); `tools: ["*"]`
or `["t*"]` matches `task`. "Minus `task*`" must be applied to resolved names, and better: register the three task
tools only when `depth == 0`, and `TaskManager.start` refuses a caller with `depth > 0`. Also expand the child's
globs against the parent's actual tool dict; give children no MCP tools in v1 (a glob `mcp__*` would start a
second set of MCP server processes per child, `loop.py:215-222`).

**M5. Guard client claim is wrong (section 2, "serialised, not broken").** The lock covers only the request id
(`guard/__init__.py:176-180`); `stdin.write` + `flush` run unlocked, so two threads can interleave bytes of two
JSON lines. The server is one sequential loop (`guard/entrypoint.py:106`): a 10-minute `bash` in one agent makes
the other agent's `fs_read` fail with its 30 s timeout. Fix in step 0: a write lock around write+flush; before
step 4 either count the wait in the timeout or document "one long guard call blocks all agents" and test it.

**M6. Mode logic vs hooks (section 5.1).** A hook `ask` sets the decision to ASK while `verdict.source` stays
`default` (`gate.py:169-170`); applied as written, the mode would relax a hook's ask. Fix: `apply_approval` runs
on the rule verdict **before** hooks; a hook ask afterwards always reaches the asker. Add the test.

**M7. Protocol 2 is not backward compatible as claimed (section 7).** An old front-end ignores the unknown `task`
field and prints a child's `tool_start`/`tool_end` in the main transcript; `tool_end` has no call id, so with
parallel children start/end cannot be paired; `permission` and task events arrive after `final`, outside a
request, which v1 never did. ai-rex cannot know what the UI understands: `ready.protocol` is one-way. Fix: the UI
opts in (`{"type":"hello","protocol":2}` or a `--protocol 2` start option); without it no background tasks and no
out-of-request events. Drop child `tool_start`/`tool_end` from the stream entirely (`task_update.last` already
carries the gist). Add a test that a v1 session's event stream is unchanged.

**M8. `stop` and the shared asker (section 4.3, 5.4).** Today `stop` calls `deny_all()` and the asker's interrupt
check looks only at the parent loop (`jsonl_bridge.py:76-81`, `:259`). As designed, background tasks survive
`stop`, so the asker must keep the owner per question (deny by owner; interrupt check on the asking loop). Also
share **one** `PermissionRules` object between parent and children (with a lock in `add_allow_always`): with a
gate "built from the same config" each has its own in-memory allow list and an `allow_always` given to one is
asked again by the other.

## Minor

1. Section 5.2: protected-path matching is lexical (`normalize_path` does not resolve symlinks: confirmed, mark
   it V). Check the resolved path too, and casefold on Windows (`.GIT/`, `Agents.md`). Add `.gitattributes`.
2. Section 5.2: `git commit/checkout/branch` are "writes inside the workspace" and become silent in `edits`.
   Keep `edits` to `edit`, `fileops write/edit`, `render_diagram`.
3. Section 4.5: the inbox notice is a **user-role** message holding 2 KB of child text: higher authority than a
   tool result. Send one line without result text (`task t-3 done; call task_wait`).
4. Section 8: `fileops.py` also takes the tracker (`loop.py:194` passes none); list it. `gate.py:186-188` is now
   `:181-183`.
5. Section 5.4: a reply that crosses `permission_closed` gets `error: unknown permission id` today; make it silent.
6. Section 7.2: reader-thread handlers that `emit` (256 KB `task_result`) block on a slow pipe and then `stop` is
   not read. One writer thread with a queue; the reader never writes.
7. Section 4.3/11: the gateway waits 300 s for a slot, the LLM client times out after 60 s and retries
   (`client.py:146`, `:322`); abandoned calls keep slots. Default `max_parallel` 2; a 503/timeout in a child is
   `failed`, never retried silently.
8. Section 5.4: the model chooses the task title shown in the approval dialog. The dialog's main line must be the
   gate's own summary; the title is a secondary label.
9. Section 5.3: `~/.config/ai-rex/**` (config, allow store) belongs in the protected list.

## Cut (over-engineering)

- `queued` state and the queue: at the cap return an error result, the model waits. Removes cancel-while-queued.
- `lost` state, `waiting` for `task_wait`, the free `agent` label, `task_get`/`task_result` with a 50-line log,
  `tasks` snapshot (only Lassie can reconnect; add when Lassie needs it), `tasks.max_tokens`, `pending` counter.
- `REX.spawn_subagent` reimplementation: deprecate, do not rebuild.

## Steps 0-7

Order is right (approval before tasks; foreground before background). Step 0, 2, 6, 7 are small. Step 1 is small
only after B2. Step 3 becomes small with M3 (inline, no threads). **Step 4 is three steps**: 4a background start
+ `task_end` + cap + notice; 4b `task_wait`/`task_cancel` + UI `task_stop`; 4c `task_update` throttle and panel
fields. Step 0 should end with a written list of process globals and the decision for each (hook engine,
`READ_TRACKER`, guard client, config, MCP manager, registry), not "audit".

## Missing tests

Crafted calls in `auto` (env, absolute path, `find -delete`, `--output`); hook ask not relaxed; symlink and case
variants of protected paths; child with `tools:["*"]` gets no task tool; cancel before the child starts; cancel
while a question is open (exactly one `permission_closed`); mode switched while a question is open; `stop` leaves
a background question open; `bye` with a child stuck in a 300 s LLM call exits in bounded time; two threads
through the guard client; 200 concurrent session appends give 200 valid lines; v1 stream unchanged without
`hello`; fixtures generated by the bridge tests (regenerate flag), never edited by hand.

## Owner questions

Only question 2 is an owner question (risk he carries); widen it to "may `auto`/`full` run project code on the
host without a question". Questions 1, 3, 4, 5 are technical and already answered by the design's own reasoning:
decide them (read-only background, start in `ask`, cap from the gateway, depth 1) and report in one line.
Missing owner questions (scope and priority): stop after step 3 and use it for a while before building
background tasks (yes/no); which front-end gets the panel first (1 Layka, 2 Sisi, 3 Lassie).

## What is good

- Section 2 is honest and almost all line references hold; V/A marking makes the review cheap.
- Reusing the loop, gate and bridge instead of processes or asyncio is the right size for this project.
- Read-only background tasks and depth 1 remove the hardest problems instead of solving them.
- Deny rules, ask rules and hooks stay above every mode; `approved` reason is recorded per call.
- Shared protocol fixtures for three front-ends is the cheapest way to keep them identical.

---

Sources: `docs/DESIGN-orchestration.md` (2026-11-24); ai-rex working tree read on 2026-11-24 (files listed at the
top; design refers to commit `5592aa3`, the tree itself was not compared with that commit).
