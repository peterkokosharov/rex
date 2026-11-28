<!--
Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
LLM: claude-opus-5-5
Licensed under MIT License
-->

# Agent profiles: layered instructions

ai-rex tells the model how to work through a tree of small Markdown files, general to specific. The effective
instruction is the concatenation of the active layers, root first, leaf last. Task skills (`review-project`,
`java-maven`, ...) sit on top of whatever layers are active, and AGENTS.md comes after both.

```
                       agents/                     base: work loop, honesty, safety, language
                          |
              +-----------+------------+
              |                        |
        programming/               writing/ , odoo/ ...   (your own branches)
   explore, build, test,
   review format, git
              |
      +-------+--------+
      |                |
    java/           python/
 JDK, Maven,      venv, pytest,
 Gradle,          ruff, mypy
 surefire
      |
  spring/ (your own leaf)

 system prompt = base + programming + java [+ python ...]
                 + tool list + skill index (load_skill on demand)
                 + AGENTS.md (project, then user)
```

## Layers

A layer is a folder with `LAYER.md` under an `agents` root; its folder path is its name (`programming/java`).
The root layer is `agents/LAYER.md`. Frontmatter (all optional):

| Key | Meaning |
|---|---|
| `description` | one line shown by `ai-rex profile` |
| `detect` | comma-separated file/folder name patterns (fnmatch) matched against the workspace root, e.g. `pom.xml, build.gradle*` |
| `extend: true` | append this text to the same-path layer of a lower root instead of replacing it |

Built-in tree (ships in `ai_runtime_rex/core/profiles/agents/`):

| Layer | Detected by | Content |
|---|---|---|
| (base) | always | role, explore -> plan -> act -> verify -> report, keep going, honesty, ask before destructive actions, answer in the user's language |
| `programming` | `.git`, `src`, `Makefile`, `CMakeLists.txt`, `package.json`, `Cargo.toml`, `go.mod`, `*.sln`, `*.csproj`, `Dockerfile` | explore code from build files to entry points, how to use each tool, builds and tests, git hygiene, review report format |
| `programming/java` | `pom.xml`, `build.gradle(.kts)`, `settings.gradle(.kts)`, `mvnw`, `gradlew` | JDK check, Maven/Gradle commands, surefire reports, common failures, review points |
| `programming/python` | `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements*.txt`, `Pipfile`, `poetry.lock`, `uv.lock`, `tox.ini` | interpreter and venv, pytest, ruff/mypy, review points |

## Selection

First match wins:

1. `--profile LAYERS` (repeatable or comma-separated), or the setting `agent.profile`
   (`AI_REX_AGENT__PROFILE`, `--config` file).
2. `AI_REX_PROFILE`.
3. A line `ai-rex-profile: LAYERS` in a loaded AGENTS.md (user file, or the project file of a trusted
   workspace). It may be wrapped in an HTML comment: `<!-- ai-rex-profile: programming/java -->`.
4. `auto`: every layer whose `detect` patterns match a name in the workspace root.

Tokens: a layer path (`programming/java`), `auto` (the detected layers, can be combined:
`auto,programming/python`), `base` or `none` (root layer only). Every ancestor of a chosen layer is active
too; several leaves are fine for mixed repositories. An unknown path gives a warning and keeps its existing
ancestors. Order is by depth, then alphabetical: base, programming, programming/java, programming/python.

## Your own layers

Roots, highest precedence first:

1. `~/.config/ai-rex/agents/`
2. `<install folder>/agents/` (the portable install folder, next to `ai-rex.cmd`; a reinstall keeps it)
3. folders in the setting `agent.layer_paths` (list, or paths separated by `;` on Windows, `:` elsewhere)
4. `<workspace>/.ai-rex/agents/` and `<workspace>/.rex/agents/` - only when `workspace.trust` is true
5. the built-in tree

The same path in a higher root replaces the lower layer (or extends it with `extend: true`); `detect` and
`description` are inherited when the higher layer does not set them. A new path adds a layer, for example
`~/.config/ai-rex/agents/programming/java/spring/LAYER.md` with `detect: application.yml, application.properties`.
A new branch such as `agents/writing/LAYER.md` is chosen with `--profile writing`.

Keep a layer short and concrete (10-40 lines): commands, places to look, common failures. Each layer is capped
at 16 KB.

## Checking what is active

```
ai-rex profile          # the tree, active layers marked with *, their source files
ai-rex profile show     # the composed instruction text
```

The system prompt also carries a line `Active profile: programming, programming/java`.

## Task skills

Built-in skills (`ai-rex skills`): `review-project` (assess an unknown repository), `java-maven` (build, test,
diagnose a Java build), `python-project` (run and diagnose Python tests), `code-review` (review a diff),
`write-tests`, `debug`, `commit-pr`. Only their names and descriptions are in the prompt; the model loads a
body with `load_skill` when the task matches. They are independent of the active layers.

## AGENTS.md

A project describes itself in `AGENTS.md`; see [AGENTS-TEMPLATE.md](AGENTS-TEMPLATE.md). The project file is read
only when the workspace is trusted (`workspace.trust: true`, `AI_REX_WORKSPACE__TRUST=true`); the user file
`~/.config/ai-rex/AGENTS.md` always.
