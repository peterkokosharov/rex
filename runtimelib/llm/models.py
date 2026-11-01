# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Model registry and classification (from kiko.db)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ModelClass(Enum):
    """Model class from kiko.db classification."""
    TEXT = "text"           # General text models (Sonnet, Opus, Haiku, GPT-4)
    REASONING = "reasoning" # Reasoning models (o1, o3-mini, etc.)
    EMBEDDING = "embedding" # Embedding models
    EYE = "eye"             # Vision models (GPT-4V, Claude 3.5 Sonnet vision)
    VOICE = "voice"         # Audio/TTS/STT models (Whisper, TTS)
    IMAGE = "image"         # Image generation (DALL-E, Midjourney)
    VIDEO = "video"         # Video generation
    CODE = "code"           # Specialized code models
    MEMORY = "memory"       # Memory-augmented models
    TOOL = "tool"           # Tool-use specialized models
    AGENT = "agent"         # Agent/orchestration models
    OTHER = "other"


@dataclass
class ModelInfo:
    """Model information."""
    id: str
    name: str
    provider: str
    model_class: ModelClass = ModelClass.TEXT
    max_input_tokens: int = 4096
    max_output_tokens: int = 4096
    supports_streaming: bool = True
    supports_tools: bool = False
    supports_vision: bool = False
    supports_audio: bool = False
    input_price_per_1k: float = 0.0
    output_price_per_1k: float = 0.0
    aliases: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class ModelRegistry:
    """Registry of known models from LiteLLM proxy + kiko.db classification."""

    def __init__(self) -> None:
        self._models: dict[str, ModelInfo] = {}
        self._alias_map: dict[str, str] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register known models with kiko.db classification."""
        defaults = [
            # Anthropic
            ModelInfo(
                id="claude-opus-4-1",
                name="Claude Opus 4.1",
                provider="anthropic",
                model_class=ModelClass.AGENT,
                max_input_tokens=200000,
                max_output_tokens=8192,
                supports_tools=True,
                supports_vision=True,
                input_price_per_1k=15.0,
                output_price_per_1k=75.0,
                aliases=["opus", "opus-4"],
            ),
            ModelInfo(
                id="claude-sonnet-4-0",
                name="Claude Sonnet 4.0",
                provider="anthropic",
                model_class=ModelClass.AGENT,
                max_input_tokens=200000,
                max_output_tokens=8192,
                supports_tools=True,
                supports_vision=True,
                input_price_per_1k=3.0,
                output_price_per_1k=15.0,
                aliases=["sonnet", "sonnet-4"],
            ),
            ModelInfo(
                id="claude-3-5-sonnet-20241022",
                name="Claude 3.5 Sonnet",
                provider="anthropic",
                model_class=ModelClass.AGENT,
                max_input_tokens=200000,
                max_output_tokens=8192,
                supports_tools=True,
                supports_vision=True,
                input_price_per_1k=3.0,
                output_price_per_1k=15.0,
                aliases=["sonnet-3.5"],
            ),
            ModelInfo(
                id="claude-3-5-haiku-20241022",
                name="Claude 3.5 Haiku",
                provider="anthropic",
                model_class=ModelClass.AGENT,
                max_input_tokens=200000,
                max_output_tokens=8192,
                supports_tools=True,
                supports_vision=True,
                input_price_per_1k=0.8,
                output_price_per_1k=4.0,
                aliases=["haiku", "haiku-3.5"],
            ),
            ModelInfo(
                id="claude-3-opus-20240229",
                name="Claude 3 Opus",
                provider="anthropic",
                model_class=ModelClass.AGENT,
                max_input_tokens=200000,
                max_output_tokens=4096,
                supports_tools=True,
                supports_vision=True,
                input_price_per_1k=15.0,
                output_price_per_1k=75.0,
                aliases=["opus-3"],
            ),
            ModelInfo(
                id="claude-3-sonnet-20240229",
                name="Claude 3 Sonnet",
                provider="anthropic",
                model_class=ModelClass.AGENT,
                max_input_tokens=200000,
                max_output_tokens=4096,
                supports_tools=True,
                supports_vision=True,
                input_price_per_1k=3.0,
                output_price_per_1k=15.0,
                aliases=["sonnet-3"],
            ),
            ModelInfo(
                id="claude-3-haiku-20240307",
                name="Claude 3 Haiku",
                provider="anthropic",
                model_class=ModelClass.AGENT,
                max_input_tokens=200000,
                max_output_tokens=4096,
                supports_tools=True,
                supports_vision=True,
                input_price_per_1k=0.25,
                output_price_per_1k=1.25,
                aliases=["haiku-3"],
            ),

            # OpenAI
            ModelInfo(
                id="gpt-4o",
                name="GPT-4o",
                provider="openai",
                model_class=ModelClass.AGENT,
                max_input_tokens=128000,
                max_output_tokens=16384,
                supports_tools=True,
                supports_vision=True,
                input_price_per_1k=2.5,
                output_price_per_1k=10.0,
                aliases=["gpt4o"],
            ),
            ModelInfo(
                id="gpt-4o-mini",
                name="GPT-4o Mini",
                provider="openai",
                model_class=ModelClass.AGENT,
                max_input_tokens=128000,
                max_output_tokens=16384,
                supports_tools=True,
                supports_vision=True,
                input_price_per_1k=0.15,
                output_price_per_1k=0.6,
                aliases=["gpt4o-mini"],
            ),
            ModelInfo(
                id="o1-preview",
                name="o1 Preview",
                provider="openai",
                model_class=ModelClass.REASONING,
                max_input_tokens=128000,
                max_output_tokens=32768,
                supports_tools=False,
                input_price_per_1k=15.0,
                output_price_per_1k=60.0,
                aliases=["o1"],
            ),
            ModelInfo(
                id="o1-mini",
                name="o1 Mini",
                provider="openai",
                model_class=ModelClass.REASONING,
                max_input_tokens=128000,
                max_output_tokens=65536,
                supports_tools=False,
                input_price_per_1k=3.0,
                output_price_per_1k=12.0,
            ),
            ModelInfo(
                id="o3-mini",
                name="o3 Mini",
                provider="openai",
                model_class=ModelClass.REASONING,
                max_input_tokens=200000,
                max_output_tokens=100000,
                supports_tools=True,
                input_price_per_1k=1.1,
                output_price_per_1k=4.4,
            ),
            ModelInfo(
                id="gpt-4-turbo",
                name="GPT-4 Turbo",
                provider="openai",
                model_class=ModelClass.AGENT,
                max_input_tokens=128000,
                max_output_tokens=4096,
                supports_tools=True,
                supports_vision=True,
                input_price_per_1k=10.0,
                output_price_per_1k=30.0,
                aliases=["gpt4-turbo"],
            ),
            ModelInfo(
                id="gpt-3.5-turbo",
                name="GPT-3.5 Turbo",
                provider="openai",
                model_class=ModelClass.TEXT,
                max_input_tokens=16385,
                max_output_tokens=4096,
                supports_tools=True,
                input_price_per_1k=0.5,
                output_price_per_1k=1.5,
                aliases=["gpt35", "gpt-3.5"],
            ),
            ModelInfo(
                id="text-embedding-3-small",
                name="Text Embedding 3 Small",
                provider="openai",
                model_class=ModelClass.EMBEDDING,
                max_input_tokens=8191,
                max_output_tokens=0,
                input_price_per_1k=0.02,
                aliases=["embedding-3-small"],
            ),
            ModelInfo(
                id="text-embedding-3-large",
                name="Text Embedding 3 Large",
                provider="openai",
                model_class=ModelClass.EMBEDDING,
                max_input_tokens=8191,
                max_output_tokens=0,
                input_price_per_1k=0.13,
                aliases=["embedding-3-large"],
            ),
            ModelInfo(
                id="dall-e-3",
                name="DALL-E 3",
                provider="openai",
                model_class=ModelClass.IMAGE,
                max_input_tokens=4000,
                max_output_tokens=0,
                input_price_per_1k=0.04,  # per image
                aliases=["dalle3"],
            ),
            ModelInfo(
                id="whisper-1",
                name="Whisper",
                provider="openai",
                model_class=ModelClass.VOICE,
                max_input_tokens=0,
                max_output_tokens=0,
                supports_audio=True,
                input_price_per_1k=0.006,  # per minute
                aliases=["whisper"],
            ),
            ModelInfo(
                id="tts-1",
                name="TTS-1",
                provider="openai",
                model_class=ModelClass.VOICE,
                max_input_tokens=4096,
                max_output_tokens=0,
                supports_audio=True,
                input_price_per_1k=0.015,  # per 1k chars
                aliases=["tts"],
            ),

            # Google
            ModelInfo(
                id="gemini-1.5-pro",
                name="Gemini 1.5 Pro",
                provider="google",
                model_class=ModelClass.AGENT,
                max_input_tokens=2000000,
                max_output_tokens=8192,
                supports_tools=True,
                supports_vision=True,
                input_price_per_1k=1.25,
                output_price_per_1k=5.0,
                aliases=["gemini-pro"],
            ),
            ModelInfo(
                id="gemini-1.5-flash",
                name="Gemini 1.5 Flash",
                provider="google",
                model_class=ModelClass.AGENT,
                max_input_tokens=1000000,
                max_output_tokens=8192,
                supports_tools=True,
                supports_vision=True,
                input_price_per_1k=0.075,
                output_price_per_1k=0.3,
                aliases=["gemini-flash"],
            ),

            # Others via LiteLLM
            ModelInfo(
                id="deepseek-chat",
                name="DeepSeek Chat",
                provider="deepseek",
                model_class=ModelClass.AGENT,
                max_input_tokens=128000,
                max_output_tokens=4096,
                supports_tools=True,
                input_price_per_1k=0.14,
                output_price_per_1k=0.28,
                aliases=["deepseek"],
            ),
            ModelInfo(
                id="deepseek-reasoner",
                name="DeepSeek Reasoner",
                provider="deepseek",
                model_class=ModelClass.REASONING,
                max_input_tokens=128000,
                max_output_tokens=8192,
                input_price_per_1k=0.14,
                output_price_per_1k=2.19,
            ),
        ]

        for model in defaults:
            self.register(model)

    def register(self, model: ModelInfo) -> None:
        """Register a model."""
        self._models[model.id] = model
        self._alias_map[model.id] = model.id
        for alias in model.aliases:
            self._alias_map[alias.lower()] = model.id

    def get(self, model_id: str) -> ModelInfo | None:
        """Get model by ID or alias."""
        resolved = self._alias_map.get(model_id.lower(), model_id)
        return self._models.get(resolved)

    def get_by_class(self, model_class: ModelClass) -> list[ModelInfo]:
        """Get all models of a specific class."""
        return [m for m in self._models.values() if m.model_class == model_class]

    def list_all(self) -> list[ModelInfo]:
        """List all registered models."""
        return list(self._models.values())

    def resolve(self, model_id: str) -> str:
        """Resolve alias to model ID."""
        return self._alias_map.get(model_id.lower(), model_id)

    def update_from_litellm(self, models_data: list[dict[str, Any]]) -> None:
        """Update registry from LiteLLM /v1/models response."""
        for m in models_data:
            model_id = m.get("id", "")
            if not model_id or model_id in self._models:
                continue

            # Try to infer class from ID
            model_class = self._infer_class(model_id)
            self.register(ModelInfo(
                id=model_id,
                name=m.get("id", model_id),
                provider=m.get("owned_by", "unknown"),
                model_class=model_class,
                max_input_tokens=m.get("max_input_tokens", 4096),
                max_output_tokens=m.get("max_output_tokens", 4096),
                supports_tools=m.get("supports_tools", False),
                supports_vision=m.get("supports_vision", False),
            ))

    def _infer_class(self, model_id: str) -> ModelClass:
        """Infer model class from ID."""
        model_id = model_id.lower()
        if any(x in model_id for x in ["embedding", "embed"]):
            return ModelClass.EMBEDDING
        if any(x in model_id for x in ["whisper", "tts", "speech", "audio"]):
            return ModelClass.VOICE
        if any(x in model_id for x in ["dall-e", "midjourney", "stable-diffusion", "image"]):
            return ModelClass.IMAGE
        if any(x in model_id for x in ["o1", "o3", "reasoning", "r1"]):
            return ModelClass.REASONING
        if any(x in model_id for x in ["opus", "sonnet", "haiku", "gpt-4", "gemini"]):
            return ModelClass.AGENT
        return ModelClass.TEXT


# Global registry
_registry: ModelRegistry | None = None


def get_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
    return _registry