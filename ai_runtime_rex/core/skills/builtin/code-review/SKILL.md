---
name: code-review
description: Review a diff for correctness bugs, edge cases, security and risky changes. Use when asked to review a diff, a commit, a branch or a pull request, or to check a change before merge.
---

Review the changed code for defects that would bite in production. Report problems, not praise.

1. Get the diff. Prefer `git diff --merge-base main` (or the branch's base); fall back to `git diff HEAD`
   or the paths/PR you were given. Read enough surrounding code to judge each change in context.
2. Hunt, in this order of importance:
   - **Correctness:** wrong logic, off-by-one, inverted conditions, bad null/empty handling, type
     mismatches, misused APIs, broken error paths, resource leaks (unclosed files/sockets/locks).
   - **Edge cases:** empty input, zero, negative, very large, Unicode, concurrency/races, partial failure,
     retries, time zones, integer/float overflow.
   - **Security:** injection (SQL/shell/path), unsanitised input, secrets in code or logs, unsafe
     deserialisation, missing authz checks, `..`/absolute-path escapes, SSRF.
   - **Regressions:** behaviour the change silently alters, removed validation, broken callers.
3. For each finding give: `path:line`, severity (critical / high / medium / low), one sentence on the bug,
   one sentence on the fix. Say how sure you are; flag guesses as "verify".
4. Verify before claiming. If a suspected bug depends on code you cannot see, say so rather than assert it.
5. Rank findings most-severe first. Skip pure style, naming and formatting unless they cause a real bug.
6. End with one line: a go / fix-first verdict. If you found nothing substantive, say so plainly.
