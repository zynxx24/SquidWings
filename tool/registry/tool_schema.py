"""
tool.registry.tool_schema — Tool schema definitions.

Pydantic models for describing tool parameters and schemas
in OpenAI function-calling compatible format.
"""

from __future__ import annotations

from enum import StrEnum, unique
from typing import Any

from pydantic import BaseModel, Field


@unique
class ParamType(StrEnum):
    """JSON Schema parameter types."""
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class ToolParameter(BaseModel):
    """A single tool parameter definition.

    Attributes:
        name: Parameter name.
        param_type: JSON Schema type.
        description: Human-readable description.
        required: Whether this parameter is required.
        default: Default value if not provided.
        enum: Allowed values (if restricted).
        items: Schema for array items (if param_type is array).
    """
    name: str
    param_type: ParamType = ParamType.STRING
    description: str = ""
    required: bool = True
    default: Any = None
    enum: list[str] | None = None
    items: dict[str, Any] | None = None

    def to_json_schema(self) -> dict[str, Any]:
        """Convert to JSON Schema property format.

        Returns:
            dict: JSON Schema property definition.
        """
        schema: dict[str, Any] = {
            "type": self.param_type.value,
            "description": self.description,
        }
        if self.enum:
            schema["enum"] = self.enum
        if self.default is not None:
            schema["default"] = self.default
        if self.items and self.param_type == ParamType.ARRAY:
            schema["items"] = self.items
        return schema


class ToolSchema(BaseModel):
    """Complete tool schema for function calling.

    Generates OpenAI-compatible function calling JSON schema.

    Attributes:
        name: Tool function name.
        description: What the tool does.
        parameters: List of tool parameters.
    """
    name: str
    description: str
    parameters: list[ToolParameter] = Field(default_factory=list)

    def to_openai_schema(self) -> dict[str, Any]:
        """Convert to OpenAI function-calling schema with deterministic ordering.

        Returns:
            dict: OpenAI-compatible tool definition with sorted properties.
        """
        properties: dict[str, Any] = {}
        required: list[str] = []

        # Sort parameters by name for deterministic JSON schema and cache prefix stability
        for param in sorted(self.parameters, key=lambda p: p.name):
            properties[param.name] = param.to_json_schema()
            if param.required:
                required.append(param.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": sorted(required),
                },
            },
        }

    def get_required_params(self) -> list[str]:
        """Get names of required parameters."""
        return [p.name for p in self.parameters if p.required]

    def get_param(self, name: str) -> ToolParameter | None:
        """Get a parameter by name."""
        for p in self.parameters:
            if p.name == name:
                return p
        return None
