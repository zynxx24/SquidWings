"""
tool.execute.execute_command_prompt.command_sanitizer — Command safety checks.

Validates and sanitizes shell commands before execution.
Uses a whitelist approach with pattern-based blocking.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SanitizeResult:
    """Result of command sanitization.

    Attributes:
        is_safe: Whether the command is safe to execute.
        sanitized_command: The (possibly modified) command.
        warnings: List of warning messages.
        blocked_reason: Reason the command was blocked (if unsafe).
    """
    is_safe: bool = True
    sanitized_command: str = ""
    warnings: list[str] = field(default_factory=list)
    blocked_reason: str = ""


# Patterns that are always blocked
_BLOCKED_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?/\s*$", "rm of root directory"),
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/", "recursive rm of root"),
    (r"\bsudo\b", "sudo commands not allowed"),
    (r"\bchmod\s+777\b", "chmod 777 not allowed"),
    (r"\bchown\b", "chown not allowed"),
    (r"\bmkfs\b", "mkfs not allowed"),
    (r"\bdd\s+", "dd command not allowed"),
    (r"\b:(){ :\|:& };:", "fork bomb detected"),
    (r"\b>\s*/dev/sd[a-z]", "writing to block device"),
    (r"\bcurl\b.*\|\s*\b(bash|sh)\b", "piping curl to shell"),
    (r"\bwget\b.*\|\s*\b(bash|sh)\b", "piping wget to shell"),
    (r"\bnc\s+-[a-zA-Z]*l", "netcat listener not allowed"),
    (r"\bshutdown\b", "shutdown not allowed"),
    (r"\breboot\b", "reboot not allowed"),
]


class CommandSanitizer:
    """Validates and sanitizes shell commands.

    Uses a combination of:
    - Whitelist of allowed base commands
    - Blacklist of dangerous patterns
    - Structural validation (no pipe to dangerous commands)
    """

    def __init__(
        self,
        allowed_commands_path: str | Path | None = None,
        custom_blocked: list[str] | None = None,
    ) -> None:
        """Initialize the sanitizer.

        Args:
            allowed_commands_path: Path to allowed_commands.yaml.
            custom_blocked: Additional blocked command patterns.
        """
        self._allowed: set[str] = set()
        self._blocked: set[str] = set()
        self._custom_blocked_patterns: list[tuple[str, str]] = []

        if allowed_commands_path:
            self._load_config(Path(allowed_commands_path))
        else:
            self._use_defaults()

        if custom_blocked:
            for pattern in custom_blocked:
                self._custom_blocked_patterns.append(
                    (pattern, f"Custom blocked: {pattern}")
                )

    def _use_defaults(self) -> None:
        """Set default allowed/blocked commands."""
        self._allowed = {
            "python", "python3", "pip", "pip3",
            "node", "npm", "npx",
            "git", "ls", "cat", "echo", "mkdir", "cp", "mv",
            "head", "tail", "grep", "find", "wc", "sort", "uniq",
            "pwd", "which", "env", "printenv",
            "touch", "date", "whoami", "uname",
            "tar", "zip", "unzip", "gzip",
            "curl", "wget",  # Allowed standalone, blocked when piped to shell
            "tree", "diff", "patch",
        }
        self._blocked = {
            "rm", "sudo", "su", "chown", "chmod",
            "mkfs", "dd", "shutdown", "reboot", "kill", "killall",
            "nc", "ncat",
        }

    def _load_config(self, path: Path) -> None:
        """Load config from YAML file."""
        if not path.exists():
            logger.warning(f"[sanitizer] Config not found: {path}")
            self._use_defaults()
            return

        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        self._allowed = set(config.get("allowed", []))
        self._blocked = set(config.get("blocked", []))
        logger.info(
            f"[sanitizer] Loaded: {len(self._allowed)} allowed, "
            f"{len(self._blocked)} blocked"
        )

    def sanitize(self, command: str) -> SanitizeResult:
        """Sanitize a shell command.

        Args:
            command: The command string to check.

        Returns:
            SanitizeResult with safety assessment.
        """
        result = SanitizeResult(sanitized_command=command.strip())

        if not command.strip():
            result.is_safe = False
            result.blocked_reason = "Empty command"
            return result

        # Check blocked patterns
        for pattern, reason in _BLOCKED_PATTERNS + self._custom_blocked_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                result.is_safe = False
                result.blocked_reason = reason
                logger.warning(f"[sanitizer] BLOCKED: {reason} — {command[:80]}")
                return result

        # Extract base command
        base_cmd = self._extract_base_command(command)

        # Check if base command is explicitly blocked
        if base_cmd in self._blocked:
            result.is_safe = False
            result.blocked_reason = f"Command '{base_cmd}' is blocked"
            logger.warning(f"[sanitizer] BLOCKED: {base_cmd}")
            return result

        # Check if base command is in the allowed list
        if self._allowed and base_cmd not in self._allowed:
            result.warnings.append(
                f"Command '{base_cmd}' not in allowed list — proceeding with caution"
            )

        # Warn about pipes to potentially dangerous commands
        if "|" in command:
            pipe_targets = self._get_pipe_targets(command)
            for target in pipe_targets:
                if target in self._blocked:
                    result.is_safe = False
                    result.blocked_reason = f"Pipe to blocked command: {target}"
                    return result
                if target not in self._allowed:
                    result.warnings.append(f"Pipe to unlisted command: {target}")

        if result.warnings:
            logger.debug(f"[sanitizer] Warnings: {result.warnings}")

        return result

    def _extract_base_command(self, command: str) -> str:
        """Extract the base command name.

        Args:
            command: Full command string.

        Returns:
            Base command name (e.g., 'python' from 'python script.py').
        """
        try:
            parts = shlex.split(command)
            if parts:
                # Handle env vars prefix like VAR=val cmd
                for part in parts:
                    if "=" not in part:
                        return Path(part).name
            return command.split()[0]
        except ValueError:
            return command.split()[0] if command.split() else ""

    def _get_pipe_targets(self, command: str) -> list[str]:
        """Get commands on the receiving end of pipes.

        Args:
            command: Full command string with pipes.

        Returns:
            list: Base command names after pipe symbols.
        """
        targets: list[str] = []
        parts = command.split("|")
        for part in parts[1:]:  # Skip the first command
            stripped = part.strip()
            if stripped:
                targets.append(self._extract_base_command(stripped))
        return targets
