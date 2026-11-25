---
name: commit-pr
description: Write a clean commit message or pull-request description from the staged diff and git log. Use when asked to commit, write a commit message, or draft a PR body. Never invent changes not in the diff.
---

Describe only what the diff actually does. Accuracy over polish.

1. Read the real change. For a commit use `git diff --staged` (nothing staged: inspect `git diff` and stage
   deliberately). For a PR use `git log --oneline main..HEAD` and `git diff main...HEAD`. The diff is ground
   truth - never describe a change that is not in it, and never omit a behaviour change that is.
2. Commit message:
   - Subject: imperative mood, <= 50 chars, no trailing period ("Fix race in cache eviction").
   - Blank line, then body wrapped at ~72 cols explaining **why** (the problem, the reasoning), not a
     line-by-line restatement of **what** - the diff already shows what.
   - Note breaking changes, and reference an issue/ticket only if you were given one.
3. PR description: a one-line summary, a short "Why" paragraph, a terse bullet list of the notable changes,
   and how it was tested. Call out anything risky or needing reviewer attention.
4. Honesty: if the diff mixes unrelated changes, say so and suggest splitting rather than inventing a tidy
   story. Do not claim tests pass unless you ran them.
5. Match the repo's existing commit style if one is obvious (`git log`). See `references/style.md` for the
   conventional-commits variant if this repo uses it. Do not run `git commit` or `git push` unless asked to.
