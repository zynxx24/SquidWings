"""
utils.validators — Input and output validation functions.

Provides validation for model IDs, API keys, message formats,
tool call arguments, and other data flowing through the system.
"""

from __future__ import annotations

import re
from typing import Any


def validate_model_id(model_id: str) -> tuple[bool, str]:
    """Validate a model ID format.

    Expected format: 'PROVIDER/model-name' or 'PROVIDER/model-name:variant'

    Args:
        model_id: The model ID to validate.

    Returns:
        Tuple of (is_valid, error_message). error_message is empty if valid.
    """
    if not model_id:
        return False, "Model ID cannot be empty"

    if "/" not in model_id:
        return False, f"Model ID must contain a provider prefix (e.g., 'TH/model-name'), got: '{model_id}'"

    provider, name = model_id.split("/", 1)
    if not provider:
        return False, "Provider prefix cannot be empty"
    if not name:
        return False, "Model name cannot be empty"

    return True, ""


def validate_api_key(api_key: str) -> tuple[bool, str]:
    """Validate an API key format.

    Args:
        api_key: The API key to validate.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not api_key:
        return False, "API key cannot be empty"

    if api_key == "your-api-key-here":
        return False, "API key is still the placeholder value"

    if len(api_key) < 10:
        return False, "API key seems too short (min 10 characters)"

    return True, ""


def validate_message_format(messages: list[dict[str, Any]]) -> tuple[bool, str]:
    """Validate the format of a messages list for the chat API.

    Each message must have 'role' and 'content' fields.
    Valid roles: 'system', 'user', 'assistant', 'tool'.

    Args:
        messages: List of message dicts to validate.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not messages:
        return False, "Messages list cannot be empty"

    if not isinstance(messages, list):
        return False, f"Messages must be a list, got {type(messages).__name__}"

    valid_roles = {"system", "user", "assistant", "tool"}

    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            return False, f"Message at index {i} must be a dict, got {type(msg).__name__}"

        if "role" not in msg:
            return False, f"Message at index {i} missing 'role' field"

        if msg["role"] not in valid_roles:
            return False, (
                f"Message at index {i} has invalid role '{msg['role']}'. "
                f"Valid roles: {valid_roles}"
            )

        if "content" not in msg and "tool_calls" not in msg:
            return False, (
                f"Message at index {i} must have 'content' or 'tool_calls' field"
            )

    return True, ""


def validate_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    registered_tools: set[str] | None = None,
) -> tuple[bool, str]:
    """Validate a tool call before execution.

    Args:
        tool_name: Name of the tool being called.
        arguments: Dict of arguments passed to the tool.
        registered_tools: Optional set of known tool names.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not tool_name:
        return False, "Tool name cannot be empty"

    if not isinstance(arguments, dict):
        return False, f"Tool arguments must be a dict, got {type(arguments).__name__}"

    if registered_tools and tool_name not in registered_tools:
        return False, (
            f"Unknown tool '{tool_name}'. "
            f"Registered tools: {sorted(registered_tools)}"
        )

    return True, ""


def validate_temperature(temperature: float) -> tuple[bool, str]:
    """Validate temperature parameter.

    Args:
        temperature: Temperature value (0.0 to 2.0).

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not isinstance(temperature, (int, float)):
        return False, f"Temperature must be a number, got {type(temperature).__name__}"

    if temperature < 0.0 or temperature > 2.0:
        return False, f"Temperature must be between 0.0 and 2.0, got {temperature}"

    return True, ""


def validate_max_tokens(max_tokens: int, model_max: int | None = None) -> tuple[bool, str]:
    """Validate max_tokens parameter.

    Args:
        max_tokens: Maximum tokens to generate.
        model_max: Optional model-specific maximum.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not isinstance(max_tokens, int):
        return False, f"max_tokens must be an integer, got {type(max_tokens).__name__}"

    if max_tokens < 1:
        return False, f"max_tokens must be positive, got {max_tokens}"

    if model_max and max_tokens > model_max:
        return False, (
            f"max_tokens ({max_tokens}) exceeds model maximum ({model_max})"
        )

    return True, ""


def sanitize_user_input(text: str) -> str:
    """Sanitize user input by removing control characters.

    Preserves newlines and tabs but removes other control characters.

    Args:
        text: Raw user input.

    Returns:
        Sanitized string.
    """
    # Remove control chars except newline (\n), carriage return (\r), tab (\t)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text).strip()
