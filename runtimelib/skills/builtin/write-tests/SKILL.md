---
name: write-tests
description: Add focused tests for a specific change - business logic and real edge cases only, not getters or boilerplate. Use when asked to write, add or improve tests for code you just changed.
---

Write tests that would catch a real bug in this change, and run them.

1. Pin the target. Identify exactly which function/behaviour changed and what it promises. Test that
   contract, not the implementation details.
2. Match the repo. Find existing tests (`tests/`, `*_test.*`, `test_*.*`) and copy their framework, layout,
   naming, fixtures and assertion style. Do not introduce a new test tool or dependency.
3. Cover what matters, each as its own case:
   - the main success path with a representative input,
   - the edge cases that break code: empty, zero, negative, large, boundary, Unicode, duplicate, missing;
   - error handling: invalid input raises/returns the right thing;
   - any bug this change fixes (add a case that fails without the fix).
4. Skip the noise. No tests for trivial getters/setters, constants, straight delegation or framework code.
   A handful of sharp tests beats twenty shallow ones.
5. Keep them deterministic and isolated: no network, clock or global state; fake it. A test that writes
   state must restore it. Make failure messages say what was expected.
6. Run them and watch them pass. If one fails, decide whether the test or the code is wrong, fix it, rerun.
   Report what you added and the final pass/fail count.
