---
name: python-project
description: Run, test or fix a Python project - interpreter and venv detection, pytest, ruff/mypy, common import and dependency failures. Use when Python tests or checks need to run or fail.
---

1. **Read the setup.** `pyproject.toml` (or `setup.py` / `setup.cfg`, `requirements*.txt`, `tox.ini`):
   required Python version, dependencies, `[project.scripts]`, `[tool.pytest.ini_options]`, ruff and mypy
   settings.
2. **Pick the interpreter.** A project venv (`.venv/`, `venv/`) wins: `.venv/bin/python` (Windows
   `.venv/Scripts/python.exe`). Else `python --version` / `python3` / `py -3`. Compare with the required
   version. Do not create a venv or install packages unless the user asked for it.
3. **Run the tests** (timeout 600 s): `<python> -m pytest -q` (all), `-q tests/test_x.py::test_name` (one),
   `-x` to stop at the first failure, `--lf` to rerun the last failures. Read the short summary at the end
   first, then the first failure's traceback.
4. **Checks when the project configures them:** `<python> -m ruff check .`, `<python> -m mypy <package>`.
5. **Common failures and their usual cause:**
   - `ModuleNotFoundError` for the project itself - src layout not installed; run with `PYTHONPATH=src` or
     install in editable mode (only if allowed).
   - `ModuleNotFoundError` for a dependency - not installed in this interpreter; wrong venv.
   - Syntax errors on modern syntax - interpreter older than the project needs.
   - Tests needing network, a database or environment variables - name what is missing.
6. **Report:** interpreter and command used, result (passed / failed counts), first real error with
   `path:line`, cause, and the fix (applied or proposed).
