# Testing ai-rex

Everything below can be run by one person with no outside help. Production code is stdlib-only; the test tools
(pytest) are installed with pip into a throw-away virtual environment.

## 1. Prerequisites

| | Windows 11 | Linux (Mint/Ubuntu/Debian) | macOS |
|---|---|---|---|
| Python 3.12 or newer | python.org installer, or `winget install Python.Python.3.12` | `sudo apt install python3 python3-venv` | `brew install python` |
| git | Git for Windows | `sudo apt install git` | Xcode command line tools |
| C++ compiler (only for the native suite) | optional: MinGW-w64 g++ on PATH | `sudo apt install g++` | `xcode-select --install` (clang as `c++`) |

Docker, a network connection and API keys are NOT needed: the tests use fakes (`tests/fakes`, `tests/fixtures`).

## 2. Set up the virtual environment (once)

Linux / macOS:

```bash
cd rex
python3 -m venv .venv-test
.venv-test/bin/python -m pip install pytest==9.0.2
```

Windows (Git Bash or cmd):

```bash
cd rex
python -m venv .venv-test
.venv-test\Scripts\python.exe -m pip install pytest==9.0.2
```

`.venv-test` is git-ignored.

## 3. Run everything (one command)

Linux / macOS:

```bash
.venv-test/bin/python tools/run_tests.py
```

Windows:

```bash
.venv-test\Scripts\python.exe tools/run_tests.py
```

This runs every suite that can run on your machine and writes `test-results.txt` (date UTC, git commit, OS, per suite
the command and passed/failed/skipped counts, and `OVERALL: PASS` or `FAIL`). Suites that cannot run (for example no
compiler) are listed as `not run here: <reason>` and do not make the run fail. Exit code 0 means PASS.

## 4. Run one suite by hand

```bash
# Python suite (about 700 tests, a few minutes)
.venv-test/bin/python -m pytest -q            # Windows: .venv-test\Scripts\python.exe -m pytest -q
.venv-test/bin/python -m pytest tests/test_guard.py -q        # one file
.venv-test/bin/python -m pytest -q -k "compaction"            # by name

# Attribution headers
python -I tools/check_ai_header.py

# Native C++ self-test (needs g++)
cd native && make test
```

## 5. What each suite covers

- **Python (pytest, `tests/`)**: the whole runtime. Agent loop and compaction, LLM client and streaming, the
  Claude CLI gateway (incl. end-to-end with a fake CLI), tools (edit, webfetch, shell), permissions and the guard
  (including consistency with the Docker guard), MCP client and lifecycle, ACP and JSONL bridge, config,
  sessions, skills, security checks, vendored kikolib pin, native-library wrapper with Python fallback, CLI.
- **Attribution headers (`tools/check_ai_header.py`)**: every file with an author header carries an
  `AI models:` (or older `LLM:`) line. Also run inside pytest as `tests/test_ai_header.py`.
- **Native C++ (`native/test_native.cpp`)**: JSON helpers, SSE parsing and file helpers of `rex_native.cpp`.
  Plain asserts; a non-zero exit code is a failure. Tests that need the built library are skipped in pytest when it
  is missing (reported as "skipped", not a failure).

## 6. What red means and where to look

- `test-results.txt`: the `output tail` under a failed suite shows the last lines.
- pytest: run the failing file alone with `-x -vv`; the traceback names the test and the assertion.
- Many failures in `test_native*`, none elsewhere: the native library or compiler is the cause, not the runtime.
- `missing attribution line: <file>`: add an `AI models: <id>` line to that file's header.
- Failures only on Windows or only on Linux: look for path separators, line endings and shell quoting.
- Network errors: tests must not need a network; treat that as a bug in the test.

## 7. CI

`.github/workflows/build.yml` runs on every push and pull request: all suites (full pytest, header check, native
C++ test) on Ubuntu, Windows and macOS (Python 3.12 and 3.14), and uploads `test-results.txt` as an artifact
(`test-results-<os>-py<ver>`). Packaging runs only after the tests pass.

## 8. Tests are never shipped

`packaging/build-deb.sh` and `packaging/build-win-zip.sh` copy only `runtime/`, `runtimelib/` and `vendor/kikolib`.
`tests/`, `tools/` and `native/test_native.cpp` are not copied; check with `unzip -l dist/ai-rex-win.zip` or
`dpkg -c dist/*.deb`.

## 9. Manual test plans

- Gateway tool calling, section 5 "Test plan": [docs/plans/2026-11-23-gateway-tool-calling.md](docs/plans/2026-11-23-gateway-tool-calling.md)
- Debian package install smoke test (clean container): [docs/plans/2026-11-23-debian-packaging.md](docs/plans/2026-11-23-debian-packaging.md)
- Guard in Docker: [docs/guard-docker.md](docs/guard-docker.md)
- Windows native build: [native/README-WINDOWS.md](native/README-WINDOWS.md)
