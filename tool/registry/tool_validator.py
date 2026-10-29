"""
tool.registry.tool_validator — Validate tool call arguments.

Validates that tool call arguments match the expected schema
before execution. Checks required params, types, and constraints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tool.registry.tool_schema import ParamType, ToolSchema
from utils.logger import get_logger

logger = get_logger(__name__)

# Python type mapping for JSON Schema types
_TYPE_MAP: dict[str, tuple[type, ...]] = {
    ParamType.STRING.value: (str,),
    ParamType.INTEGER.value: (int,),
    ParamType.NUMBER.value: (int, float),
    ParamType.BOOLEAN.value: (bool,),
    ParamType.ARRAY.value: (list,),
    ParamType.OBJECT.value: (dict,),
}


@dataclass
class ValidationResult:
    """Result of tool argument validation.

    Attributes:
        is_valid: Whether all checks passed.
        errors: List of error messages.
        warnings: List of warning messages.
        cleaned_args: Arguments after applying defaults.
    """
    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cleaned_args: dict[str, Any] = field(default_factory=dict)


class ToolValidator:
    """Validates tool call arguments against their schemas.

    Checks:
    - All required parameters are present
    - Parameter types match the schema
    - Enum constraints are satisfied
    - Applies default values for missing optional params
    """

    def validate(
        self,
        schema: ToolSchema,
        arguments: dict[str, Any],
    ) -> ValidationResult:
        """Validate arguments against a tool schema.

        Args:
            schema: The tool's schema definition.
            arguments: The arguments to validate.

        Returns:
            ValidationResult with pass/fail and details.
        """
        result = ValidationResult(cleaned_args=dict(arguments))
        errors = result.errors
        warnings = result.warnings

        # Check required parameters
        for param in schema.parameters:
            if param.required and param.name not in arguments:
                errors.append(
                    f"Missing required parameter: '{param.name}'"
                )

        # Apply defaults for missing optional params
        for param in schema.parameters:
            if (
                not param.required
                and param.name not in arguments
                and param.default is not None
            ):
                result.cleaned_args[param.name] = param.default
                warnings.append(
                    f"Using default for '{param.name}': {param.default}"
                )

        # Validate types and constraints
        for param in schema.parameters:
            if param.name not in arguments:
                continue

            value = arguments[param.name]

            # Type check
            expected_types = _TYPE_MAP.get(param.param_type.value)
            if expected_types and not isinstance(value, expected_types):
                errors.append(
                    f"Parameter '{param.name}': expected "
                    f"{param.param_type.value}, got {type(value).__name__}"
                )

            # Enum check
            if param.enum and value not in param.enum:
                errors.append(
                    f"Parameter '{param.name}': value '{value}' "
                    f"not in allowed values {param.enum}"
                )

        # Warn about unknown parameters
        known_names = {p.name for p in schema.parameters}
        for key in arguments:
            if key not in known_names:
                warnings.append(f"Unknown parameter: '{key}'")

        result.is_valid = len(errors) == 0

        if not result.is_valid:
            logger.warning(
                f"[tool_validator] Validation failed for '{schema.name}': "
                f"{errors}"
            )
        else:
            logger.debug(f"[tool_validator] Validation passed for '{schema.name}'")

        return result
