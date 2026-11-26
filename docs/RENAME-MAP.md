# Rename map: old path -> new path (ai_runtime_rex refactor)

Authoritative table. Status: DONE = new path is the real code, old path is a shim; ALIAS = new path re-exports
the old one; PLANNED = not started. Plan step 4+ moves the rest and removes the shims at the end.

| Old path | New path | Status |
|---|---|---|
| `rex_runtime_lib/` (llm.py + session.py) | `ai_runtime_rex/runtime/embed.py` | DONE (old `rex_runtime_lib/__init__.py` is a shim) |
| `rex_runtime_lib/README.md` | `ai_runtime_rex/runtime/EMBED_README.md` | DONE |
| `runtimelib/` | `ai_runtime_rex/core/` | ALIAS (`ai_runtime_rex.core` re-exports `runtimelib`; real move = step 4) |
| `runtime/` (cmd-line chat) | `ai_runtime_rex/runtime/` | PLANNED (step 6) |
| `guard/` | `ai_runtime_rex/core/sandbox/` | PLANNED (later) |
| `native/` | `ai_runtime_rex/core/native/` | PLANNED |
| `vendor/kikolib/` | stays vendored; facade `ai_runtime_rex/kiko_lite/` | PLANNED (step 8) |
| `orchestrator/` | `extras/orchestrator/` | PLANNED (owner decision pending) |
| (new) | `ai_runtime_rex/shared/`, `cli/`, `desktop/`, `web/` | PLANNED (steps 5, 7, 9) |
