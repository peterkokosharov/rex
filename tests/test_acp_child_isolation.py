# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov
# AI models: claude-sonnet-5-5
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
    assert sys.executable in cmd[0]
