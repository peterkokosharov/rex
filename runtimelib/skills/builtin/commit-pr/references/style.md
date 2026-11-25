# Conventional Commits (use only if the repo already does)

Check `git log --oneline -20`. If subjects look like `type(scope): summary`, follow that; otherwise use a
plain imperative subject and skip this.

Format: `type(scope): summary`

- Types: `feat` (new behaviour), `fix` (bug fix), `docs`, `test`, `refactor` (no behaviour change),
  `perf`, `build`, `ci`, `chore`.
- `scope` is optional: the area touched, e.g. `feat(skills): bundle built-in skills`.
- Summary: imperative, lower-case start, no trailing period, <= 50 chars.
- Breaking change: add `!` after the type/scope (`feat!:`) and a `BREAKING CHANGE:` footer explaining it.
- Body (optional): blank line, then the why, wrapped at ~72 cols.

One commit = one logical change. Do not bundle a refactor with a fix under a single `feat`.
