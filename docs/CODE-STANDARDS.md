# Code standards — py-vision and rex

One style, as if one person wrote it. The test: the owner must be able to read
the whole code base end to end (like reading Linux 0.01 in a week) and answer
for every line. Every agent — Claude, Nemotron, Gemini, DeepSeek — gets this
document in its brief. Reviewers reject code in another style even when it runs.

The reference is what **py-vision already does**. Where **rex** differs, the
last section says which way wins and how rex converges over time.

---

## 1. Principles

- **Standard library only in production.** No third-party runtime deps. Tests
  and dev tools may use pip in a venv (pytest), never the shipped package.
- **The simplest code that works.** Cut, then two nails, then verify. No
  cleverness, no metaprogramming, no decorators-as-magic, no dynamic class
  building. If it works, leave it; rewrite only on hard evidence.
- **No global state** unless it is a documented module-level registry with a
  plain reason (e.g. `icons._registry`, `icons._active`). Everything else is
  passed in or injected (`clock`, `atexit_mod`, module seams for tests).
- **Small units.** Observed and expected limits:
  - a method/function is typically **3–25 lines**; a hard one may reach ~50,
    but then it carries a docstring saying why;
  - a module is **one coherent subsystem**; keep it well under ~900 lines and
    split when two ideas appear in one file;
  - one class = one concept (`InputLine`, `MoneyLine`, `Window`, `Dialog`).
- **Read-aloud test.** If a block cannot be explained in one plain sentence,
  it is too clever.

## 2. Layout of a module

Header first (personal repos, MIT, never the A1 header):

```python
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# SPDX-License-Identifier: MIT
# AI models: claude-opus-4-8, claude-sonnet-5-5
```

rex currently uses its own four-line variant (`# LLM:` instead of
`# AI models:`, SPDX first, an extra `# Licensed under MIT License` line); see
section 8. New code uses the three-line form above.

Then, in order:

```python
"""One line saying WHAT this module is.

Then plain prose: HOW it works and WHY it is built this way, including any
deliberate differences from the thing it ports or mirrors. Teach the reader.
"""
from __future__ import annotations

import re                         # stdlib, alphabetical
from decimal import Decimal

from ..events import Command      # local, relative, grouped after stdlib
from ..geometry import Rect

MAX_INTEGER_DIGITS = 15           # module constants
_RAW_RE = re.compile(...)         # module-private constants: leading underscore
```

Classes and functions follow. Group related methods under short `# -- name --`
banners, as the existing files do.

## 3. Naming, comments, docstrings

- **Modules:** short, lowercase, run together, no underscores — `inputline`,
  `moneyline`, `msgbox`, `scrollbar`.
- **Classes:** `CapWords` — `TabDialog`, `MoneyLine`.
- **Functions / methods / variables:** `snake_case` — `handle_event`, `cursor_pos`.
- **Private names:** one leading underscore — `_apply_edit`, `_scroll_into_view`.
- **Constants:** `UPPER_SNAKE`; module-private ones keep a leading underscore
  (`_GROUP_CHARS`).
- **Tests:** `test_<behaviour>`, a full plain sentence —
  `test_autoindent_copies_leading_whitespace`.
- **Comments in code: American English** ("normalize", "color", "behavior").
  **Comments and names in tests: British English** ("normalise", "behaviour").
  Comments say *why*, never restate the code.
- **Docstrings:** triple-quoted. First line is a summary; method docstrings
  state the contract and what the return value means:

```python
def kill_timer(self, timer_id: int) -> bool:
    """Cancel a timer that has not fired yet. False if the id is unknown
    or the timer already fired."""
```

Full type hints everywhere, modern syntax: `tuple[int, int]`, `View | None`,
`list[str]`. Do not import `Optional`/`List` for this.

## 4. Error handling, outside input, money, paths

- **Never trust outside data.** Filter every byte that crosses the boundary —
  paste, clipboard, files, user labels. Pattern from the code:

```python
def _clean_paste(text: str) -> str:
    """Newline and TAB become a space; other C0/C1 controls and DEL dropped."""
    out = []
    for ch in text:
        if ch in "\n\t":
            out.append(" ")
        elif ch < " " or "\x7f" <= ch <= "\x9f":
            continue
        else:
            out.append(ch)
    return "".join(out)
```

- **Raise `ValueError`/`TypeError`** with a short lowercase message that names
  the problem: `raise ValueError("a timer delay cannot be negative")`.
- **Money is `Decimal`, never float.** Parse, `quantize` to scale with
  `ROUND_HALF_UP`, and reject float/bool at the door:

```python
if isinstance(value, (float, bool)):
    raise TypeError("MoneyLine.set_value: float and bool are not allowed")
```

- **Paths use `pathlib`.** No string concatenation of paths, no `os.path`.
- **Cross-platform.** Code must run on Windows, Linux, macOS. Isolate OS-only
  steps and guard them (`import termios` in a `try/except ImportError`;
  `getattr(signal, "SIGHUP", None)`); never assume a POSIX-only call exists.

## 5. Tests

- **Through the real routing**, never by poking a widget. Build a `Program` on
  a `HeadlessTerminal`, `feed_events`, run `pump`/`exec_view`, and read results
  from the rendered screen buffer — integration bugs hide when you call a method
  directly (a documented pipeline lesson).
- **Name each done-when behaviour** in the test-module docstring and cover it
  with a `test_<behaviour>` of the same name.
- **No `skip`, no `xfail`, no `xpass`.** A test either passes or fails; the gate
  fails the build on any new xfail. An unfinished behaviour is a failing test.
- **Mutation-check the key tests:** break the code on purpose once and confirm
  the test goes red, so no test is vacuous.
- **Performance bounds are generous** (10–40× a laptop measurement), per step,
  and print the real numbers — they catch a quadratic, not a slow machine.

## 6. Commits

- **One step per commit.** A commit is a coherent, green change; never mix a
  refactor with a feature.
- **Message:** a short imperative subject, then a body of plain prose saying
  what changed and why. No A1, no company trailer.
- **Trailer:** end with the models that wrote it, so a model that breaks things
  can be dropped later:

```
Money input: commit as Decimal, reject float and bool

AI-Models: claude-opus-4-8, claude-sonnet-5-5
```

- Commit after each verified milestone; never push/pull/fetch on another's
  behalf.

## 7. Reviewer checklist (pass/fail)

1. Header present, three lines, MIT, `AI models:` with this run's model id — no A1.
2. `from __future__ import annotations`; imports stdlib-then-local, no third-party in production.
3. Module docstring explains WHAT and HOW in plain words.
4. Names follow section 3; modules lowercase-no-underscore, tests `test_<behaviour>`.
5. Functions small (≈≤25 lines) and single-purpose; no file grew into two subsystems.
6. No metaprogramming, no undocumented global state, no cleverness.
7. Full modern type hints (`X | None`, `list[...]`).
8. Every outside input is filtered before use.
9. Errors raise `ValueError`/`TypeError` with a clear message; no bare `except`.
10. Money is `Decimal` with `ROUND_HALF_UP`; no float touches an amount.
11. Paths use `pathlib`; OS-specific steps are guarded and isolated.
12. Comments American in code, British in tests; comments say why, not what.
13. New tests run through real routing, map to done-when names, no skip/xfail.
14. At least the key new tests were shown to fail when the code is broken.
15. One step per commit, plain message, `AI-Models:` trailer, no A1.

---

## 8. rex: where it deviates today, and the target

rex is converging on this style. Current gaps and the fix (apply gradually,
one file per touch, never a mass rewrite):

- **Header.** rex uses four lines, SPDX first, `# LLM:` instead of
  `# AI models:`, plus a redundant `# Licensed under MIT License`, and some
  files have blank lines inside the header. **Target:** the three-line form of
  section 2 (`Copyright` first, `SPDX`, `AI models:`); drop the extra licence
  line and the blank lines. Keep model ids truthful and lowercase; a file that
  legacy models (e.g. `nvidia nemotron ultra`) touched keeps that id until the
  file is substantially rewritten under Claude only, then it is updated.
  **py-vision wins.**
- **Typing.** rex imports `Optional`, `Callable`, `Any` and writes
  `Optional[Config]`. **Target:** `Config | None` and bare `list`/`dict`
  generics, as py-vision does. **py-vision wins.**
- **Docstrings.** rex dataclasses carry terse one-liners ("Agent role
  configuration."). **Target:** keep one-liners for a plain data holder, but any
  module or non-trivial class gets the teaching docstring of section 2.
  **py-vision wins.**
- **Async.** rex is legitimately `asyncio`-based (orchestration, ACP); py-vision
  is synchronous. This is a domain difference, not a style gap — **keep async in
  rex**, but hold to the same naming, typing, docstring and test rules.
- **Tests.** Same rule as section 5: drive rex through its real session/agent
  loop, not internal calls; no xfail.

When a rex file is opened for any reason, bring its header and typing up to this
standard in the same commit. Do not open files only to reformat them.

---
*MIT. Peter Kokosharov <peter.kokosharov@gmail.com>. This document is the single
source of style for py-vision and rex; copies in both repos are kept identical.*
