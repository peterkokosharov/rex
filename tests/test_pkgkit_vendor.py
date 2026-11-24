# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5
"""packaging/vendor/pkgkit must be the pinned, unmodified release copy (kiko-src, tag pkgkit-v0.1.0)."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "packaging" / "vendor" / "pkgkit"
PINNED_VERSION = "0.1.0"


def test_manifest_check_passes():
    r = subprocess.run([sys.executable, "-I", str(KIT / "manifest.py"), "check", str(KIT)],
                       capture_output=True, text=True)
    assert r.returncode == 0, "vendor/pkgkit was edited; fix it in kiko-src and re-copy:\n" + r.stdout + r.stderr


def test_pinned_version():
    m = re.search(r'__version__\s*=\s*"([^"]+)"', (KIT / "__init__.py").read_text(encoding="utf-8"))
    assert m and m.group(1) == PINNED_VERSION


def test_app_toml_is_consistent():
    cfg = tomllib.loads((ROOT / "packaging" / "app.toml").read_text(encoding="utf-8"))
    assert cfg["app"]["name"] == "ai-rex"
    assert cfg["payload"]["dir"] == "rex"  # Layka and Sisi look for the folder "rex"
    assert cfg["app"]["min_python"] == "3.11"
    assert f'>={cfg["app"]["min_python"]}' == tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["requires-python"]
    for inc in cfg["payload"]["include"]:
        assert (ROOT / inc).exists(), inc
