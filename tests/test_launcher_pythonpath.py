# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-opus-5-5
# Licensed under MIT License
"""Launchers never put the current directory on sys.path (no trailing empty PYTHONPATH entry).

CPython reads an empty PYTHONPATH entry as the current directory, which would defeat ``-P``: a planted
``json.py`` / ``argparse.py`` in the folder the user runs ai-rex from would be imported.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_ai_rex.py"

PLANT = "import pathlib; pathlib.Path(r'{marker}').write_text('planted')\n"


def _plant(folder: Path) -> Path:
    marker = folder / "PLANTED.txt"
    for mod in ("json.py", "argparse.py", "shutil.py"):
        (folder / mod).write_text(PLANT.format(marker=marker), encoding="utf-8")
    (folder / "runtimelib").mkdir()
    (folder / "runtimelib" / "__init__.py").write_text(PLANT.format(marker=marker), encoding="utf-8")
    return marker


def _env_without_pythonpath():
    return {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}


def _run_from(folder: Path, launcher: Path, env=None):
    return subprocess.run([str(launcher), "--version"], cwd=folder, capture_output=True, text=True,
                          env=env or _env_without_pythonpath(), timeout=120)


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    dest = tmp_path_factory.mktemp("inst") / "ai-rex"
    r = subprocess.run([sys.executable, "-I", str(SCRIPT), str(dest)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return dest / ("ai-rex.cmd" if sys.platform == "win32" else "ai-rex")


@pytest.mark.parametrize("which", ["installed", "checkout"])
def test_launcher_ignores_planted_modules_in_cwd(tmp_path, installed, which):
    if which == "installed":
        launcher = installed
    else:
        launcher = ROOT / ("ai-rex.cmd" if sys.platform == "win32" else "ai-rex")
    marker = _plant(tmp_path)
    r = _run_from(tmp_path, launcher)
    assert r.stdout.startswith("ai-rex "), (r.stdout, r.stderr)
    assert not marker.exists(), "a module from the current directory was imported"


def test_existing_pythonpath_is_kept(tmp_path, installed):
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "sitecustomize.py").write_text("", encoding="utf-8")
    probe = tmp_path / "probe"
    probe.mkdir()
    marker = _plant(probe)
    env = _env_without_pythonpath()
    env["PYTHONPATH"] = str(extra)
    r = _run_from(probe, installed, env)
    assert r.stdout.startswith("ai-rex "), (r.stdout, r.stderr)
    assert not marker.exists()


@pytest.mark.parametrize("rel", ["ai-rex.cmd", "packaging/ai-rex.cmd.in"])
def test_cmd_launchers_have_no_trailing_separator(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert "dp0;%PYTHONPATH%" not in text and "if defined PYTHONPATH" in text


def test_installer_cmd_template_has_no_trailing_separator():
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        import install_ai_rex
    finally:
        sys.path.pop(0)
    assert "lib;%PYTHONPATH%" not in install_ai_rex.CMD
    assert "if defined PYTHONPATH" in install_ai_rex.CMD
