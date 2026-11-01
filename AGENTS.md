<!--
Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
LLM: nvidia nemotron ultra
Licensed under MIT License
-->

# AGENTS.md

## Agent Rules

### 1. Language for Human Communication
- **Reply in English** — All responses to the human must be in English.
- **Code, code comments, documentation (technical)** — in English.
- **Human is on console (terminal)** — wants short, substantive answers. No unnecessary explanations, no "backstory".

### 2. Working Directory
- **Don't leave the folder** — All files and operations must stay in the working directory (the project root). Don't access files outside this folder.

### 3. Git as a Tool (Internal Workflow)
- **After every completed task** — make a local `git commit`.
- **Commit message** — short, substantive: what changed, why.
- **Purpose**: The team (orchestrator) knows where to get results. Change history = context for state.
- **Don't push** — local commits only. Human pushes when they decide.

### 4. Tools
- **Bash** — for commands, scripts, checks
- **File read/write/edit** — for code, config, documentation
- **Git** — for commit on every completed task
- **Web search** — only when needed for current information

### 5. Work Style
- **Practical, not theoretical** — write code, test, iterate.
- **Tests parallel with code** — every new module has a test.
- **Mock/stub for external dependencies** — tests must be fast and isolated.
- **Short answers** — human is in terminal, doesn't read essays.

### 6. Planning vs Execution
- **Plan** — only when necessary (many files, architectural decision).
- **Execute** — by default. The plan is in the code and commits.

---

## Summary: What You Do Now

1. Write code → 2. Run tests → 3. `git commit` → 4. Short answer to human what happened.