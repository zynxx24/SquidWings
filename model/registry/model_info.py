"""
model.registry.model_info — Model data class.

Defines the ModelInfo dataclass that holds all metadata about a model,
including its capabilities, limits, and provider information.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config.constants import FREE_MODEL_SUFFIX, ThinkingFormat


@dataclass
class ModelCapabilities:
    """Capabilities of a model.

    Each field indicates whether the model supports
    a specific feature.
    """

    vision: bool = False
    pdf: bool = False
    audio_input: bool = False
    video_input: bool = False
    image_output: bool = False
    audio_output: bool = False
    search: bool = False
    tools: bool = False
    reasoning: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelCapabilities:
        """Create from API response dict.

        Args:
            data: Capabilities dict from /v1/models response.

        Returns:
            ModelCapabilities instance.
        """
        return cls(
            vision=data.get("vision", False),
            pdf=data.get("pdf", False),
            audio_input=data.get("audioInput", data.get("audio_input", False)),
            video_input=data.get("videoInput", data.get("video_input", False)),
            image_output=data.get("imageOutput", data.get("image_output", False)),
            audio_output=data.get("audioOutput", data.get("audio_output", False)),
            search=data.get("search", False),
            tools=data.get("tools", False),
            reasoning=data.get("reasoning", False),
        )


@dataclass
class ModelInfo:
    """Complete information about a model.

    Attributes:
        id: Unique model identifier (e.g., "ag/gemini-3.8-flash-high").
        display_name: Human-readable model name.
        provider: Provider prefix (e.g., "ag", "TH").
        capabilities: What the model can do.
        thinking_format: How the model outputs thinking.
        thinking_can_disable: Whether thinking can be turned off.
        thinking_effort_supported: Whether effort levels are supported.
        context_window: Maximum input tokens.
        max_output: Maximum output tokens.
        tags: Descriptive tags for routing.
    """

    id: str
    display_name: str = ""
    provider: str = ""
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    thinking_format: str = ""
    thinking_can_disable: bool = False
    thinking_effort_supported: bool = False
    context_window: int = 0
    max_output: int = 0
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Auto-derive provider and display_name if not set."""
        if not self.provider and "/" in self.id:
            self.provider = self.id.split("/")[0]
        if not self.display_name:
            self.display_name = self.id

    # --- Convenience Properties ---

    @property
    def is_free(self) -> bool:
        """Check if this is a free-tier model."""
        return self.id.endswith(FREE_MODEL_SUFFIX)

    @property
    def supports_vision(self) -> bool:
        """Check if the model supports image input."""
        return self.capabilities.vision

    @property
    def supports_audio(self) -> bool:
        """Check if the model supports audio input."""
        return self.capabilities.audio_input

    @property
    def supports_video(self) -> bool:
        """Check if the model supports video input."""
        return self.capabilities.video_input

    @property
    def supports_multimodal(self) -> bool:
        """Check if the model supports any multimodal input."""
        return (
            self.capabilities.vision
            or self.capabilities.audio_input
            or self.capabilities.video_input
        )

    @property
    def supports_tools(self) -> bool:
        """Check if the model supports function calling."""
        return self.capabilities.tools

    @property
    def supports_reasoning(self) -> bool:
        """Check if the model supports reasoning/thinking."""
        return self.capabilities.reasoning

    @property
    def supports_search(self) -> bool:
        """Check if the model supports web search."""
        return self.capabilities.search

    @property
    def thinking_format_enum(self) -> ThinkingFormat | None:
        """Get the thinking format as an enum, if recognized."""
        try:
            return ThinkingFormat(self.thinking_format)
        except ValueError:
            return None

    # --- Factory Methods ---

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> ModelInfo:
        """Create ModelInfo from an API /v1/models response item.

        Args:
            data: A single model dict from the API response.

        Returns:
            ModelInfo instance with all fields populated.
        """
        caps_data = data.get("capabilities", {})

        return cls(
            id=data["id"],
            provider=data.get("owned_by", ""),
            capabilities=ModelCapabilities.from_dict(caps_data),
            thinking_format=caps_data.get("thinkingFormat", ""),
            thinking_can_disable=caps_data.get("thinkingCanDisable", False),
            thinking_effort_supported=caps_data.get("thinkingEffortSupported", False),
            context_window=data.get("context_length", caps_data.get("contextWindow", 0)),
            max_output=data.get("max_completion_tokens", caps_data.get("maxOutput", 0)),
        )

    @classmethod
    def from_config(cls, model_id: str, config: dict[str, Any]) -> ModelInfo:
        """Create ModelInfo from YAML config.

        Args:
            model_id: The model ID key from config.
            config: The model's config dict.

        Returns:
            ModelInfo instance.
        """
        caps_data = config.get("capabilities", {})

        return cls(
            id=model_id,
            display_name=config.get("display_name", model_id),
            provider=config.get("provider", ""),
            capabilities=ModelCapabilities.from_dict(caps_data),
            thinking_format=config.get("thinking_format", ""),
            thinking_can_disable=config.get("thinking_can_disable", False),
            thinking_effort_supported=config.get("thinking_effort_supported", False),
            context_window=config.get("context_window", 0),
            max_output=config.get("max_output", 0),
            tags=config.get("tags", []),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for logging/display."""
        return {
            "id": self.id,
            "display_name": self.display_name,
            "provider": self.provider,
            "thinking_format": self.thinking_format,
            "context_window": self.context_window,
            "max_output": self.max_output,
            "vision": self.supports_vision,
            "audio": self.supports_audio,
            "video": self.supports_video,
            "tools": self.supports_tools,
            "search": self.supports_search,
            "reasoning": self.supports_reasoning,
            "is_free": self.is_free,
            "tags": self.tags,
        }

    def __repr__(self) -> str:
        return (
            f"ModelInfo(id='{self.id}', ctx={self.context_window}, "
            f"max_out={self.max_output}, think='{self.thinking_format}')"
        )
