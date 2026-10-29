"""
config.constants — Global constants and enumerations.

Defines all enums used across the application for type-safe
configuration and routing.
"""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class TaskType(StrEnum):
    """Types of tasks the agent can handle.

    Each task type maps to a specific handler, system prompt,
    thinking prompt, and set of preferred models.
    """

    CODE = "code"
    MATH = "math"
    ACTIVITY = "activity"
    SCHOOL_TASK = "school_task"
    GENERATE_IMG = "generate_img"
    REASON = "reason"
    MULTIMODAL = "multimodal"

    @classmethod
    def from_string(cls, value: str) -> TaskType:
        """Convert a string to TaskType (case-insensitive).

        Args:
            value: String representation of the task type.

        Returns:
            TaskType: The matching enum member.

        Raises:
            ValueError: If no matching task type is found.
        """
        normalized = value.lower().strip().replace(" ", "_").replace("-", "_")
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(
            f"Unknown task type: '{value}'. "
            f"Valid types: {[m.value for m in cls]}"
        )


@unique
class ThinkingFormat(StrEnum):
    """Thinking output formats supported by different models.

    - GEMINI_LEVEL: Gemini's level-based thinking system.
    - CLAUDE_ADAPTIVE: Claude Sonnet's adaptive thinking (can toggle on/off).
    - CLAUDE_BUDGET: Claude Opus's budget-based deep thinking.
    - OPENAI: Thinking is returned as a separate field in the response JSON.
    - DEEPSEEK: Thinking is embedded in content within <think>...</think> tags.
    """

    GEMINI_LEVEL = "gemini-level"
    CLAUDE_ADAPTIVE = "claude-adaptive"
    CLAUDE_BUDGET = "claude-budget"
    OPENAI = "openai"
    DEEPSEEK = "deepseek"


@unique
class ModelProvider(StrEnum):
    """Model provider identifiers."""

    AG = "ag"  # Agent router models (primary)
    TH = "TH"  # TH provider models (available as fallback)
    KG = "kg"  # Kagiro provider models (external marketplace)
    GEMINI = "gemini"  # Direct Google Gemini models from 9Router
    BANSOS = "bansos"  # Bansos mode (free Kagiro direct API)
    VK = "vk"  # Vikey router models (vk/... multi-vendor)


@unique
class ToolName(StrEnum):
    """Registered tool names for function calling."""

    EXECUTE_COMMAND = "execute_command"
    EDIT_FILE = "edit_file"
    DISCARD_CHANGE = "discard_change"
    UPLOAD_FILE = "upload_file"
    CREATE_PLAN = "create_plan"
    EXECUTE_PLAN = "execute_plan"
    SEARCH_FILES = "search_files"
    READ_FILE = "read_file"


@unique
class EventType(StrEnum):
    """Event types for the internal event bus."""

    # Lifecycle events
    SESSION_STARTED = "session_started"
    SESSION_ENDED = "session_ended"

    # Classification events
    INPUT_RECEIVED = "input_received"
    INTENT_CLASSIFIED = "intent_classified"

    # Model events
    MODEL_SELECTED = "model_selected"
    MODEL_FALLBACK = "model_fallback"
    REQUEST_SENT = "request_sent"
    RESPONSE_RECEIVED = "response_received"
    STREAM_CHUNK_RECEIVED = "stream_chunk_received"
    STREAM_COMPLETED = "stream_completed"

    # Tool events
    TOOL_CALL_REQUESTED = "tool_call_requested"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    TOOL_CALL_FAILED = "tool_call_failed"

    # Error events
    ERROR_OCCURRED = "error_occurred"
    RETRY_ATTEMPTED = "retry_attempted"

    # Token events
    TOKENS_COUNTED = "tokens_counted"
    CONTEXT_COMPRESSED = "context_compressed"


@unique
class ResponseStatus(StrEnum):
    """Status of an agent response."""

    SUCCESS = "success"
    PARTIAL = "partial"  # Response incomplete (min tokens not met)
    ERROR = "error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    FALLBACK_USED = "fallback_used"


# ============================================
# Static Constants
# ============================================

# Default confidence threshold for intent classification
CLASSIFICATION_CONFIDENCE_THRESHOLD: float = 0.6

# Maximum iterations for tool call loop (safety guard)
MAX_TOOL_CALL_ITERATIONS: int = 40
# Reasoning models may emit thinking-only turns before their first tool call.
# Those must not eat the tool budget, or a model that "thinks twice" silently
# loses 2 of its 10 tool rounds and gives up early.
MAX_THINKING_ONLY_ITERATIONS: int = 3

# Buffer tokens to reserve when calculating max output
TOKEN_BUFFER: int = 512

# Supported file extensions for syntax validation after edit
SYNTAX_CHECKABLE_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml",
})

# Models that are free tier (no cost)
FREE_MODEL_SUFFIX: str = ":free"
