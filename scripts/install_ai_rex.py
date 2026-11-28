#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5, claude-opus-5-5
"""Portable install of ai-rex into ONE folder; delete the folder to uninstall. Python standard library only.

    python scripts/install_ai_rex.py DEST               install (or update) into DEST
    python scripts/install_ai_rex.py --uninstall DEST   delete DEST (only if it holds an ai-rex install)

Nothing outside DEST is touched: no PATH change, no registry, no shortcut, no config file. The install prints
the full launcher path (DEST\\ai-rex.cmd on Windows, DEST/ai-rex elsewhere). DEST also gets uninstall.cmd and
uninstall.sh.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = ".ai-rex-install"
PACKAGES = ("ai_runtime_rex", "runtime", "runtimelib")  # code under DEST/lib
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info")

CMD = """@echo off
REM ai-rex launcher (portable install). Uses "py -3" if present, else "python". No PATH change needed.
setlocal
REM No trailing ";": an empty PYTHONPATH entry would put the current directory on sys.path.
set "AIREX_PP=%~dp0lib"
if defined PYTHONPATH set "AIREX_PP=%AIREX_PP%;%PYTHONPATH%"
set "PYTHONPATH=%AIREX_PP%"
set "AIREX_PP="
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% -P -m runtime %*
exit /b %ERRORLEVEL%
"""
SH = """#!/bin/sh
# ai-rex launcher (portable install). No PATH change needed.
here="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$here/lib${PYTHONPATH:+:$PYTHONPATH}" exec python3 -P -m runtime "$@"
"""
UNINSTALL_CMD = """@echo off
REM Removes this ai-rex install: deletes the folder this file is in.
REM No ( ) blocks and the path only ever inside quotes: a folder name with ) & %% ^ ! must not break it.
REM The child cmd expands %%AIREX_DIR%% itself (one pass, inside quotes), so the path is never re-parsed.
setlocal DisableDelayedExpansion
set "AIREX_DIR=%~dp0"
if not exist "%AIREX_DIR%.ai-rex-install" goto refuse
echo Deleting "%AIREX_DIR%"
cd /d "%AIREX_DIR%.."
start "" /b cmd /d /v:off /c "ping -n 2 127.0.0.1 >nul & rd /s /q "%%AIREX_DIR%%""
exit /b 0
:refuse
echo refusing: "%AIREX_DIR%" holds no ai-rex install
exit /b 1
"""
UNINSTALL_SH = """#!/bin/sh
here="$(cd "$(dirname "$0")" && pwd)"
[ -f "$here/.ai-rex-install" ] || { echo "refusing: $here holds no ai-rex install" >&2; exit 1; }
echo "Deleting $here"
rm -rf "$here"
"""


def install(dest: Path) -> Path:
    dest = dest.resolve()
    if dest.exists() and not dest.is_dir():
        raise SystemExit(f"refusing: {dest} is not a folder")
    if dest.exists() and any(dest.iterdir()) and not (dest / MARKER).exists():
        raise SystemExit(f"refusing: {dest} is not empty and not an ai-rex install")
    lib = dest / "lib"
    if lib.exists():
        shutil.rmtree(lib)
    lib.mkdir(parents=True)
    for pkg in PACKAGES:
        shutil.copytree(ROOT / pkg, lib / pkg, ignore=IGNORE)
    shutil.copytree(ROOT / "vendor" / "kikolib", lib / "vendor" / "kikolib", ignore=IGNORE)
    for name, text in (("ai-rex.cmd", CMD), ("uninstall.cmd", UNINSTALL_CMD)):
        (dest / name).write_bytes(text.replace("\n", "\r\n").encode())
    for name, text in (("ai-rex", SH), ("uninstall.sh", UNINSTALL_SH)):
        path = dest / name
        path.write_bytes(text.encode())
        path.chmod(0o755)
    for doc in ("LICENSE", "docs/AI-REX.md"):
        if (ROOT / doc).exists():
            shutil.copy2(ROOT / doc, dest / Path(doc).name)
    (dest / MARKER).write_text("ai-rex portable install\n", encoding="utf-8")
    return dest / ("ai-rex.cmd" if sys.platform == "win32" else "ai-rex")


def uninstall(dest: Path) -> None:
    dest = dest.resolve()
    if not (dest / MARKER).exists():
        raise SystemExit(f"refusing: {dest} holds no ai-rex install ({MARKER} missing)")
    shutil.rmtree(dest)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dest", type=Path)
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()
    if args.uninstall:
        uninstall(args.dest)
        print(f"removed {args.dest}")
        return 0
    launcher = install(args.dest)
    print(f"installed into {args.dest.resolve()}")
    print(f"run:       {launcher}")
    print(f"uninstall: delete the folder, or run {args.dest.resolve() / 'uninstall.cmd'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
