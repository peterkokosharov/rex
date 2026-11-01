# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Session management - JSONL persistence with checkpoints."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..llm import ChatMessage
from ..agent.state import AgentState, CheckpointManager


@dataclass
class SessionEntry:
    """Single session entry (JSONL line)."""
    timestamp: str
    type: str  # "user", "assistant", "tool", "system", "checkpoint"
    data: dict[str, Any]
    session_id: str

    def to_jsonl(self) -> str:
        return json.dumps({
            "timestamp": self.timestamp,
            "type": self.type,
            "data": self.data,
            "session_id": self.session_id,
        }, ensure_ascii=False)

    @classmethod
    def from_jsonl(cls, line: str) -> "SessionEntry":
        data = json.loads(line)
        return cls(
            timestamp=data["timestamp"],
            type=data["type"],
            data=data["data"],
            session_id=data["session_id"],
        )


class SessionManager:
    """Manages session persistence and history."""

    def __init__(self, history_file: Path, checkpoint_dir: Path | None = None) -> None:
        self.history_file = history_file
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = str(uuid.uuid4())[:8]
        self._entries: list[SessionEntry] = []
        self._load()

        self.checkpoint_manager = None
        if checkpoint_dir:
            self.checkpoint_manager = CheckpointManager(checkpoint_dir)

    def _load(self) -> None:
        """Load existing history."""
        if self.history_file.exists():
            for line in self.history_file.read_text(encoding="utf-8").strip().split("\n"):
                if line.strip():
                    try:
                        self._entries.append(SessionEntry.from_jsonl(line))
                    except json.JSONDecodeError:
                        pass

    def append(self, entry_type: str, data: dict[str, Any]) -> None:
        """Add entry to session."""
        entry = SessionEntry(
            timestamp=datetime.now().isoformat(),
            type=entry_type,
            data=data,
            session_id=self.session_id,
        )
        self._entries.append(entry)
        self._write_entry(entry)

    def _write_entry(self, entry: SessionEntry) -> None:
        """Write single entry to JSONL file."""
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.to_jsonl() + "\n")

    def get_history(self, limit: int | None = None) -> list[SessionEntry]:
        """Get session history."""
        if limit:
            return self._entries[-limit:]
        return self._entries

    def get_messages(self) -> list[ChatMessage]:
        """Reconstruct messages from history."""
        messages = []
        for entry in self._entries:
            if entry.type in ("user", "assistant", "tool", "system"):
                messages.append(ChatMessage(**entry.data))
        return messages

    def save_state(self, state: AgentState) -> Path | None:
        """Save agent state checkpoint."""
        if self.checkpoint_manager:
            return self.checkpoint_manager.save_checkpoint(state)
        return None

    def load_latest_state(self) -> AgentState | None:
        """Load latest agent state from checkpoint."""
        if self.checkpoint_manager:
            latest = self.checkpoint_manager.latest_checkpoint()
            if latest:
                return self.checkpoint_manager.load_checkpoint(latest.name)
        return None

    def new_session(self) -> str:
        """Start a new session."""
        self.session_id = str(uuid.uuid4())[:8]
        self._entries = []
        return self.session_id

    def export_json(self, path: Path) -> None:
        """Export full session as JSON."""
        data = {
            "session_id": self.session_id,
            "entries": [
                {
                    "timestamp": e.timestamp,
                    "type": e.type,
                    "data": e.data,
                }
                for e in self._entries
            ],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def create_session_manager(
    history_path: str | None = None,
    checkpoint_dir: str | None = None,
) -> SessionManager:
    """Factory function to create session manager from config paths."""
    from ..config import get_config
    config = get_config()

    history_file = Path(history_path or config.get("session.history_file", "~/.config/runtime/history.jsonl")).expanduser()
    checkpoint_path = Path(checkpoint_dir or "~/.config/runtime/checkpoints").expanduser() if checkpoint_dir else None

    return SessionManager(history_file, checkpoint_path)