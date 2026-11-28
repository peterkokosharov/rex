# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov
# AI models: claude-opus-5-5
"""Guard entrypoint _safe_path: a sibling directory sharing the workspace prefix is outside it."""

import importlib.util
import sys
from pathlib import Path

import pytest

ENTRY = Path(__file__).resolve().parent.parent / "guard" / "entrypoint.py"


@pytest.fixture(scope="module")
def entry():
    sys.path.insert(0, str(ENTRY.parent.parent / "vendor"))
    spec = importlib.util.spec_from_file_location("guard_entrypoint_safe_path", ENTRY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sibling_with_same_prefix_is_rejected(entry, tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (tmp_path / "workspace2").mkdir()
    with pytest.raises(entry.GuardError):
        entry._safe_path(ws, "../workspace2")
    with pytest.raises(entry.GuardError):
        entry._safe_path(ws, str(tmp_path / "workspace2" / "x"))


@pytest.mark.parametrize("bad", ["..", "../x", "/etc/passwd", "a/../../x"])
def test_traversal_is_rejected(entry, tmp_path, bad):
    with pytest.raises(entry.GuardError):
        entry._safe_path(tmp_path / "ws", bad)


@pytest.mark.parametrize("good", [".", "a", "a/b/../c", "./x.txt"])
def test_inside_paths_are_accepted(entry, tmp_path, good):
    ws = tmp_path / "ws"
    ws.mkdir()
    got = entry._safe_path(ws, good)
    assert got == (ws / good).resolve()


def test_symlink_out_of_workspace_is_rejected(entry, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    out = tmp_path / "outside"
    out.mkdir()
    try:
        (ws / "link").symlink_to(out, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no symlink permission")
    with pytest.raises(entry.GuardError):
        entry._safe_path(ws, "link/secret")
