"""
tool.execute.execute_command_prompt.command_output_parser — Parse command output.

Analyzes command stdout/stderr to detect errors, warnings,
and success patterns. Returns structured output for the agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class StructuredOutput:
    """Parsed and structured command output.

    Attributes:
        status: Overall status (success, error, warning).
        summary: Short summary of the output.
        stdout: Full stdout text.
        stderr: Full stderr text.
        return_code: Exit code.
        errors: Detected error messages.
        warnings: Detected warning messages.
        output_lines: Number of output lines.
    """
    status: str = "success"
    summary: str = ""
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    output_lines: int = 0

    def to_tool_result(self) -> str:
        """Format as a string for tool result message."""
        parts = [f"Status: {self.status}"]
        if self.summary:
            parts.append(f"Summary: {self.summary}")
        if self.errors:
            parts.append(f"Errors: {'; '.join(self.errors[:5])}")
        if self.warnings:
            parts.append(f"Warnings: {'; '.join(self.warnings[:5])}")
        if self.stdout.strip():
            # Truncate long output
            out = self.stdout.strip()
            if len(out) > 3000:
                out = out[:3000] + "\n\n[... output truncated ...]"
            parts.append(f"Output:\n{out}")
        if self.stderr.strip() and not self.errors:
            parts.append(f"Stderr:\n{self.stderr.strip()[:1000]}")
        return "\n".join(parts)


# Common error patterns
_ERROR_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(Error|ERROR|error):?\s+", re.MULTILINE),
    re.compile(r"^Traceback \(most recent call last\)", re.MULTILINE),
    re.compile(r"^SyntaxError:", re.MULTILINE),
    re.compile(r"^(NameError|TypeError|ValueError|KeyError|IndexError|AttributeError):", re.MULTILINE),
    re.compile(r"^(ImportError|ModuleNotFoundError):", re.MULTILINE),
    re.compile(r"^(FileNotFoundError|PermissionError|OSError):", re.MULTILINE),
    re.compile(r"^FAILED", re.MULTILINE),
    re.compile(r"fatal:", re.MULTILINE | re.IGNORECASE),
    re.compile(r"npm ERR!", re.MULTILINE),
    re.compile(r"command not found", re.MULTILINE | re.IGNORECASE),
]

# Common warning patterns
_WARNING_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(Warning|WARNING|warning):?\s+", re.MULTILINE),
    re.compile(r"^DeprecationWarning:", re.MULTILINE),
    re.compile(r"^(FutureWarning|UserWarning|RuntimeWarning):", re.MULTILINE),
    re.compile(r"npm WARN", re.MULTILINE),
]


class CommandOutputParser:
    """Parses and analyzes command output.

    Detects error and warning patterns in stdout/stderr,
    determines overall status, and creates a structured summary.
    """

    def parse(
        self,
        stdout: str,
        stderr: str,
        return_code: int,
    ) -> StructuredOutput:
        """Parse command output into structured format.

        Args:
            stdout: Standard output text.
            stderr: Standard error text.
            return_code: Process exit code.

        Returns:
            StructuredOutput with analysis results.
        """
        errors = self._find_errors(stdout, stderr)
        warnings = self._find_warnings(stdout, stderr)

        # Determine status
        if return_code != 0 or errors:
            status = "error"
        elif warnings:
            status = "warning"
        else:
            status = "success"

        # Build summary
        summary = self._build_summary(stdout, stderr, return_code, errors)

        output_lines = (
            len(stdout.strip().splitlines()) + len(stderr.strip().splitlines())
        )

        result = StructuredOutput(
            status=status,
            summary=summary,
            stdout=stdout,
            stderr=stderr,
            return_code=return_code,
            errors=errors,
            warnings=warnings,
            output_lines=output_lines,
        )

        logger.debug(
            f"[output_parser] status={status}, "
            f"errors={len(errors)}, warnings={len(warnings)}, "
            f"lines={output_lines}"
        )

        return result

    def _find_errors(self, stdout: str, stderr: str) -> list[str]:
        """Find error patterns in output."""
        errors: list[str] = []
        combined = f"{stdout}\n{stderr}"

        for pattern in _ERROR_PATTERNS:
            for match in pattern.finditer(combined):
                # Get the matched line
                line_start = combined.rfind("\n", 0, match.start()) + 1
                line_end = combined.find("\n", match.end())
                if line_end == -1:
                    line_end = len(combined)
                error_line = combined[line_start:line_end].strip()
                if error_line and error_line not in errors:
                    errors.append(error_line[:200])

        return errors[:10]  # Max 10 errors

    def _find_warnings(self, stdout: str, stderr: str) -> list[str]:
        """Find warning patterns in output."""
        warnings: list[str] = []
        combined = f"{stdout}\n{stderr}"

        for pattern in _WARNING_PATTERNS:
            for match in pattern.finditer(combined):
                line_start = combined.rfind("\n", 0, match.start()) + 1
                line_end = combined.find("\n", match.end())
                if line_end == -1:
                    line_end = len(combined)
                warning_line = combined[line_start:line_end].strip()
                if warning_line and warning_line not in warnings:
                    warnings.append(warning_line[:200])

        return warnings[:10]

    def _build_summary(
        self,
        stdout: str,
        stderr: str,
        return_code: int,
        errors: list[str],
    ) -> str:
        """Build a short summary of the command result."""
        if return_code == 0 and not errors:
            lines = stdout.strip().splitlines()
            if lines:
                return f"Success ({len(lines)} lines of output)"
            return "Success (no output)"

        if errors:
            return f"Failed (rc={return_code}): {errors[0][:100]}"

        return f"Exited with code {return_code}"
