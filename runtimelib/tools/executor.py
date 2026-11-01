# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Tool executor with timeout and retry."""

from __future__ import annotations

import concurrent.futures
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .base import Tool, ToolResult


@dataclass
class ExecutorConfig:
    """Configuration for tool executor."""
    default_timeout: float = 30.0
    max_retries: int = 0
    retry_on: tuple[type[Exception], ...] = (Exception,)


class ToolExecutor:
    """Execute tools with timeout, retry, and cancellation support."""

    def __init__(self, config: Optional[ExecutorConfig] = None) -> None:
        self.config = config or ExecutorConfig()
        self._cancel_event = threading.Event()

    def execute(
        self,
        tool: Tool,
        args: dict[str, Any],
        timeout: Optional[float] = None,
        on_start: Optional[Callable[[str, dict], None]] = None,
        on_end: Optional[Callable[[ToolResult], None]] = None,
    ) -> ToolResult:
        """Execute a tool with timeout and retry."""
        timeout = timeout or self.config.default_timeout
        self._cancel_event.clear()

        if on_start:
            on_start(tool.name, args)

        last_error = None
        for attempt in range(self.config.max_retries + 1):
            if self._cancel_event.is_set():
                return ToolResult(
                    tool_name=tool.name,
                    success=False,
                    error="Execution cancelled",
                )

            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(tool.execute, **args)
                    try:
                        result = future.result(timeout=timeout)
                        tool_result = ToolResult(
                            tool_name=tool.name,
                            success=True,
                            result=result,
                        )
                        if on_end:
                            on_end(tool_result)
                        return tool_result
                    except concurrent.futures.TimeoutError:
                        future.cancel()
                        raise TimeoutError(f"Tool {tool.name} timed out after {timeout}s")
            except self.config.retry_on as e:
                last_error = e
                if attempt < self.config.max_retries:
                    continue
                break

        tool_result = ToolResult(
            tool_name=tool.name,
            success=False,
            error=str(last_error) if last_error else "Unknown error",
        )
        if on_end:
            on_end(tool_result)
        return tool_result

    def cancel(self) -> None:
        """Cancel current execution."""
        self._cancel_event.set()

    def execute_async(
        self,
        tool: Tool,
        args: dict[str, Any],
        callback: Callable[[ToolResult], None],
        timeout: Optional[float] = None,
    ) -> concurrent.futures.Future:
        """Execute tool asynchronously with callback."""
        def _run():
            result = self.execute(tool, args, timeout=timeout)
            callback(result)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(_run)