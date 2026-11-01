# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Configuration schema validation (stdlib-only, pydantic-style)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Field:
    """Schema field definition."""
    type: type | tuple[type, ...] = str
    required: bool = False
    default: Any = None
    validator: Optional[Callable[[Any], bool]] = None
    description: str = ""
    choices: list[Any] | None = None

    def validate(self, value: Any, path: str) -> Any:
        """Validate and coerce value."""
        if value is None:
            if self.required:
                raise ValueError(f"Required field missing: {path}")
            return self.default

        # Type check/coerce
        if not isinstance(value, self.type):
            try:
                if self.type == bool and isinstance(value, str):
                    value = value.lower() in ("true", "1", "yes", "on")
                elif self.type == int:
                    value = int(value)
                elif self.type == float:
                    value = float(value)
                elif self.type == list and isinstance(value, str):
                    import json
                    value = json.loads(value)
                elif self.type == dict and isinstance(value, str):
                    import json
                    value = json.loads(value)
                else:
                    value = self.type(value)
            except (ValueError, TypeError) as e:
                raise ValueError(f"Field {path}: cannot coerce {value!r} to {self.type}: {e}")

        # Choices
        if self.choices and value not in self.choices:
            raise ValueError(f"Field {path}: value {value!r} not in allowed choices: {self.choices}")

        # Custom validator
        if self.validator and not self.validator(value):
            raise ValueError(f"Field {path}: validation failed for value {value!r}")

        return value


@dataclass
class ConfigSchema:
    """Configuration schema."""
    fields: dict[str, Field] = field(default_factory=dict)
    nested: dict[str, "ConfigSchema"] = field(default_factory=dict)

    def add_field(self, name: str, field: Field) -> "ConfigSchema":
        self.fields[name] = field
        return self

    def add_nested(self, name: str, schema: "ConfigSchema") -> "ConfigSchema":
        self.nested[name] = schema
        return self

    def validate(self, data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        """Validate config data against schema."""
        result = {}

        # Validate fields
        for name, field in self.fields.items():
            path = f"{prefix}.{name}" if prefix else name
            value = data.get(name, None)
            result[name] = field.validate(value, path)

        # Validate nested schemas
        for name, schema in self.nested.items():
            path = f"{prefix}.{name}" if prefix else name
            nested_data = data.get(name, {})
            if not isinstance(nested_data, dict):
                if any(f.required for f in schema.fields.values()) or schema.nested:
                    raise ValueError(f"Expected dict for {path}, got {type(nested_data).__name__}")
                nested_data = {}
            result[name] = schema.validate(nested_data, path)

        return result


# Default runtime schema
def create_runtime_schema() -> ConfigSchema:
    """Create the default runtime configuration schema."""
    schema = ConfigSchema()

    # liteLLM section
    lite_llm = ConfigSchema()
    lite_llm.add_field("base_url", Field(type=str, required=True, description="LiteLLM proxy base URL"))
    lite_llm.add_field("api_key", Field(type=str, required=False, description="API key for proxy"))
    lite_llm.add_field("timeout", Field(type=int, default=60, description="Request timeout seconds"))
    lite_llm.add_field("max_retries", Field(type=int, default=3, description="Max retry attempts"))
    schema.add_nested("liteLLM", lite_llm)

    # docker_guard section
    docker_guard = ConfigSchema()
    docker_guard.add_field("image", Field(type=str, default="runtime-guard:latest", description="Guard container image"))
    docker_guard.add_field("memory_limit", Field(type=str, default="512m", description="Memory limit"))
    docker_guard.add_field("cpu_limit", Field(type=float, default=1.0, description="CPU limit"))
    docker_guard.add_field("network_mode", Field(type=str, default="none", choices=["none", "bridge", "host"], description="Network mode"))
    docker_guard.add_field("timeout", Field(type=int, default=30, description="Command timeout seconds"))
    schema.add_nested("docker_guard", docker_guard)

    # agent section
    agent = ConfigSchema()
    agent.add_field("max_steps", Field(type=int, default=20, description="Max agent steps"))
    agent.add_field("model", Field(type=str, default="sonnet", description="Default model"))
    agent.add_field("temperature", Field(type=float, default=0.1, description="Temperature"))
    agent.add_field("system_prompt", Field(type=str, default="", description="Custom system prompt"))
    schema.add_nested("agent", agent)

    # session section
    session = ConfigSchema()
    session.add_field("history_file", Field(type=str, default="~/.config/runtime/history.jsonl", description="History file"))
    session.add_field("checkpoint_interval", Field(type=int, default=10, description="Checkpoint every N steps"))
    session.add_field("max_history", Field(type=int, default=1000, description="Max history entries"))
    schema.add_nested("session", session)

    # ui section
    ui = ConfigSchema()
    ui.add_field("language", Field(type=str, default="bg", choices=["bg", "en"], description="UI language"))
    ui.add_field("theme", Field(type=str, default="dark", choices=["dark", "light"], description="Theme"))
    ui.add_field("show_tokens", Field(type=bool, default=True, description="Show token usage"))
    ui.add_field("show_cost", Field(type=bool, default=True, description="Show cost"))
    schema.add_nested("ui", ui)

    return schema


DEFAULT_SCHEMA = create_runtime_schema()


def validate_config(data: dict[str, Any], schema: Optional[ConfigSchema] = None) -> dict[str, Any]:
    """Validate config against schema."""
    schema = schema or DEFAULT_SCHEMA
    return schema.validate(data)