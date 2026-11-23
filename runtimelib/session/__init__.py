# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-sonnet-5-5
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


def default_sessions_dir() -> Path:
    """Directory for per-session files (config ``session.dir``)."""
    from ..config import get_config
    return Path(get_config().get("session.dir", "~/.config/runtime/sessions")).expanduser()


@dataclass
class SessionInfo:
    """Summary of one stored session (for listing)."""
    id: str
    path: Path
    cwd: str
    model: str
    started: str
    updated: str
    title: str
    messages: int


def _read_lines(path: Path) -> list[dict[str, Any]]:
    """Parse a session file; bad lines (e.g. a torn last write) are skipped."""
    out: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _replay(records: list[dict[str, Any]]) -> tuple[list[ChatMessage], str, int]:
    """Rebuild (messages, title, message count) from records.

    A ``compact`` record replaces everything before it.
    """
    messages: list[ChatMessage] = []
    title = ""
    for rec in records:
        kind = rec.get("type")
        try:
            if kind == "message":
                messages.append(ChatMessage.from_dict(rec["m"]))
            elif kind == "compact":
                messages = [ChatMessage.from_dict(m) for m in rec["messages"]]
            elif kind == "title" and not title:
                title = str(rec.get("title", ""))
        except (KeyError, TypeError, AttributeError):
            continue
    return messages, title, len(messages)


class SessionStore:
    """One JSONL file per session: ``<dir>/<id>.jsonl``.

    Lines: ``meta`` (first), ``message`` (one per ChatMessage, exact replay
    including tool calls and results), ``compact`` (snapshot after a
    compaction; replay starts from the last one) and ``title``.
    """

    def __init__(self, directory: Path | str, session_id: str | None = None,
                 cwd: str | None = None, model: str = "") -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.id = session_id or datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.path = self.directory / f"{self.id}.jsonl"
        self._titled = False
        if self.path.exists():
            self._titled = any(r.get("type") == "title" for r in _read_lines(self.path))
        else:
            self._write({"type": "meta", "id": self.id, "cwd": cwd or os.getcwd(),
                         "model": model, "started": datetime.now().isoformat()})

    def _write(self, rec: dict[str, Any]) -> None:
        prefix = ""
        try:
            with open(self.path, "rb") as f:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    prefix = "\n"  # torn last line: do not glue onto it
        except OSError:
            pass
        with open(self.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(prefix + json.dumps(rec, ensure_ascii=False) + "\n")

    def append_message(self, msg: ChatMessage) -> None:
        self._write({"type": "message", "m": msg.to_dict()})
        if not self._titled and msg.role == "user" and msg.content:
            first = msg.content.strip().splitlines()[0][:80] if msg.content.strip() else ""
            if first:
                self._write({"type": "title", "title": first})
                self._titled = True

    def append_compact(self, messages: list[ChatMessage]) -> None:
        self._write({"type": "compact", "messages": [m.to_dict() for m in messages]})

    def load_messages(self) -> list[ChatMessage]:
        return _replay(_read_lines(self.path))[0]

    @classmethod
    def open(cls, directory: Path | str, session_id: str) -> "SessionStore":
        """Open an existing session to keep appending; FileNotFoundError if absent."""
        path = Path(directory) / f"{session_id}.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"session not found: {session_id}")
        return cls(directory, session_id=session_id)

    @staticmethod
    def list_sessions(directory: Path | str) -> list[SessionInfo]:
        """All sessions, newest update first."""
        d = Path(directory)
        if not d.is_dir():
            return []
        infos: list[SessionInfo] = []
        for p in d.glob("*.jsonl"):
            recs = _read_lines(p)
            meta = next((r for r in recs if r.get("type") == "meta"), None)
            if meta is None:
                continue
            _, title, count = _replay(recs)
            infos.append(SessionInfo(
                id=str(meta.get("id") or p.stem), path=p,
                cwd=str(meta.get("cwd", "")), model=str(meta.get("model", "")),
                started=str(meta.get("started", "")),
                updated=datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
                title=title, messages=count,
            ))
        infos.sort(key=lambda i: (i.path.stat().st_mtime_ns, i.id), reverse=True)
        return infos

    @staticmethod
    def latest_for_cwd(directory: Path | str, cwd: str | None = None) -> SessionInfo | None:
        """Most recently updated session whose cwd equals ``cwd`` (default: current)."""
        want = os.path.normcase(os.path.abspath(cwd or os.getcwd()))
        for info in SessionStore.list_sessions(directory):
            if info.cwd and os.path.normcase(os.path.abspath(info.cwd)) == want:
                return info
        return None


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