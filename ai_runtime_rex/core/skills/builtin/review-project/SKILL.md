---
name: review-project
description: Assess a whole unfamiliar project or repository - structure, quality, problems, suggestions. Use for "look at this project and tell me how it is", onboarding or audit requests.
---

Systematic review of a repository you do not know yet. Work through the steps; do not answer before step 5.

1. **Map the root.** `fileops list .`, then read the build files (pom.xml, build.gradle*, pyproject.toml,
   setup.py, requirements*.txt, package.json, go.mod, Cargo.toml, Makefile), README, AGENTS.md and CI config
   (.github/workflows, Jenkinsfile, .gitlab-ci.yml). Note: language and version, framework, modules,
   dependencies, how it is built and tested.
2. **Size it.** `fileops glob` for the main source and test files (e.g. `**/*.java`, `src/test/**/*.java`,
   `**/*.py`, `tests/**/*.py`). Count code files vs test files. `git log --oneline -15` (if it is a git repo)
   shows activity and recent topics.
3. **Read the entry points.** Main classes / `__main__.py` / CLI / web controllers / app factory. Follow one
   real request or command through the layers to learn the architecture.
4. **Sample the core.** Read 4-8 key modules in full: the biggest or most central (grep for the domain words
   from the README), the configuration, the data access code, one or two tests. Grep for known smells:
   `printStackTrace`, `System.out`, `catch (Exception`, `TODO|FIXME`, `except:`, `shell=True`, hard-coded
   passwords or URLs, string-built SQL.
5. **Run it if cheap and allowed.** Build and test with the project's own command (see the active profile
   layer) when the user did not ask for read-only work; read the result. Otherwise say it was not run.
6. **Report** in the user's language:
   - **Summary:** what the project does, stack and versions, size, overall verdict (2-4 sentences).
   - **Structure:** modules/packages and their roles, in a short list.
   - **Strengths:** concrete, with examples.
   - **Problems:** most serious first; each with `path:line`, what is wrong and why it matters.
   - **Suggestions:** concrete next steps ordered by value (quick wins first).
   - **Not checked:** what you did not read or run.

Every claim must come from a file you read or a command you ran. Typical depth: 15-40 tool calls.
