# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra
# Licensed under MIT License

"""Tests for Orchestrator spawn and agent functionality."""

from __future__ import annotations

import asyncio
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch

import pytest

pytestmark = pytest.mark.skip(reason="orchestrator parked: unreviewed upstream, see docs/DESIGN-orchestration.md")

from orchestrator import Orchestrator, AgentContext, SubagentHandle
from orchestrator.policy import RolePolicy, get_role_policy, DEFAULT_POLICIES
from orchestrator.taskspawn import TaskSpawnTool, SpawnRequest


class TestRolePolicy:
    """Test role policies."""

    def test_default_policies_exist(self):
        """All default roles should have policies."""
        expected_roles = {"ORCHESTRATOR", "CODER", "TESTER", "REVIEWER", "DEBUGGER", "RESEARCHER"}
        assert set(DEFAULT_POLICIES.keys()) == expected_roles

    def test_coder_policy(self):
        """CODER policy should have correct defaults."""
        policy = get_role_policy("CODER")
        assert policy.name == "CODER"
        assert policy.model_class == "SONNET"
        assert policy.max_steps == 25
        assert "fileops" in policy.allowed_tools
        assert "bash" in policy.allowed_tools
        assert "git" in policy.allowed_tools
        assert policy.sandbox.network == "bridge"

    def test_tester_policy(self):
        """TESTER policy should deny write operations."""
        policy = get_role_policy("TESTER")
        assert policy.name == "TESTER"
        assert policy.model_class == "HAIKU"
        assert "fileops:write" in policy.permissions.get("deny", [])
        assert "git:push" in policy.permissions.get("deny", [])

    def test_reviewer_policy(self):
        """REVIEWER policy should be read-only."""
        policy = get_role_policy("REVIEWER")
        assert policy.name == "REVIEWER"
        assert policy.model_class == "OPUS"
        assert "fileops:write" in policy.permissions.get("deny", [])
        assert "bash:*" in policy.permissions.get("deny", [])

    def test_fallback_to_coder(self):
        """Unknown role should fall back to CODER."""
        policy = get_role_policy("UNKNOWN_ROLE")
        assert policy.name == "CODER"


class TestTaskSpawnTool:
    """Test TaskSpawnTool functionality."""

    @pytest.fixture
    def mock_orchestrator(self):
        """Create a mock orchestrator."""
        orch = Mock()
        orch.get_policy.return_value = Mock(
            model_class="SONNET",
            allowed_tools=["fileops", "bash", "git"],
            max_steps=25,
        )
        return orch

    @pytest.fixture
    def tool(self, mock_orchestrator):
        """Create TaskSpawnTool with mock orchestrator."""
        return TaskSpawnTool(orchestrator=mock_orchestrator)

    def test_schema(self, tool):
        """Tool should have correct schema."""
        schema = tool.schema
        assert schema["name"] == "task"
        assert "prompt" in schema["parameters"]["properties"]
        assert "role" in schema["parameters"]["properties"]
        assert "tools" in schema["parameters"]["properties"]
        assert "model_class" in schema["parameters"]["properties"]
        assert "max_steps" in schema["parameters"]["properties"]
        assert "worktree" in schema["parameters"]["properties"]
        assert schema["parameters"]["required"] == ["prompt"]

    def test_execute_missing_prompt(self, tool):
        """Execute should fail without prompt."""
        result = tool.execute()
        assert result.success is False
        assert "prompt" in result.error.lower()

    def test_execute_with_orchestrator(self, tool, mock_orchestrator):
        """Execute with orchestrator should return spawned result."""
        result = tool.execute(
            prompt="Write tests for login",
            role="TESTER",
        )
        assert result.success is True
        assert result.result["status"] == "spawned"
        assert "agent_id" in result.result
        assert result.result["role"] == "TESTER"

    def test_execute_without_orchestrator(self, tool):
        """Execute without orchestrator should queue for external."""
        tool.orchestrator = None
        result = tool.execute(prompt="Test task", role="CODER")
        assert result.success is True
        assert result.result["status"] == "pending"
        assert "agent_id" in result.result
        assert "Orchestrator not injected" in result.result["note"]


class TestOrchestrator:
    """Test Orchestrator functionality."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for tests."""
        temp = tempfile.mkdtemp()
        yield Path(temp)
        shutil.rmtree(temp, ignore_errors=True)

    @pytest.fixture
    async def orchestrator(self, temp_dir):
        """Create orchestrator with temp git repo."""
        # Initialize git repo
        subprocess.run(["git", "init", "-b", "main"], cwd=temp_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=temp_dir, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=temp_dir, check=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "Initial"], cwd=temp_dir, check=True, capture_output=True)

        orch = Orchestrator(project_root=temp_dir)
        await orch.initialize()
        yield orch
        await orch.cleanup()

    @pytest.mark.asyncio
    async def test_initialize_creates_session(self, temp_dir):
        """Initialize should create session directory."""
        orch = Orchestrator(project_root=temp_dir)
        await orch.initialize()
        assert orch.session_dir.exists()
        assert (orch.session_dir / "history.jsonl").exists()
        await orch.cleanup()

    @pytest.mark.asyncio
    async def test_spawn_subagent(self, orchestrator):
        """Spawning subagent should create handle."""
        handle = await orchestrator.spawn_subagent(
            task="Write hello world",
            role="CODER",
        )
        assert isinstance(handle, SubagentHandle)
        assert handle.agent_id.startswith("coder-")
        assert handle.context.role == "CODER"
        assert handle.context.task == "Write hello world"
        assert handle.context.depth == 1

    @pytest.mark.asyncio
    async def test_spawn_subagent_with_worktree(self, orchestrator):
        """Spawning with worktree should create git worktree."""
        handle = await orchestrator.spawn_subagent(
            task="Test task",
            role="CODER",
            worktree=True,
        )
        assert handle.context.worktree_path != str(orchestrator.project_root)
        assert Path(handle.context.worktree_path).exists()
        # Worktree should be in .worktrees/
        assert ".worktrees" in handle.context.worktree_path

    @pytest.mark.asyncio
    async def test_spawn_subagent_inherits_depth(self, orchestrator):
        """Subagent should inherit depth from parent."""
        # Create parent context
        parent = AgentContext(
            agent_id="coder-001",
            role="CODER",
            worktree_path=str(orchestrator.project_root),
            session_id="test",
            depth=1,
        )
        handle = await orchestrator.spawn_subagent(
            task="Child task",
            role="TESTER",
            parent_context=parent,
        )
        assert handle.context.depth == 2
        assert handle.context.parent_id == "coder-001"

    @pytest.mark.asyncio
    async def test_run_agent(self, orchestrator):
        """Running agent should complete task."""
        with patch.object(orchestrator, '_run_subagent', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = None
            # We can't easily test full run_agent without mocking the runtime
            # This tests the spawn part
            handle = await orchestrator.spawn_subagent(
                task="Test",
                role="CODER",
            )
            result = await handle.wait()
            assert isinstance(result, dict) or hasattr(result, 'success')

    @pytest.mark.asyncio
    async def test_create_worktree(self, orchestrator):
        """Creating worktree should work."""
        worktree = await orchestrator._create_worktree("test-agent")
        assert Path(worktree).exists()
        assert "test-agent" in worktree

    @pytest.mark.asyncio
    async def test_commit_and_merge(self, orchestrator):
        """Commit and merge should work."""
        # Create worktree first
        handle = await orchestrator.spawn_subagent(
            task="Test",
            role="CODER",
            worktree=True,
        )
        worktree_path = handle.context.worktree_path

        # Make a change
        test_file = Path(worktree_path) / "test.txt"
        test_file.write_text("hello")

        result = await orchestrator.commit_and_merge(
            agent_id=handle.agent_id,
            message="Add test file",
        )
        assert result.success is True
        assert result.commit_hash is not None


class TestSubagentHandle:
    """Test SubagentHandle functionality."""

    @pytest.fixture
    def mock_orchestrator(self):
        return Mock()

    def test_handle_creation(self, mock_orchestrator):
        """Handle should store context and orchestrator."""
        context = AgentContext(
            agent_id="test-001",
            role="CODER",
            worktree_path="/tmp/test",
            session_id="session-1",
        )
        handle = SubagentHandle(
            agent_id="test-001",
            context=context,
            _orchestrator=mock_orchestrator,
        )
        assert handle.agent_id == "test-001"
        assert handle.context.role == "CODER"

    @pytest.mark.asyncio
    async def test_wait(self, mock_orchestrator):
        """Wait should complete when task done."""
        context = AgentContext(
            agent_id="test-001",
            role="CODER",
            worktree_path="/tmp/test",
            session_id="session-1",
        )
        handle = SubagentHandle(
            agent_id="test-001",
            context=context,
            _orchestrator=mock_orchestrator,
        )
        # Simulate completion
        handle._result = {"success": True, "output": "Done"}
        handle._completed.set()
        result = await handle.wait()
        assert result["success"] is True

    def test_cancel(self, mock_orchestrator):
        """Cancel should cancel the task."""
        context = AgentContext(
            agent_id="test-001",
            role="CODER",
            worktree_path="/tmp/test",
            session_id="session-1",
        )
        handle = SubagentHandle(
            agent_id="test-001",
            context=context,
            _orchestrator=mock_orchestrator,
        )
        mock_task = AsyncMock()
        handle._task = mock_task
        handle.cancel()
        assert handle._cancelled is True
        mock_task.cancel.assert_called_once()


class TestPolicyYaml:
    """Test policy YAML loading."""

    def test_load_policy_file(self, temp_dir):
        """Loading policy.yaml should merge with defaults."""
        yaml_content = """
roles:
  CUSTOM_ROLE:
    model: "test/model"
    model_class: "CUSTOM"
    max_steps: 10
    allowed_tools: ["fileops"]
    sandbox:
      network: "bridge"
      memory: "1G"
    permissions:
      allow: ["fileops:*"]
      deny: ["bash:*"]
"""
        yaml_file = temp_dir / "policy.yaml"
        yaml_file.write_text(yaml_content)

        from orchestrator.policy import load_policy_file
        policies = load_policy_file(yaml_file)

        assert "CUSTOM_ROLE" in policies
        policy = policies["CUSTOM_ROLE"]
        assert policy.model == "test/model"
        assert policy.model_class == "CUSTOM"
        assert policy.max_steps == 10
        assert policy.allowed_tools == ["fileops"]
        assert policy.sandbox.network == "bridge"

    def test_load_missing_file_returns_defaults(self, temp_dir):
        """Missing file should return defaults."""
        from orchestrator.policy import load_policy_file
        policies = load_policy_file(temp_dir / "nonexistent.yaml")
        assert policies == DEFAULT_POLICIES


# Run tests with: pytest tests/test_orchestrator_spawn.py -v
if __name__ == "__main__":
    pytest.main([__file__, "-v"])