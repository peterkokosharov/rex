# Rename: rex -> ai-rex

Why: Debian/Ubuntu ship the Perl deployment tool Rex as package `rex` (`/usr/bin/rex`), and Homebrew has a formula
`rex`. Everything this project installs or that a user types is now `ai-rex`. Prose may still say "rex" for the project.

## What changed (old -> new)

| What | Old | New |
|------|-----|-----|
| Command (deb, pip, Windows) | `rex` (deb), `runtime` (pip) | `ai-rex` |
| Python distribution | `runtime` | `ai-rex` |
| Debian package / file | `rex_<ver>_amd64.deb`, `/usr/bin/rex`, `/usr/share/doc/rex` | `ai-rex_<ver>_amd64.deb`, `/usr/bin/ai-rex`, `/usr/share/doc/ai-rex` |
| Windows zip / folder / launcher | `rex-win.zip`, `rex\`, `rex.cmd` | `ai-rex-win.zip`, `ai-rex\`, `ai-rex.cmd` |
| Config/data dir | `~/.config/runtime` | `~/.config/ai-rex` |
| Project skills dir | `<workspace>/.rex/skills` | `<workspace>/.ai-rex/skills` (old one is still read) |
| Env var prefix | `RUNTIME_*` | `AI_REX_*` (old still read, deprecation note on stderr) |
| Guard Docker image | `rex-guard:latest` | `ai-rex-guard:latest` |
| ACP agent name, REPL prompt, `--help` prog | `rex` / `runtime` | `ai-rex` |
| Example config | `scripts/rex-claude-cli.example.yaml` | `scripts/ai-rex-claude-cli.example.yaml` |

## Migration

On every start `ai-rex` checks `~/.config/ai-rex`. If it is missing and `~/.config/runtime` exists, the old directory is
copied once (config, `.env`, skills, sessions, permissions) and a note goes to stderr. The old directory is never
deleted or changed; remove it yourself when you are happy. `RUNTIME_*` variables keep working and print
"environment variable RUNTIME_X is deprecated, use AI_REX_X"; if both are set, `AI_REX_*` wins.

## Upgrade

1. Remove the old package if you had it (`sudo apt remove rex` only if it was ours, not Perl Rex), then
   `sudo apt install ./ai-rex_<ver>_amd64.deb`. From a checkout: `pip install -e .` (the command is now `ai-rex`).
2. Rebuild the guard image: `docker build -t ai-rex-guard:latest .`; if your config sets `docker_guard.image`, change it.
3. Rename `RUNTIME_*` variables to `AI_REX_*` at your leisure; move `.rex/skills` to `.ai-rex/skills`.
4. Editor/front-end configs that start `rex acp` must now start `ai-rex acp`. Front-ends (Layka, Sisi, Lassie) that look
   for a `rex` command or a `rex` folder next to them need the same one-word change in their own repos.

## Left as "rex" (internal, never a command or top-level import)

- Python module `runtimelib/rex.py` (`from .rex import REX`) and the `REX` API class; top-level packages stay
  `runtime` and `runtimelib`.
- Native helper files and symbols `rex_native.*`, `librex_native.*`, `rex_*` C functions (not installed as `rex`).
- Logger names (`rex.skills`), temp prefixes, User-Agent `rex-webfetch/1.0`, internal `REX_COMMAND`/`REX_ROOT`.
- `scripts/claude_cli_gateway.py` and its `REX_GATEWAY_TOKEN` variable: not touched on purpose (a gateway is running);
  rename it to `AI_REX_GATEWAY_TOKEN` (with the old name as fallback) in a later change.
- `.runtime/sessions` inside a project (sandbox data), `docker-compose.yml` container names, GitHub repo name in `deploy.sh`.
- Test-only variable `REX_LIVE_CLAUDE`; history documents under `docs/history`.
