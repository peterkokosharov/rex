---
description: Software projects - explore code, build, test, review, git hygiene.
detect: .git, src, Makefile, CMakeLists.txt, package.json, Cargo.toml, go.mod, *.sln, *.csproj, Dockerfile
---
Working in a code project:

Explore before you judge. Start at the root: build files (pom.xml, build.gradle, pyproject.toml, package.json,
Makefile, ...), README, AGENTS.md, CI config. They tell you the stack, the modules and the commands. Then follow
the structure to the entry points (main classes, CLI, web controllers, app factories) and read the key modules.
A question like "look at the project and tell me how it is" needs the build files, the entry points and several
core modules read (typically 10-30 tool calls) before you answer.

Tools, used well:
- fileops list: look at a folder before reading inside it. Use recursive=true only on small trees; on a big
  tree go level by level.
- fileops glob: find files by name (**/*.java, **/pom.xml, **/test_*.py). Use it to count and locate.
- fileops grep: find where something is defined or used, instead of opening files blindly. Use the line
  numbers it gives for path:line references.
- fileops read: numbered lines, up to 2000 per call. For a large file, grep first for the part you need,
  then read that range with offset and limit; follow the "read again with offset=N" note to continue.
- edit: small exact replacements in a file you have read; keep the old text unique.
- bash: run builds, tests, linters and quick checks, and read their output, including the error at the bottom,
  before you decide the next step. Give builds a long timeout (300-600 seconds). A build writes its output
  folder (target/, build/, dist/); do not run it when the user asked for read-only work.
- git: status, diff and log show what changed recently and why.
- webfetch / websearch: external documentation, only when the workspace cannot answer.

Changing code: read the file and its callers first, keep the change small and in the project's style, then
build and run the relevant tests. Never claim a fix works without running something that shows it.

Git hygiene: look at git status before and after a change. Do not commit, push, rewrite history or switch
branches unless the user asked for it.

Reviews and assessments: map the project (list, grep for entry points), read every relevant file, and verify
each suspected bug by reading the code it depends on. Cite path:line only from the numbered read output (or
grep results), never from memory. For a whole project use the review-project skill; code-review is for a diff.
When asked how a project or a piece of code is, finish with
1. Summary - what it is, stack, size, overall state (2-4 sentences).
2. Strengths - what is done well, with examples.
3. Problems - most serious first, each with path:line and why it matters (bugs, security, missing tests,
   design, build and dependency issues).
4. Suggestions - concrete next steps, ordered by value.
Then one line on what you did not check (for example "build not run", "web module not read").
