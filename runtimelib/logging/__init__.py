# SPDX-License-Identifier: MIT
"""Structured logging and audit trail."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


# Context variables for tracing
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)


@dataclass
class LogRecord:
    """Structured log record."""
    timestamp: str
    level: str
    logger: str
    message: str
    trace_id: str | None = None
    session_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({
            "timestamp": self.timestamp,
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            **self.extra,
        }, ensure_ascii=False)


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_record = LogRecord(
            timestamp=datetime.fromtimestamp(record.created).isoformat(),
            level=record.levelname,
            logger=record.name,
            message=record.getMessage(),
            trace_id=trace_id_var.get(),
            session_id=session_id_var.get(),
            extra=getattr(record, "extra", {}),
        )
        return log_record.to_json()


class AuditLogger:
    """Audit trail logger for security-sensitive operations."""

    def __init__(self, audit_file: Path) -> None:
        self.audit_file = audit_file
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(
        self,
        action: str,
        resource: str,
        outcome: str,  # "success", "failure", "denied"
        user: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log an audit event."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "resource": resource,
            "outcome": outcome,
            "user": user or os.environ.get("USER", "unknown"),
            "session_id": session_id_var.get() or "unknown",
            "trace_id": trace_id_var.get() or "unknown",
            "details": details or {},
        }

        with self._lock:
            with open(self.audit_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_tool_execution(
        self,
        tool_name: str,
        args: dict[str, Any],
        success: bool,
        error: str | None = None,
    ) -> None:
        """Log tool execution for audit."""
        self.log(
            action="tool_execute",
            resource=f"tool:{tool_name}",
            outcome="success" if success else "failure",
            details={"args": args, "error": error},
        )

    def log_permission_check(
        self,
        tool_name: str,
        allowed: bool,
        reason: str = "",
    ) -> None:
        """Log permission check."""
        self.log(
            action="permission_check",
            resource=f"tool:{tool_name}",
            outcome="success" if allowed else "denied",
            details={"reason": reason},
        )

    def log_session_event(
        self,
        event: str,  # "start", "end", "interrupt"
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log session lifecycle event."""
        self.log(
            action="session_event",
            resource=f"session:{event}",
            outcome="success",
            details=details or {},
        )


def setup_logging(
    level: int = logging.INFO,
    json_format: bool = False,
    log_file: Path | None = None,
    audit_file: Path | None = None,
) -> tuple[logging.Logger, Optional[AuditLogger]]:
    """Setup structured logging."""
    logger = logging.getLogger("runtime")
    logger.setLevel(level)
    logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    if json_format:
        console_handler.setFormatter(StructuredFormatter())
    else:
        console_handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        ))
    logger.addHandler(console_handler)

    # File handler
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(StructuredFormatter())
        logger.addHandler(file_handler)

    # Audit logger
    audit_logger = None
    if audit_file:
        audit_logger = AuditLogger(audit_file)

    return logger, audit_logger


def get_logger(name: str = "runtime") -> logging.Logger:
    """Get logger instance."""
    return logging.getLogger(name)


def set_trace_id(trace_id: str | None = None) -> str:
    """Set trace ID for current context."""
    if trace_id is None:
        trace_id = str(uuid.uuid4())[:8]
    trace_id_var.set(trace_id)
    return trace_id


def set_session_id(session_id: str | None) -> str:
    """Set session ID for current context."""
    if session_id is None:
        session_id = str(uuid.uuid4())[:8]
    session_id_var.set(session_id)
    return session_id


def clear_context() -> None:
    """Clear trace and session context."""
    trace_id_var.set(None)
    session_id_var.set(None)