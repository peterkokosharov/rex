<!--
Author: Peter Kokosharov <peter.kokosharov@gmail.com>
AI models: claude-sonnet-5-5, claude-opus-5-5
Licensed under MIT License
-->

# ai-rex: the engine command

`ai-rex` is the small, script-safe command of rex. With no arguments in a terminal it opens the interactive agent
in the current folder (the workspace). With `-i`, `-f` or piped input it answers once and exits. It is configured
by flags and **environment variables only**; it never writes a config file. (`rex`, the future TUI, is a separate app.)

**Changed 2026-11-28:** the plain REPL and one-shot runs no longer read `~/.config/ai-rex/config.yaml` or
`~/.config/ai-rex/.env` (and no longer migrate `~/.config/runtime`). Settings that lived there (endpoint, model,
permission rules, hooks, MCP servers, `workspace.trust`, ...) now come from flags and `AI_REX_*` variables, or you
start with `--config ~/.config/ai-rex/config.yaml` to get the old behaviour. `--jsonl`, `--acp` and `ai-rex skills`
still read the file. The REPL history (`~/.config/ai-rex/history`, `history.jsonl`) and `--session` files are data,
not configuration, and are still written.

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

## Modes

| Situation | Mode |
|---|---|
| stdin is a terminal, no `-i` / `-f` | interactive REPL |
| `-i`, `-f`, or stdin is a pipe/file | one-shot: plain answer on stdout, exit |
| `--jsonl`, `--acp` | protocol bridges for Layka / Sisi / Lassie / editors (still read the config file) |

## Flags

Order of precedence everywhere: flag > environment variable > default.

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
| `--max-tokens N`, `--temperature X` | sampling limits | | provider default |
| `--timeout S` | HTTP timeout in seconds | `AI_REX_TIMEOUT` | `60` |
| `--no-tools` | plain chat, the model gets no tools | | tools on |
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
error (exit 2); stdin bytes that are not UTF-8 become U+FFFD. The answer is written as UTF-8 with `
` line ends,
also on a Windows cp1252/cp1251 console and also to stdout. The `-o` file is opened on the first write, so a run
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
| `AI_REX_<SECTION>__<KEY>` | any engine setting, e.g. `AI_REX_AGENT__MAX_STEPS=30`, `AI_REX_SESSION__DIR=D:\sessions` |

Keys are read only from the environment and are never printed or logged. Local kiko needs no key.

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
