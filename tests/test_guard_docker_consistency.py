# SPDX-License-Identifier: MIT
"""Guard image consistency: no stale kikolib path, no hard-coded version outside the single source."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STALE_IMPORT = "runtime" + ".kikolib"
OLD_VERSION = "0." + "2.2"
SUFFIXES = {".py", ".sh", ".yml", ".yaml", ".example"}


def _files():
    targets = [ROOT / "Dockerfile", ROOT / "docker-compose.yml"]
    for d in ("guard", "scripts", "packaging", "runtime", "runtimelib"):
        for p in (ROOT / d).rglob("*"):
            if p.is_file() and p.suffix in SUFFIXES and "__pycache__" not in p.parts:
                targets.append(p)
    return [p for p in targets if p.is_file()]


def test_no_stale_kikolib_path():
    bad = [str(p.relative_to(ROOT)) for p in _files() if STALE_IMPORT in p.read_text(encoding="utf-8", errors="replace")]
    assert not bad, f"stale kikolib path in: {bad}"


def test_no_hardcoded_version():
    bad = []
    for p in _files():
        if p.name == "__init__.py" and p.parent.name == "runtimelib":
            continue  # the single source of the version
        if OLD_VERSION in p.read_text(encoding="utf-8", errors="replace"):
            bad.append(str(p.relative_to(ROOT)))
    assert not bad, f"hard-coded version in: {bad}"


def test_pyproject_version_matches_source():
    src = (ROOT / "runtimelib" / "__init__.py").read_text(encoding="utf-8")
    ver = re.search(r'^__version__ = "(.+)"$', src, re.M).group(1)
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(rf'^version = "{re.escape(ver)}"$', pyproject, re.M)
