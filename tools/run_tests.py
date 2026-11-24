# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5
"""Run every test suite that can run on this machine and write test-results.txt.

Usage: python tools/run_tests.py [--out FILE]      (stdlib only; needs pytest installed for the Python suite)
Exit code 0 = every suite that ran passed; 1 = some suite failed or pytest is missing.
Suites that cannot run here are listed as "not run here: <reason>" and do not fail the run.
"""
from __future__ import annotations

import datetime
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], cwd: Path = ROOT) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def counts(text: str) -> dict[str, int]:
    out = {"passed": 0, "failed": 0, "skipped": 0}
    for line in reversed(text.strip().splitlines()):
        if re.search(r"\d+ (passed|failed|skipped|errors?).* in [\d.]+s", line):
            for n, word in re.findall(r"(\d+) (passed|failed|skipped|errors?)", line):
                out["failed" if word.startswith("error") else word] += int(n)
            break
    return out


def suite_pytest() -> dict:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    shown = "python -m pytest -q"
    if subprocess.run([sys.executable, "-c", "import pytest"], capture_output=True).returncode:
        return {"name": "python (pytest)", "cmd": shown, "skip": "pytest is not installed in this Python (see TESTING.md)"}
    rc, out = run(cmd)
    c = counts(out)
    return {"name": "python (pytest)", "cmd": shown, "rc": rc, "tail": out[-1500:], **c}


def suite_header() -> dict:
    rc, out = run([sys.executable, "-I", "tools/check_ai_header.py"])
    bad = len([ln for ln in out.splitlines() if ln.startswith("missing attribution")])
    return {"name": "attribution headers", "cmd": "python -I tools/check_ai_header.py", "rc": rc,
            "tail": out[-1500:], "passed": 1 if rc == 0 else 0, "failed": bad if rc else 0, "skipped": 0}


def suite_native() -> dict:
    name, shown = "native C++ (test_native)", "g++ -std=c++17 -pthread native/test_native.cpp native/rex_native.cpp && run"
    cxx = shutil.which("g++") or shutil.which("c++")
    if not cxx:
        return {"name": name, "cmd": shown, "skip": "no C++ compiler (g++) on PATH"}
    with tempfile.TemporaryDirectory() as td:
        exe = Path(td) / ("test_native.exe" if sys.platform == "win32" else "test_native")
        rc, out = run([cxx, "-std=c++17", "-O2", "-Wall", "-Wextra", "-pthread", "-o", str(exe),
                       "native/test_native.cpp", "native/rex_native.cpp"])
        if rc:
            return {"name": name, "cmd": shown, "rc": rc, "tail": "compile failed:\n" + out[-1500:],
                    "passed": 0, "failed": 1, "skipped": 0}
        rc, out = run([str(exe)], cwd=ROOT / "native")
    return {"name": name, "cmd": shown, "rc": rc, "tail": out[-1500:],
            "passed": 1 if rc == 0 else 0, "failed": 0 if rc == 0 else 1, "skipped": 0}


def main(argv: list[str]) -> int:
    out_path = ROOT / "test-results.txt"
    if "--out" in argv:
        out_path = Path(argv[argv.index("--out") + 1])
    _, commit = run(["git", "rev-parse", "--short", "HEAD"])
    results = [suite_header(), suite_pytest(), suite_native()]
    ran = [r for r in results if "skip" not in r]
    ok = bool(ran) and all(r["rc"] == 0 for r in ran) and any(r["name"].startswith("python") for r in ran)
    lines = [
        f"date (UTC): {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
        f"git commit: {commit.strip() or 'unknown'}",
        f"os: {platform.platform()} (python {platform.python_version()})",
        "",
    ]
    for r in results:
        lines.append(f"suite: {r['name']}")
        lines.append(f"  command: {r['cmd']}")
        if "skip" in r:
            lines.append(f"  not run here: {r['skip']}")
        else:
            lines.append(f"  passed: {r['passed']}  failed: {r['failed']}  skipped: {r['skipped']}")
            lines.append(f"  result: {'PASS' if r['rc'] == 0 else 'FAIL'}")
            if r["rc"]:
                lines.append("  output tail:")
                lines += ["    " + ln for ln in r["tail"].splitlines()[-30:]]
        lines.append("")
    lines.append(f"OVERALL: {'PASS' if ok else 'FAIL'}")
    text = "\n".join(lines) + "\n"
    out_path.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
