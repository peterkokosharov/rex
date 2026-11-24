# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
"""vendor/kikolib must be the pinned, unmodified release copy (kiko repo, tag kikolib-v0.2.1)."""

from __future__ import annotations

import hashlib
from pathlib import Path

PINNED_VERSION = "0.2.1"
PKG = Path(__file__).resolve().parent.parent / "vendor" / "kikolib"
MANIFEST = "SHA256SUMS"


def _have() -> dict[str, str]:
    out = {}
    for p in sorted(PKG.rglob("*")):
        rel = p.relative_to(PKG)
        if not p.is_file() or "__pycache__" in rel.parts or rel.as_posix() == MANIFEST:
            continue
        out[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _want() -> dict[str, str]:
    want = {}
    for line in (PKG / MANIFEST).read_text(encoding="utf-8").splitlines():
        h, _, n = line.partition("  ")
        want[n] = h
    return want


def test_manifest_matches_files():
    want, have = _want(), _have()
    problems = [f"missing: {n}" for n in sorted(want.keys() - have.keys())]
    problems += [f"not in manifest: {n}" for n in sorted(have.keys() - want.keys())]
    problems += [f"changed: {n}" for n in sorted(want.keys() & have.keys()) if want[n] != have[n]]
    assert not problems, "vendor/kikolib was edited; fix it in the kiko repo and re-copy: " + "; ".join(problems)


def test_pinned_version():
    import kikolib

    assert Path(kikolib.__file__).resolve().parent == PKG.resolve()
    assert kikolib.__version__ == PINNED_VERSION


def test_http_backend_pinned_to_stdlib():
    import runtimelib  # noqa: F401  (pins the backend at import)
    from kikolib import http
    from kikolib.http import StdlibBackend

    assert isinstance(http.client(), StdlibBackend)


def test_old_kikolib_is_rejected(tmp_path):
    import os
    import subprocess
    import sys

    fake = tmp_path / "kikolib"
    fake.mkdir()
    (fake / "__init__.py").write_text('__version__ = "0.1.9"\n', encoding="utf-8")
    root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(tmp_path) + os.pathsep + str(root)}
    r = subprocess.run([sys.executable, "-c", "import runtimelib"], env=env,
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode != 0
    assert "rex needs kikolib 0.2.x; found 0.1.9" in r.stderr
    assert str(fake) in r.stderr


def test_pyproject_packages_kikolib_from_vendor():
    import tomllib

    root = Path(__file__).resolve().parent.parent
    cfg = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    find = cfg["tool"]["setuptools"]["packages"]["find"]
    assert "vendor" in find["where"]
    assert "kikolib*" in find["include"]


def test_yaml_config_is_read_only(tmp_path):
    import pytest
    from runtimelib.config import Config
    from runtimelib.config.sources import FileSource

    with pytest.raises(NotImplementedError, match="read-only"):
        FileSource(tmp_path / "c.yaml").save({"a": 1})
    with pytest.raises(NotImplementedError, match="read-only"):
        Config({}).save(tmp_path / "c.yaml")
