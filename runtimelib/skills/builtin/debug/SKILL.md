---
name: debug
description: Find and fix a bug methodically - reproduce first, isolate by bisection, make the smallest fix, then prove it. Use when something fails, errors, crashes or gives wrong output and the cause is unknown.
---

Do not guess-and-patch. Reproduce, isolate, fix small, prove.

1. Reproduce first. Get an exact, repeatable trigger: a failing test, or a precise command with its input
   and the wrong output. If you cannot reproduce it, say so and gather more facts before touching code.
   Capture the true error (full message, stack trace, exit code), not a paraphrase.
2. Form one hypothesis about the cause from the evidence. State it in a sentence.
3. Isolate it. Narrow the fault: bisect the input, the code path, or history (`git bisect` / `git log -S`).
   Add a temporary print/log or breakpoint to confirm the actual values at the suspect point. Keep cutting
   until you can point at the single line or condition. Let evidence, not intuition, pick the spot.
4. Make the smallest fix that addresses the root cause, not the symptom. Resist refactoring nearby code.
5. Prove it. Re-run the exact reproduction from step 1: it must now pass. Run the surrounding tests to show
   you broke nothing. Add a regression test that fails without your fix.
6. Clean up temporary logging/prints. Report the root cause in one or two sentences and what the fix changed.
