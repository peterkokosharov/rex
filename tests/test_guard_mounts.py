# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov
# AI models: claude-sonnet-5-5
"""The guard container must not get the API key / kiko database mounted."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from runtimelib.guard import GuardClient


def test_start_mounts_no_secret_dirs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config" / "ai-rex").mkdir(parents=True)
    (home / ".kiko").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    client = GuardClient(image="test:latest", workspace=tmp_path)
    proc = MagicMock()
    proc.poll.return_value = None
    with patch("subprocess.Popen", return_value=proc) as popen, \
            patch.object(GuardClient, "_wait_ready"), \
            patch.object(GuardClient, "_resolve_image", return_value="test:latest"):
        client.start()
    joined = " ".join(popen.call_args[0][0])
    assert ".config/ai-rex" not in joined
    assert ".kiko" not in joined
    assert str(home) not in joined


def test_compose_has_no_secret_mounts():
    text = (Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text(encoding="utf-8")
    assert ".config/ai-rex" not in text and "~/.kiko" not in text
