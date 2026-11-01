# SPDX-License-Identifier: MIT
"""Tests for config module."""

import tempfile
from pathlib import Path

import pytest

from runtimelib.config import Config


def test_config_load_yaml():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.yaml"
        config_path.write_text("liteLLM:\n  base_url: http://test:4000\n  model: test-model\n")

        config = Config.load(config_path=config_path)
        assert config.get("liteLLM.base_url") == "http://test:4000"
        assert config.get("liteLLM.model") == "test-model"


def test_config_env_override():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.yaml"
        config_path.write_text("liteLLM:\n  base_url: http://yaml:4000\n")

        import os
        # Use double underscore for nested keys
        os.environ["RUNTIME_LITELLM__BASE_URL"] = "http://env:4000"
        try:
            config = Config.load(config_path=config_path)
            assert config.get("liteLLM.base_url") == "http://env:4000"
        finally:
            del os.environ["RUNTIME_LITELLM__BASE_URL"]


def test_config_dotenv():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.yaml"
        env_path = Path(tmpdir) / ".env"
        config_path.write_text("liteLLM:\n  base_url: http://yaml:4000\n")
        env_path.write_text("LITELLM_BASE_URL=http://dotenv:4000\n")

        config = Config.load(config_path=config_path, env_path=env_path)
        # dotenv returns flat keys (LITELLM_BASE_URL), not nested
        # So YAML nested key remains
        assert config.get("liteLLM.base_url") == "http://yaml:4000"
        # But flat key is also available
        assert config.get("LITELLM_BASE_URL") == "http://dotenv:4000"


def test_config_nested_env():
    import os
    # Use double underscore for nested keys
    os.environ["RUNTIME_LITELLM__API_KEY"] = "test-key"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("liteLLM:\n  base_url: http://test:4000\n")

            config = Config.load(config_path=config_path)
            assert config.get("liteLLM.api_key") == "test-key"
    finally:
        del os.environ["RUNTIME_LITELLM__API_KEY"]


def test_config_get_default():
    config = Config({})
    assert config.get("nonexistent", "default") == "default"
    assert config.get("nonexistent") is None