<!--
Author: Peter Kokosharov <peter.kokosharov@gmail.com>
AI models: claude-sonnet-5-5, claude-opus-5-5
Licensed under MIT License
-->

# ai-rex: the engine command

`ai-rex` is the small, script-safe command of rex. With no arguments in a terminal it opens the interactive agent
in the current folder (the workspace). With `-i`, `-f` or piped input it answers once and exits. It is configured
by flags and **environment variables only**; it never writes a config file. (`rex`, the future TUI, is a separate app.)
What it does write, precisely, is listed under "Files it reads and writes" below.

**Changed 2026-11-28:** the plain REPL and one-shot runs no longer read `~/.config/ai-rex/config.yaml` or
`~/.config/ai-rex/.env` (and no longer migrate `~/.config/runtime`). Settings that lived there (endpoint, model,
permission rules, hooks, MCP servers, `workspace.trust`, ...) now come from flags and `AI_REX_*` variables, or you
start with `--config ~/.config/ai-rex/config.yaml` to get the old behaviour. `--jsonl`, `--acp`, `ai-rex skills` and
`ai-rex profile` still read the file.

### Files it reads and writes

In the plain REPL and one-shot runs (no `--config`, `--env-file`, `--jsonl`, `--acp`):

- **Written:** the REPL history (`~/.config/ai-rex/history`), the conversation log
  (`~/.config/ai-rex/history.jsonl`, or `AI_REX_SESSION__HISTORY_FILE`), `--session` / `--continue` files, and
  whatever the model writes in the workspace through its tools (after you allow it). Nothing else.
- **"Allow always" answers** last for the running session only. Set `AI_REX_PERMISSIONS_FILE=<path>` to keep them in
  that one file across runs (it is read at start and updated on every "always").
- **Read, never written:** the user-level `~/.config/ai-rex/AGENTS.md` and instruction layers in
  `~/.config/ai-rex/agents/` (see "How the agent is instructed"), the install folder, and the workspace.

`--config`, `--env-file`, `--jsonl` and `--acp` keep the older behaviour: they read the config file, store "allow
always" rules per workspace in `~/.config/ai-rex/permissions/`, and copy an old `~/.config/runtime` folder to
`~/.config/ai-rex` once (the old folder is kept). `ai-rex skills` and `ai-rex profile` read the config file but
write nothing.

## Install and run

From a checkout (no install): `C:\private\rex\ai-rex.cmd` (Windows) or `/path/to/rex/ai-rex` (Linux/macOS).

Portable install into ONE folder (no PATH change, no registry, no shortcut):

```
python scripts\install_ai_rex.py C:\Users\<you>\apps\ai-rex
```

It prints the full launcher path, e.g. `C:\Users\<you>\apps\ai-rex\ai-rex.cmd`. Uninstall: delete that folder, or run
`uninstall.cmd` in it (`python scripts\install_ai_rex.py --uninstall <folder>` also works). With pip:
`pip install .` gives an `ai-rex` console script.

### IntelliJ terminal

Open the terminal tab (cmd, PowerShell or Git Bash), `cd` into the project and run the launcher by its full path
(or add the folder to PATH yourself if you want the bare name). The project folder is the workspace.

```
C:\Users\me\apps\ai-rex\ai-rex.cmd                              # interactive
C:\Users\me\apps\ai-rex\ai-rex.cmd -i "what does this project do?"
git diff | C:\Users\me\apps\ai-rex\ai-rex.cmd -i "review this diff" --no-tools
```

Permission questions (writes, commands) are asked in the terminal (on stderr, so `>` redirection never catches
them). Piped runs, `--json` and `--non-interactive` never ask: what would ask is denied (exit 3).

### Where tools run

The REPL and one-shot runs give the model the full coding tool set (fileops, bash, edit, git, webfetch, websearch,
render_diagram, skills); `--no-tools` turns them off. With a `docker` command on PATH they run in the Docker guard
(see `docs/guard-docker.md`). **Without Docker** (the usual Windows desktop) they run on the host in the workspace
behind the permission gate: reads are allowed, every write or command asks first. The REPL banner says
`tools: local (Docker not found) ...` when this happens. `--local-tools` forces local mode;
`AI_REX_CLI__UNSAFE_LOCAL_TOOLS=false` forces the guard (tool calls then fail without Docker). Do not point it at
an untrusted repository in local mode.

**Which shell runs commands.** The tool is called `bash`, but `--shell` / `AI_REX_SHELL` / config `tools.shell`
pick the shell (flag beats environment, environment beats the config file): `auto` (default), `bash`,
`powershell` or `cmd`. PowerShell gets the command as `-EncodedCommand` with `-NoProfile -NonInteractive`,
UTF-8 output and the native program's exit code; `cmd` runs `cmd.exe /d /s /c`. In Git Bash, `cmd /c ...` and
`cmd.exe /c ...` work (Git Bash would otherwise turn a lone `/c` into `C:/`). `!` commands in the REPL and
`ai-rex "!..."` use the same shell and runner (not scrubbed: it is your own command). Rules for every command:
stdin is empty; the call ends when the shell ends, so a launched program (`start "" app`, `start msedge x.html`,
`xdg-open`, `server &`) keeps running and does not block the agent (its late output is collected for 1 s); a
timeout kills the command's whole process tree (on Windows a job object, so background children are included);
Ctrl+C in the REPL ends a running command at once (exit 130, `[interrupted]`). An unknown `AI_REX_SHELL` /
`tools.shell` value means `auto`; a named shell that is not installed fails the call with a clear message.

## Modes

| Situation | Mode |
|---|---|
| stdin is a terminal, no `-i` / `-f` | interactive REPL |
| `-i`, `-f`, or stdin is a pipe/file | one-shot: plain answer on stdout, exit |
| `--jsonl`, `--acp` | protocol bridges for Layka / Sisi / Lassie / editors (still read the config file) |

## Flags

Order of precedence everywhere: flag > environment variable > default. For the endpoint, highest first:
`--base-url`, `--port`, `AI_REX_BASE_URL` (or `LITELLM_BASE_URL`), `AI_REX_PORT`, then `http://127.0.0.1:4000`; a flag
always beats the environment, so `--port 4001` wins over `AI_REX_BASE_URL`.

| Flag | Meaning | Env fallback | Default |
|---|---|---|---|
| `-i`, `--input TEXT` | prompt text; piped stdin / `-f` content is attached after it | | |
| `-f`, `--input-file PATH` | read the prompt from PATH, `-` = stdin | | |
| `-o`, `--output PATH` | write the answer to PATH, `-` = stdout | | `-` |
| `--append` | append to `--output` instead of overwriting | | off |
| `-m`, `--model NAME` | model name | `AI_REX_MODEL` | `sonnet` |
| `-p`, `--port N` | port of the local gateway on 127.0.0.1 | `AI_REX_PORT` | `4000` |
| `--base-url URL` | gateway URL, wins over `--port` | `AI_REX_BASE_URL`, `LITELLM_BASE_URL` | `http://127.0.0.1:<port>` |
| `--provider P` | `auto`, `openai`, `anthropic`, `gemini` | `AI_REX_PROVIDER` | `auto` |
| `--api-key-env NAME` | name of the env var that holds the key (a key value is never a flag) | | |
| `--system TEXT` / `@FILE` | extra system instructions | | |
| `--profile LAYERS` | instruction layers, e.g. `programming/java` (repeatable or comma-separated; `auto`, `base`), see "How the agent is instructed" | `AI_REX_PROFILE` | `auto` (detected from the workspace) |
| `--max-tokens N` | output token limit per model reply | `AI_REX_MAX_TOKENS` (native Anthropic adapter) | Anthropic native: 32000 for Sonnet/Opus, 8192 otherwise; else the endpoint's default |
| `--temperature X` | sampling temperature | | provider default |
| `--timeout S` | HTTP timeout in seconds (per read; one agent step through a gateway can take minutes) | `AI_REX_TIMEOUT` | `600` |
| `--no-tools` | plain chat, the model gets no tools | | tools on |
| `--shell S` | shell for the `bash` tool and `!` commands: `auto`, `bash`, `powershell`, `cmd`. `auto` = Git Bash on Windows (PATH, then the Git for Windows folders), else PowerShell (`pwsh`, then Windows PowerShell 5.1), else `cmd.exe`; bash/sh on Linux and macOS. The banner, `/context`, the tool description and every result name the shell, so the model writes its syntax | `AI_REX_SHELL`, config `tools.shell` | `auto` |
| `--mcp-config PATH` | MCP servers from a JSON file (repeatable), see "MCP: using other tools" | `AI_REX_MCP_CONFIG` (paths, separated by `;` on Windows, `:` elsewhere) | |
| `--mcp NAME=COMMAND` | one inline MCP server (repeatable) | | |
| `--no-mcp` | ignore every MCP server, also those from the environment or `--config` | | |
| `--mcp-allow PATTERN` | run these MCP tools without asking (repeatable) | | ask |
| `--mcp-trust-readonly` | run MCP tools the server marks `readOnlyHint` without asking (sets `mcp.trust_readonly`) | `AI_REX_MCP__TRUST_READONLY=true`, config `mcp.trust_readonly` (`true`/`1`/`yes`/`on`) | ask |
| `--non-interactive` | never ask; a call that needs permission is denied | | asks if a terminal is attached |
| `--session ID` | one-shot: keep the conversation in session `ID` (created if new; letters, digits, `-`, `_`) | | |
| `--continue` | one-shot: continue the latest session started in this folder (new one if none) | | |
| `--local-tools` | run tools on the host behind the permission gate (automatic without Docker) | | |
| `--cwd`, `--workspace DIR` | run in DIR | | current folder |
| `--json` | one JSON object: `ok, model, response, tools, denied, error, session, seconds` | | plain text |
| `--stream` / `--no-stream` | print as it arrives / only when complete | | stream |
| `-v` / `-vv` | log tool calls / debug (endpoint, model) | | warnings |
| `-q`, `--quiet` | log errors only | | |
| `--log-file PATH` | logs to PATH instead of stderr | | stderr |
| `--log-format text\|json` | log line format | | `text` |
| `--version`, `-h` | version, help | | |

Logs never mix with the answer: the answer goes to stdout (or `-o`), logs to stderr (or `--log-file`).

Text handling: input (`-f`, stdin) is read as UTF-8 (a BOM is dropped). A `-f` file that is not UTF-8 is a usage
error (exit 2); stdin bytes that are not UTF-8 become U+FFFD. The answer is written as UTF-8 with `\n` (LF) line
ends, also on a Windows cp1252/cp1251 console and also to stdout. The `-o` file is opened on the first write, so a run
that fails before answering neither creates nor truncates it. A positional prompt (`ai-rex "question"`) uses the
older chat path and does not read stdin: use `-i` to attach piped text.

### Exit codes

`0` ok, `1` model or runtime error (one line on stderr, no traceback), `2` usage error (nothing to do, unreadable
file, unknown provider, missing key), `3` a tool call was denied by the permission rules (the answer is still
printed), `130` interrupted.

### Environment variables

| Variable | Use |
|---|---|
| `AI_REX_MODEL`, `AI_REX_PORT`, `AI_REX_BASE_URL`, `AI_REX_PROVIDER`, `AI_REX_TIMEOUT` | the fallbacks in the table above |
| `AI_REX_API_KEY` | key for any provider |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` | provider-specific keys (used when `AI_REX_API_KEY` is unset) |
| `LITELLM_BASE_URL`, `LITELLM_API_KEY` | older names, still honoured as fallbacks |
| `AI_REX_PROFILE` | instruction layers when `--profile` is not given |
| `AI_REX_MAX_TOKENS` | output token limit of the native Anthropic adapter when `--max-tokens` is not given |
| `AI_REX_PERMISSIONS_FILE` | file that keeps "allow always" answers across runs (default: this session only) |
| `AI_REX_<SECTION>__<KEY>` | any engine setting, e.g. `AI_REX_AGENT__MAX_STEPS=30`, `AI_REX_SESSION__DIR=D:\sessions` |

Keys are read only from the environment and are never printed or logged. Local kiko needs no key.

## How the agent is instructed

The system prompt is built from layers, general to specific: a base layer (work loop explore -> plan -> act ->
verify -> report, keep going until done, honesty, ask before destructive actions, answer in the user's
language), then `programming` (exploring code, using each tool, builds, tests, review report format), then a
stack leaf such as `programming/java` or `programming/python`. Layers are detected from the workspace root
(`pom.xml` -> Java, `pyproject.toml` -> Python, ...), chosen with `--profile` / `AI_REX_PROFILE` / an
`ai-rex-profile:` line in AGENTS.md, and you can add or override layers in `~/.config/ai-rex/agents/`, the install
folder's `agents/` or a trusted project's `.ai-rex/agents/`. `ai-rex profile` shows the tree and what is active,
`ai-rex profile show` the composed text. Details: [AGENT-PROFILES.md](AGENT-PROFILES.md).

On top come the skill index (built-in task skills `review-project`, `java-maven`, `python-project`,
`code-review`, `write-tests`, `debug`, `commit-pr`; `ai-rex skills` lists them) and AGENTS.md (template:
[AGENTS-TEMPLATE.md](AGENTS-TEMPLATE.md)).

What the model sees from tools: plain text, not JSON. Every result starts with one header line written by the
loop, `[result of fileops call <id>: ok; untrusted content below]` or `[result of ... : failed]`, so file or web
content that imitates `ERROR: ...` or `exit code: 0` cannot fake the real status. `fileops read` returns numbered
lines (`  12<TAB>code`), 2000 lines per call, `offset`/`limit` for ranges and a "read again with offset=N" note on
longer files (at most the first 16 MB of a file are read); a failed call reads `ERROR: ...`; shell results show
the exit code, stdout and stderr. `list`, `glob` and `grep` skip cache folders at any depth (`.git`,
`node_modules`, `__pycache__`, `.venv`, `.idea`, `.gradle`, `.mypy_cache`, `.pytest_cache`, ...) and build output
(`target`, `build`, `dist`) only at the workspace root or next to `pom.xml`, `build.gradle`, `package.json`,
`pyproject.toml` or `setup.py` (so `src/build/` stays visible); the result ends with
`(N entries in skipped folders hidden ...)`, and a skipped folder given as the path is listed normally. `grep` with
a file as the path searches that file.

Safety net: a reply whose tail is a tool call written as text (`{"tool_calls"...`, `<invoke ...>` starting a line,
outside a code block, with nothing but markup after it), or an empty reply right after tool results, is not taken
as the answer; the agent is told to continue (at most twice), and such text is held back while streaming. If it
still comes back after two reminders, it is shown with a warning instead of being lost. An answer that quotes the
markup and then goes on in prose (explaining the protocol) is a normal answer. A reply cut off at the output token
limit is reported (`response truncated ...` on stderr) and continued once: a half-written tool call is dropped and
the model is told to write large files in parts; cut-off text is continued and joined.

## MCP: using other tools

ai-rex speaks the Model Context Protocol (MCP) as a client, so it can use tools that other people wrote: a Jira
connector, a database reader, a company tool. A server is any program that talks MCP over stdin/stdout, or an
HTTP(S) endpoint (streamable HTTP). You never need Node; use whatever command the tool's author gives you.

**A config file** in the common `mcpServers` format (the same shape Claude Code and other clients use):

```
{"mcpServers": {
  "tickets": {"command": "python", "args": ["C:/tools/tickets_mcp.py"], "env": {"TICKET_URL": "https://t.example"}},
  "wiki":    {"type": "http", "url": "https://wiki.example/mcp", "headers": {"Authorization": "Bearer ${WIKI_TOKEN}"}}
}}
```

```
ai-rex --mcp-config C:\work\mcp.json -i "which tickets are open for me?"
ai-rex --mcp-config a.json --mcp-config b.json            # several files, later ones win per server name
```

`"type": "stdio"` is accepted (it is the default), `"type": "http"` or a plain `"url"` selects streamable HTTP.
`"disabled": true` skips a server. `${VAR}` and `${VAR:-default}` inside strings are filled from the environment
(keep tokens there, not in the file). Optional per server: `"timeout"` (seconds per request, default 60) and
`"startupTimeout"` (seconds for the handshake, default 5, or set `AI_REX_MCP_STARTUP_TIMEOUT` for all). SSE and
WebSocket transports are not supported and give a clear error.

**Inline**, no file: `--mcp NAME=COMMAND [ARGS...]` as one quoted word; it is split like a shell would but run
without a shell (no pipes, no `;`, no expansion):

```
ai-rex --mcp "tickets=python C:/tools/tickets_mcp.py --readonly" -i "list my tickets"
ai-rex --mcp "wiki=https://wiki.example/mcp" -i "find the deployment page"
```

**Environment:** `AI_REX_MCP_CONFIG` holds one or more config paths (separated by `;` on Windows, `:` elsewhere).
Order, later wins per server name: servers from `--config`, the environment, `--mcp-config`, `--mcp`.
`--no-mcp` ignores all of them. A wrong `--mcp-config` path or a malformed `--mcp` is a usage error (exit 2); a bad
path in the environment variable only prints a warning.

**Names.** A tool `ping` of server `tickets` is called `mcp__tickets__ping`. Server names use letters, digits, `-`
and single `_` inside the name: no `__` and no `_` at either end (so `--mcp-allow x` can never cover a server `x_`).
The same check applies to `--mcp`, config files and `--mcp-allow` patterns; a bad name is a usage error.

**Permissions.** MCP tools go through the same gate as the built-in ones. Without a rule every MCP call asks
(denied under `--non-interactive`), also tools the server marks `readOnlyHint`: that hint is the server's own
claim, so it is trusted only with `--mcp-trust-readonly` (or `AI_REX_MCP__TRUST_READONLY=true`); then such tools run
without asking (a `deny` rule still wins). Allow more with `--mcp-allow`: `--mcp-allow tickets` (all tools of the server),
`--mcp-allow tickets__create_*` (a glob) or a full name. Rules in a `--config` file work as well, for example
`permissions.deny: ["mcp__tickets__delete_*"]`. Use `--mcp-trust-readonly` only with servers you trust. Output of an MCP tool is treated as untrusted data: control and bidi characters are removed and it is cut at
100,000 characters; it is never executed. Tool descriptions from a server are cleaned the same way and cut at 2000
characters (they go into the system prompt). HTTP servers: redirects are refused (urllib would send the configured
`Authorization` header to the new host; configure the final URL) and a reply over 8 MB is refused. An unset `${VAR}`
without a default prints one warning and becomes an empty value.

**Lifecycle.** Servers start when the agent starts working (not while flags are parsed), one after the other, and
are stopped at exit (the whole process tree on Windows). A server that is missing, slow or crashing prints one line
to stderr, for example `ai-rex: MCP server 'wiki' skipped: ... timed out after 5s`, and the session goes on without
it. `--no-tools` removes the built-in tools only; MCP servers you named explicitly stay available.

**REPL.** `/mcp` lists the configured servers, whether they are connected, and their tools (`[read-only]` marks
the server-annotated ones). Before the first message the servers are not started yet.

Not supported: MCP resources and prompts (tools only), SSE/WebSocket transports, OAuth login to remote servers
(pass a token in `headers` instead).

## Providers

The engine's internal shape is the OpenAI chat shape; adapters map it.

| Provider | Wire format | Covers |
|---|---|---|
| `openai` (default) | `/v1/chat/completions`, SSE streaming, tool calls | kiko, LiteLLM proxy, OpenAI, NVIDIA NIM, vLLM, Ollama, llama.cpp, OpenRouter |
| `anthropic` | native `/v1/messages` (x-api-key, content blocks, tool_use/tool_result) | api.anthropic.com |
| `gemini` | native `streamGenerateContent` (functionDeclarations/functionCall) | generativelanguage.googleapis.com |

Decision: kiko serves Anthropic models (`opus`, `sonnet`, `haiku`) over the OpenAI-compatible API, so the native
Anthropic path is **not needed for kiko**; it exists for people calling Anthropic directly. `auto` picks
`anthropic` / `gemini` only when the base URL host is `anthropic.com` / `googleapis.com`, otherwise `openai`.
Native providers default to the public URL and the models `claude-sonnet-5-5` (Anthropic) / `gemini-3.8-flash`
(Gemini, stable per the Gemini model list of 2026-11-28; `gemini-2.5-*` are deprecated). `-m` picks another one.
The REPL uses the same adapters as one-shot runs. The Gemini adapter sends the key only in `x-goog-api-key`, echoes
each functionCall's `thoughtSignature` (required by Gemini 3) and Gemini's own call ids, and URL-quotes the model.
Its schema conversion drops keywords Gemini rejects (`additionalProperties`, `default`, `examples`, `$schema`, ...)
but never a parameter that happens to have such a name, inlines local `$ref`s, turns `["string", "null"]` into
`string` + `nullable`, and keeps `required` to parameters that exist. Both native adapters refuse HTTP redirects
(urllib would resend the key to the new host; use the final URL as base URL) and stop a reply over 64 MB. The
Anthropic adapter asks for 32000 output tokens for Sonnet/Opus models (8192 for others) unless `--max-tokens` /
`AI_REX_MAX_TOKENS` / `liteLLM.max_tokens` says otherwise. The provider name is case-insensitive (`Anthropic` works);
an unknown name is a usage error, also from a `--config` file, and a config file without a base URL, key or model
gets the same defaults as the environment path (public URL, `ANTHROPIC_API_KEY` / `GEMINI_API_KEY`, default model).

**Claude CLI gateway and your CLAUDE.md.** When ai-rex talks to `scripts/claude_cli_gateway.py`, the gateway runs
the `claude` CLI. If that CLI offers `--safe-mode`, the gateway uses it and no user customization is loaded. If it
offers only `--setting-sources` (no `--safe-mode`), your user-level `~/.claude/CLAUDE.md` and user settings are
loaded into **every** request (the gateway prints a note at startup); keep private notes out of that file or use a
CLI with `--safe-mode`. How the gateway emulates tool calls (retry, bare JSON answers, quoted markup, untrusted
text, model names) is described in the README, section on `scripts/claude_cli_gateway.py`.

```
# local kiko (default: http://127.0.0.1:4000, model sonnet)
ai-rex -i "say OK" -m haiku

# LiteLLM proxy elsewhere
set AI_REX_BASE_URL=http://litellm.example:4000 & set AI_REX_API_KEY=sk-...  & ai-rex -i "hi" -m gpt-4o

# OpenAI directly
set OPENAI_API_KEY=sk-... & ai-rex --base-url https://api.openai.com -m gpt-4o -i "hi"

# Anthropic native
set ANTHROPIC_API_KEY=sk-ant-... & ai-rex --provider anthropic -m <model> -i "hi"

# Gemini native, key in a custom variable
set MY_GEMINI=... & ai-rex --provider gemini --api-key-env MY_GEMINI -m <model> -i "hi"

# pipes and files
type notes.txt | ai-rex -i "summarise in 3 bullets" --no-tools -o summary.md
ai-rex -f prompt.txt --json --non-interactive
```

(Windows `cmd` syntax; in Git Bash use `VAR=value ai-rex ...`.)

## Not done yet

`--session` / `--continue` in the interactive REPL (one-shot only for now), `--allow` / `--deny` rule shortcuts
(use `--config` with permission rules for now),
and the recorded-fixture contract tests use hand-written fixtures in the documented wire shape rather than captures
from the live services.
