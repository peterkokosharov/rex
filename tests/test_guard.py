# SPDX-License-Identifier: MIT
"""Tests for guard client (mocked)."""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from runtime.guard import GuardClient


def test_guard_client_creation():
    client = GuardClient(image="test:latest", workspace="/tmp/test")
    assert client.image == "test:latest"
    assert str(client.workspace) == "/tmp/test"


def test_guard_client_bash_exec_mock():
    """Test bash_exec with mocked subprocess and reader thread."""
    client = GuardClient(image="test:latest", workspace="/tmp/test")

    # Mock the subprocess
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()

    with patch("subprocess.Popen", return_value=mock_proc):
        client.start()
        # Mock _send_request to avoid threading complexity
        with patch.object(client, '_send_request') as mock_send:
            mock_send.return_value = {"exit_code": 0, "stdout": "hello\n", "stderr": ""}
            result = client.bash_exec("echo hello")
            assert result["exit_code"] == 0
            assert result["stdout"] == "hello\n"
            mock_send.assert_called_once_with("bash.exec", {
                "command": "echo hello",
                "timeout": 30,
                "cwd": ".",
                "env": {},
            }, timeout=35.0)


def test_guard_client_fs_operations():
    """Test file operations with mocked _send_request."""
    client = GuardClient(image="test:latest", workspace="/tmp/test")

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()

    with patch("subprocess.Popen", return_value=mock_proc):
        client.start()

        with patch.object(client, '_send_request') as mock_send:
            # Test fs_read
            mock_send.return_value = {"content": "file content"}
            content = client.fs_read("test.txt")
            assert content == "file content"
            mock_send.assert_called_with("fs.read", {"path": "test.txt", "encoding": "utf-8"})

            # Test fs_write
            mock_send.return_value = {"ok": True}
            client.fs_write("test.txt", "new content")
            mock_send.assert_called_with("fs.write", {"path": "test.txt", "content": "new content", "mode": 0o644})

            # Test fs_list
            mock_send.return_value = {"files": ["a.txt", "b.py"]}
            files = client.fs_list(".", recursive=False)
            assert files == ["a.txt", "b.py"]


def test_guard_client_error_handling():
    """Test error response handling."""
    client = GuardClient(image="test:latest", workspace="/tmp/test")

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()

    with patch("subprocess.Popen", return_value=mock_proc):
        client.start()

        with patch.object(client, '_send_request') as mock_send:
            # Error response
            mock_send.side_effect = RuntimeError("Guard error: File not found: test.txt")

            with pytest.raises(RuntimeError, match="File not found"):
                client.fs_read("test.txt")


def test_guard_client_start_stop():
    """Test client start/stop lifecycle."""
    client = GuardClient(image="test:latest", workspace="/tmp/test")

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()

    with patch("subprocess.Popen", return_value=mock_proc):
        client.start()
        assert client._started is True
        assert client._proc is not None

        client.stop()
        assert client._started is False
        mock_proc.terminate.assert_called_once()