"""
config — Global configuration module.

Provides centralized access to all application settings, secrets,
and constants. All configuration is loaded from environment variables
and YAML files.

Usage:
    from brain.config import get_settings, get_secrets, TaskType, ThinkingFormat
"""

from brain.config.constants import (
    EventType,
    ResponseStatus,
    TaskType,
    ThinkingFormat,
    ToolName,
)
from brain.config.secrets import Secrets, get_secrets
from brain.config.settings import Settings, get_settings

__all__ = [
    "Settings",
    "get_settings",
    "Secrets",
    "get_secrets",
    "TaskType",
    "ThinkingFormat",
    "ToolName",
    "EventType",
    "ResponseStatus",
]
