"""
utils.logger — Structured logging with rich output.

Provides per-module loggers with colored console output and
optional file rotation. All logs include structured fields
for easy debugging and monitoring.

Usage:
    from utils.logger import get_logger
    logger = get_logger(__name__)
    logger.info("Model selected", extra={"model_id": "TH/gpt-6.1-sol"})
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

# Custom theme for agent logs
_AGENT_THEME = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "critical": "bold white on red",
    "model": "bold magenta",
    "tool": "bold green",
    "brain": "bold blue",
    "router": "bold yellow",
    "token": "dim cyan",
})

_console = Console(theme=_AGENT_THEME, stderr=True)

# Track whether root logging has been configured
_logging_configured: bool = False


def _setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Configure the root logger with Rich handler and optional file handler.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file: Optional path to write logs to a file.
    """
    global _logging_configured
    if _logging_configured:
        return

    log_level = getattr(logging, level.upper(), logging.INFO)

    # Rich console handler — beautiful terminal output
    rich_handler = RichHandler(
        console=_console,
        show_time=True,
        show_path=True,
        show_level=True,
        rich_tracebacks=True,
        tracebacks_show_locals=True,
        markup=True,
    )
    rich_handler.setLevel(log_level)

    handlers: list[logging.Handler] = [rich_handler]

    # Optional file handler for persistent logs
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)  # File always captures everything
        file_formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        handlers.append(file_handler)

    # Configure root logger
    logging.basicConfig(
        level=log_level,
        handlers=handlers,
        force=True,
    )

    # Silence noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _logging_configured = True


def get_logger(name: str, level: str | None = None) -> logging.Logger:
    """Get a named logger instance.

    On first call, configures the root logging system. Subsequent calls
    return loggers that inherit the root configuration.

    Args:
        name: Logger name (typically __name__ of the calling module).
        level: Optional override for log level.

    Returns:
        logging.Logger: Configured logger instance.
    """
    # Lazy initialization — use env level or default
    if not _logging_configured:
        import os
        env_level: str = level or os.getenv("LOG_LEVEL") or "INFO"
        _setup_logging(level=env_level)

    logger = logging.getLogger(name)
    if level:
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    return logger


class LogContext:
    """Context manager for adding structured context to log messages.

    Usage:
        with LogContext(logger, model_id="TH/gpt-6.1-sol", task="code"):
            logger.info("Processing request")
            # Logs will include model_id and task in extra
    """

    def __init__(self, logger: logging.Logger, **context: Any) -> None:
        self.logger = logger
        self.context = context
        self._original_extra: dict[str, Any] = {}

    def __enter__(self) -> logging.Logger:
        # Wrap the logger's methods to inject context
        return self.logger

    def __exit__(self, *args: Any) -> None:
        pass

    def info(self, msg: str, **kwargs: Any) -> None:
        """Log info with context."""
        extra = {**self.context, **kwargs.get("extra", {})}
        self.logger.info(f"{msg} | {extra}")

    def error(self, msg: str, **kwargs: Any) -> None:
        """Log error with context."""
        extra = {**self.context, **kwargs.get("extra", {})}
        self.logger.error(f"{msg} | {extra}")

    def debug(self, msg: str, **kwargs: Any) -> None:
        """Log debug with context."""
        extra = {**self.context, **kwargs.get("extra", {})}
        self.logger.debug(f"{msg} | {extra}")


def log_separator(logger: logging.Logger, title: str = "", char: str = "─") -> None:
    """Log a visual separator line for readability.

    Args:
        logger: Logger to use.
        title: Optional title to center in the separator.
        char: Character to use for the separator line.
    """
    if title:
        logger.info(f" {char * 3} {title} {char * 3} ")
    else:
        logger.info(char * 40)
