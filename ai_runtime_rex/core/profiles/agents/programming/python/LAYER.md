---
description: Python projects - venv, pytest, ruff, mypy, packaging.
detect: pyproject.toml, setup.py, setup.cfg, requirements*.txt, Pipfile, poetry.lock, uv.lock, tox.ini
---
Python projects:

- Read pyproject.toml (or setup.py / setup.cfg, requirements*.txt): Python version, dependencies, entry points
  ([project.scripts]), tool settings (pytest, ruff, mypy, coverage).
- Interpreter: `python --version` (or `python3`, `py -3`). Use the project's virtual environment when there is
  one (.venv/, venv/): run tools as .venv/bin/python -m ... (Windows: .venv/Scripts/python.exe). Do not install
  packages unless the user asked for it.
- Layout: src/<package>/ or <package>/ at the root, tests/ beside it. Find the entry points (__main__.py,
  console scripts, app factory) before judging the design.
- Tests: `python -m pytest -q` (one test: `python -m pytest -q tests/test_x.py::test_name`; stop at the first
  failure with -x). Read the failure summary at the end of the output first.
- Checks when configured: `ruff check .`, `mypy <package>`.
- Review points: bare except or silently swallowed errors, mutable default arguments, missing type hints on
  public functions, global state, subprocess with shell=True and user input, SQL built by string formatting,
  files not closed (use with), print instead of logging in libraries, missing tests for core logic.
