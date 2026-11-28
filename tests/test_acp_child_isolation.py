# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov
# AI models: claude-sonnet-5-5, claude-opus-5-5
"""The ACP child must not import modules from the (untrusted) project directory."""

import os
import subprocess
import sys

from runtimelib.acp import ACPServer
from runtimelib.acp import server as acp_server


def test_child_does_not_import_from_project_cwd(tmp_path):
    plant = tmp_path / "runtime"
    plant.mkdir()
    (plant / "__init__.py").write_text("")
    (plant / "__main__.py").write_text("print('PWNED-BY-PROJECT')\n")
    cmd = ACPServer({}).child_command() + ["--help"]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(acp_server.REX_ROOT)
    out = subprocess.run(cmd, cwd=plant.parent, env=env, capture_output=True, text=True, timeout=120)
    assert "PWNED-BY-PROJECT" not in out.stdout + out.stderr
    assert out.returncode == 0, out.stderr[-500:]  # the real runtime ran, not just "nothing printed"
    assert sys.executable in cmd[0]


class _Captured(Exception):
    pass


def _spawn_args(monkeypatch, inherited_pythonpath, cwd):
    """Run ACPServer._spawn up to Popen and return (cmd, env) it would use."""
    monkeypatch.setenv("PYTHONPATH", inherited_pythonpath)
    seen = {}

    def fake_popen(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        raise _Captured

    with monkeypatch.context() as m:  # subprocess is the shared module: undo before the real run
        m.setattr(acp_server.subprocess, "Popen", fake_popen)
        try:
            ACPServer({})._spawn("s1", str(cwd))
        except _Captured:
            pass
    return seen["cmd"], seen["env"]


def test_inherited_pythonpath_cannot_put_project_cwd_back(tmp_path, monkeypatch):
    # An empty or relative PYTHONPATH entry means "the child's cwd" = the untrusted project.
    plant = tmp_path / "runtime"
    plant.mkdir()
    (plant / "__init__.py").write_text("")
    (plant / "__main__.py").write_text("print('PWNED-BY-PROJECT')\n")
    for inherited in (os.pathsep + str(tmp_path / "nonexistent"), ".", "runtime" + os.pathsep + "x"):
        cmd, env = _spawn_args(monkeypatch, inherited, tmp_path)
        out = subprocess.run(cmd + ["--help"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
        assert "PWNED-BY-PROJECT" not in out.stdout + out.stderr, inherited
        assert out.returncode == 0, out.stderr[-500:]


def test_absolute_inherited_pythonpath_is_kept(tmp_path, monkeypatch):
    extra = str(tmp_path / "libs")
    _, env = _spawn_args(monkeypatch, extra, tmp_path)
    assert env["PYTHONPATH"].split(os.pathsep) == [str(acp_server.REX_ROOT), extra]
