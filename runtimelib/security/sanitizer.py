# SPDX-License-Identifier: MIT
"""PromptSanitizer - Defense-in-depth against prompt injection.

Architecture:
1. Structural validation (InputValidator) - removes control chars, zero-width chars, normalizes unicode
2. Zone separation (PromptSanitizer) - strict separation of SYSTEM/CONTEXT/USER/TOOL_OUTPUT zones
3. No content-based filtering - only structural protection
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PromptZone(Enum):
    """Prompt zones with different trust levels."""
    SYSTEM = "system"           # Static, immutable, from config
    CONTEXT = "context"         # Structured data (JSON), validated schema
    USER = "user"               # Raw user input, NEVER interpreted as instructions
    TOOL_OUTPUT = "tool_output" # Tool results, treated as data


@dataclass
class PromptSegment:
    """A segment of the prompt with explicit zone."""
    zone: PromptZone
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class StructuredPromptBuilder:
    """Builds prompts with strict zone separation.

    CRITICAL: The LLM never sees concatenated strings.
    Zones are sent as structured messages (OpenAI format):
    - SYSTEM zone → system message (immutable)
    - CONTEXT zone → user message with [CONTEXT] prefix + JSON
    - USER zone → user message (raw, never parsed as instructions)
    - TOOL_OUTPUT zone → tool message (function result)
    """

    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt
        self.segments: list[PromptSegment] = []

    def add_context(self, data: dict[str, Any], label: str = "context") -> "StructuredPromptBuilder":
        """Add structured context data (validated JSON)."""
        # Validate it's valid JSON-serializable
        json.dumps(data, ensure_ascii=False)
        self.segments.append(PromptSegment(
            zone=PromptZone.CONTEXT,
            content=json.dumps({label: data}, ensure_ascii=False),
            metadata={"label": label}
        ))
        return self

    def add_user_input(self, text: str) -> "StructuredPromptBuilder":
        """Add raw user input - NEVER interpreted as instructions."""
        self.segments.append(PromptSegment(
            zone=PromptZone.USER,
            content=text,
        ))
        return self

    def add_tool_output(self, tool_name: str, result: Any) -> "StructuredPromptBuilder":
        """Add tool execution result."""
        self.segments.append(PromptSegment(
            zone=PromptZone.TOOL_OUTPUT,
            content=json.dumps({"tool": tool_name, "result": result}, ensure_ascii=False),
            metadata={"tool": tool_name}
        ))
        return self

    def build_messages(self) -> list[dict[str, str]]:
        """Build OpenAI-format messages with zone separation."""
        messages = [{"role": "system", "content": self.system_prompt}]

        for seg in self.segments:
            if seg.zone == PromptZone.CONTEXT:
                messages.append({
                    "role": "user",
                    "content": f"[CONTEXT] {seg.content}"
                })
            elif seg.zone == PromptZone.USER:
                messages.append({"role": "user", "content": seg.content})
            elif seg.zone == PromptZone.TOOL_OUTPUT:
                messages.append({
                    "role": "tool",
                    "content": seg.content,
                    "tool_call_id": seg.metadata.get("tool_call_id", "")
                })

        return messages


class InputValidator:
    """Validates and normalizes untrusted input before it enters prompt zones."""

    # Control characters that could corrupt parsing
    CONTROL_CHARS = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]')
    # Zero-width characters used for obfuscation
    ZERO_WIDTH = re.compile(r'[​-‍﻿]')

    @classmethod
    def validate_user_input(cls, text: str, max_len: int = 10000) -> str:
        """Normalize user input - structural only, no content filtering."""
        # 1. Strip control characters (structural corruption)
        text = cls.CONTROL_CHARS.sub('', text)
        text = cls.ZERO_WIDTH.sub('', text)

        # 2. Normalize unicode (NFC form)
        text = unicodedata.normalize('NFC', text)

        # 3. Length limit (DoS prevention)
        if len(text) > max_len:
            text = text[:max_len] + "\n[TRUNCATED: input too long]"

        return text

    @classmethod
    def validate_json(cls, data: Any, max_depth: int = 10, max_keys: int = 1000) -> Any:
        """Validate JSON structure for CONTEXT zone."""
        def _check(obj, depth=0):
            if depth > max_depth:
                raise ValueError("JSON depth exceeded")
            if isinstance(obj, dict):
                if len(obj) > max_keys:
                    raise ValueError("Too many keys")
                for k, v in obj.items():
                    if not isinstance(k, str):
                        raise ValueError("Non-string key")
                    _check(v, depth + 1)
            elif isinstance(obj, list):
                if len(obj) > max_keys:
                    raise ValueError("Array too large")
                for v in obj:
                    _check(v, depth + 1)
            elif not isinstance(obj, (str, int, float, bool, type(None))):
                raise ValueError(f"Invalid type: {type(obj)}")

        _check(data)
        return data


# Backwards compatibility
PromptSanitizer = InputValidator