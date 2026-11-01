# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Agent state management with checkpoint persistence."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..llm import ChatMessage


@dataclass
class AgentState:
    """Current state of the agent."""
    messages: list[ChatMessage] = field(default_factory=list)
    step: int = 0
    max_steps: int = 20
    current_tool_calls: list[Any] = field(default_factory=list)
    interrupted: bool = False
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost: float = 0.0
    session_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize state to dict."""
        return {
            "messages": [msg.to_dict() for msg in self.messages],
            "step": self.step,
            "max_steps": self.max_steps,
            "interrupted": self.interrupted,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_cost": self.total_cost,
            "session_id": self.session_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentState":
        """Deserialize state from dict."""
        state = cls()
        state.messages = [ChatMessage(**msg) for msg in data.get("messages", [])]
        state.step = data.get("step", 0)
        state.max_steps = data.get("max_steps", 20)
        state.interrupted = data.get("interrupted", False)
        state.total_tokens_in = data.get("total_tokens_in", 0)
        state.total_tokens_out = data.get("total_tokens_out", 0)
        state.total_cost = data.get("total_cost", 0.0)
        state.session_id = data.get("session_id", "")
        state.metadata = data.get("metadata", {})
        return state


class CheckpointManager:
    """Manages agent state checkpoints."""

    def __init__(self, checkpoint_dir: Path, interval: int = 10) -> None:
        self.checkpoint_dir = checkpoint_dir
        self.interval = interval
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._last_checkpoint_step = 0

    def should_checkpoint(self, step: int) -> bool:
        """Check if we should create a checkpoint."""
        return step - self._last_checkpoint_step >= self.interval

    def save_checkpoint(self, state: AgentState, name: str | None = None) -> Path:
        """Save agent state checkpoint."""
        if name is None:
            name = f"checkpoint_step_{state.step}.json"
        path = self.checkpoint_dir / name
        path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self._last_checkpoint_step = state.step
        return path

    def load_checkpoint(self, name: str) -> AgentState:
        """Load agent state from checkpoint."""
        path = self.checkpoint_dir / name
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return AgentState.from_dict(data)

    def list_checkpoints(self) -> list[Path]:
        """List available checkpoints."""
        return sorted(self.checkpoint_dir.glob("checkpoint_*.json"))

    def latest_checkpoint(self) -> Path | None:
        """Get latest checkpoint."""
        checkpoints = self.list_checkpoints()
        return checkpoints[-1] if checkpoints else None

    def cleanup_old(self, keep: int = 5) -> None:
        """Remove old checkpoints, keeping only the latest N."""
        checkpoints = self.list_checkpoints()
        for cp in checkpoints[:-keep]:
            cp.unlink(missing_ok=True)