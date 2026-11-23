# rex handoff — Roni

Date: 2026-11-23. Status: note only, no code changes in this commit.

## 1. The shape we are building

There are three layers, and they do not share state by accident:

1. **Workspace** (`ai-first-workspace`) owns one inventory DB: machines, services,
   providers, keys, models, tools, projects (`data/schema.sql`, schema v4).
   It owns the rules (`AGENTS.md`, with `CLAUDE.md` / `GEMINI.md` as pointers) and
   the stdlib tools (`tools/secret.py`, `tools/inventory.py`, ...).
2. **Plugins / runtimes** (rex, goose, Claude Code, Gemini CLI, codex, opencode)
   own no inventory. They run inside the workspace folder, read the same rules,
   and point at the same kiko gateway. Adding a runtime must not add a new
   protocol to the workspace.
3. **Attached projects** (`projects/<name>/`, each its own git repo) own their
   own inventory DB and a `project.properties` file with `service:key` entries,
   e.g. `github:/peterk/GITHUB_TOKEN`, `jira:/peter/jira_token`. The file holds
   **names only**; values live in the OS keyring.

rex is one of the runtimes. It should fit this contract instead of growing its
own version of it.

## 2. Contract every runtime must satisfy

- Reads instructions from `cwd` (`AGENTS.md`, project-local rules).
- Reads `project.properties` from `cwd` and resolves `service:key` to a keyring
  **name**, never to a value in the agent's context.
- Sends LLM traffic to kiko (`http://127.0.0.1:4000`) with a **dummy** key; the
  real key stays in the gateway / OS keyring.
- Uses relative paths in tools; absolute paths are rejected by the guard.
- Cannot push to git without an explicit human confirmation.
- Roles are capabilities, not wording: an `orc` role physically has no
  `bash` or write tools, even if the prompt says so.

## 3. What rex already has — do not redo

- JSONL bridge (`rex --jsonl`) and ACP bridge (`rex acp`), driven by the same
  agent loop. ACP negotiates `initialize` / `session/new` correctly.
- Guard client with relative-path checks; `network_mode=none`; memory/CPU limits.
- `git` tool enum has **no** `push` — correct, keep it that way.
- `! command` in the CLI runs on the host, outside the LLM and outside guard.
  rex's own `bash` tool runs in Docker guard, which has no JDK/Maven. Do not
  "fix" that by giving guard the host toolchain.
- `runtimelib/permissions/` has `PermissionPolicy`,
  `PermissionCheckingExecutor`, and `REX` has `roles.<ROLE>.allowed_tools`
  (`runtimelib/rex.py:243-245`). The intent is right; the wiring is not.
- Tests run: `75 passed, 1 skipped` with pytest on Python 3.12.

## 4. Gaps, in priority order

### P0 — origin rex does not read workspace rules

`runtimelib/agent/loop.py` has a **local, uncommitted** change that loads
`AGENTS.md` and `ARCHIVE.md` from `Path.cwd()` into the system prompt, plus a
test fix in `tests/test_agent_loop_v2.py`. It is not in origin. This note does
not include it.

Acceptance: a test creates a temp `AGENTS.md`, starts a loop there, and asserts
the text is in the system prompt; an ACP session started with that `cwd` gets
the same rules. Keep the existing isolation test (`monkeypatch.chdir(tmp_path)`)
so the repo's own `AGENTS.md` does not leak into unrelated prompt tests.

### P0 — no `project.properties` resolver

Nothing in rex reads `service:key` entries. rex's vendored
`runtimelib/kikolib/keystore.py:163-173` uses the old kiko shape:
`kiko-identity=<UPPERCASE>` and `label=kiko <UPPERCASE>`. Our keyring entries
use `service=/peterk`, `label=/peterk/<KEY>`, `key=<KEY>`,
`username=$(id -un)`. The two tools cannot see each other's entries.

One naming rule must be chosen before coding: either `/peterk/<KEY>` for
everything, or `/peterk/<PROJECT>/<KEY>` with the project as a segment. The two
examples in the brief currently use `/peterk/` and `/peter/` — pick one.

Acceptance: `project.properties` with names only; resolver finds the keyring
entry by the kiko attributes; no command prints, logs or injects a value into
the agent's environment. Clients keep using a dummy key.

### P0 — the orchestrator is a skeleton

`runtimelib/rex.py` has `REX.run_agent()` and `REX.spawn_subagent()`, but:

- `_create_tools()` (`rex.py:247-256`) pulls from the global `ToolRegistry`,
  and nothing in the repo registers tools into it. It returns an empty list.
- `TaskSpawnTool.execute()` (`tools/taskspawn.py:50-60`) does not spawn
  anything; it returns `{"status": "delegated", "note": "..."}`.
- There is no test referencing `REX`, `PermissionCheckingExecutor`, or
  `spawn_subagent`.

So "orc cannot write files or run bash" is currently a statement in code, not
an enforced boundary. CLI and ACP both use `create_agent_loop()` with all five
tools (`fileops`, `bash`, `task`, `websearch`, `git`) and role `CODER`.

Acceptance: tool factories registered; `spawn` actually starts a child with an
allowlist; role tool sets applied in CLI and ACP; a test proves a role with no
`fileops` cannot write a file even when the prompt asks for it.

### P1 — guard has no per-role profile

`docker_guard` config (`runtimelib/config/schema.py`) has image, memory, CPU,
network and timeout only. Needed: readonly mount, writable path allowlist and
command allowlist selected at container start, per role. Also
`import fcntl` in `runtimelib/guard/sandbox.py:12` blocks Windows.

### P1 — portability

`REX_ROOT=$HOME/tools/rex` is a local symlink. The `v0.2.2` release assets
(`rex_0.2.2_amd64.deb`, `rex-win.zip`) are not yet verified. The workspace
README lists opencode, Gemini, goose, Claude and codex, but not rex. Add rex to
the runtime matrix and run one identical smoke test through each runtime.

### P2 — ACP gaps

`loadSession: false`, no image/audio prompts, no MCP passthrough, no permission
gating surfaced to the client, no cost reporting. Fine for IntelliJ today;
needed if "works everywhere" includes resume across runtimes.

## 5. Non-goals

- No universal TUI, no PTY/ANSI emulation to swallow other CLIs.
- No Kircho shim — compatibility only.
- No rewrite in C++ or Rust now; Layka stays C++ Turbo Vision, rex stays Python.
- No secrets in files, no secrets in agent env, no push without confirmation.

## 6. Where to start

Commit the P0 loop change with a positive test, then wire role-based tools into
CLI/ACP, then add `project.properties`. Guard profiles and portability come
after the boundary is real.
