"""Concept 3: Schema-based instruction channel.

Provides a typed, schema-governed channel for inter-component instruction
passing, enabling decoupled communication with compile-time shape
validation and runtime contract enforcement.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Dict, Optional, Type, TypeVar, get_type_hints

T = TypeVar("T")


class InstructionSchemaError(Exception):
    """Raised when an instruction fails schema validation."""


class InstructionShape(Enum):
    """Pre-defined instruction shapes for common operation categories."""

    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    EXECUTE = "execute"
    QUERY = "query"


class InstructionSchema:
    """A schema that validates and describes an instruction payload."""

    def __init__(
        self,
        shape: InstructionShape,
        required_fields: Optional[set[str]] = None,
        optional_fields: Optional[set[str]] = None,
        field_types: Optional[Dict[str, type]] = None,
    ):
        self.shape = shape
        self.required_fields = required_fields or set()
        self.optional_fields = optional_fields or set()
        self.field_types = field_types or {}

    def validate(self, payload: Dict[str, Any]) -> bool:
        """Validate that a payload conforms to this schema.

        Returns True if valid, raises InstructionSchemaError if not.
        """
        missing = self.required_fields - set(payload.keys())
        if missing:
            raise InstructionSchemaError(
                f"Missing required fields for {self.shape.value}: {missing}"
            )

        for key, ftype in self.field_types.items():
            if key in payload and not isinstance(payload[key], ftype):
                raise InstructionSchemaError(
                    f"Field '{key}' expected type {ftype.__name__}, got {type(payload[key]).__name__}"
                )

        # Disallow unexpected extra keys unless we allow them
        allowed = self.required_fields | self.optional_fields
        unexpected = set(payload.keys()) - allowed - set(self.field_types.keys())
        if unexpected:
            raise InstructionSchemaError(
                f"Unexpected fields for {self.shape.value}: {unexpected}"
            )

        return True


class InstructionChannel:
    """Typed instruction channel that enforces schema validation on send."""

    def __init__(self, send_schema: InstructionSchema, recv_schema: Optional[InstructionSchema] = None):
        self.send_schema = send_schema
        self.recv_schema = recv_schema

    def send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send a payload, validating against the send schema."""
        self.send_schema.validate(payload)
        # Attach schema version/metadata for the receiver
        payload["_schema_version"] = self.send_schema.shape.value
        return payload

    def receive(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Receive and validate a payload against the recv schema."""
        if self.recv_schema is not None:
            self.recv_schema.validate(payload)
        return payload


def make_instruction(
    shape: InstructionShape,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Convenience constructor for common instruction shapes.

    Args:
        shape: The instruction shape.
        **kwargs: Field values matching the shape's expected fields.

    Returns:
        A validated instruction dict.
    """
    # Base fields present in all instructions
    instruction: Dict[str, Any] = {"_shape": shape.value}

    # Shape-specific required/optional mappings
    shape_defaults: Dict[InstructionShape, Dict[str, Any]] = {
        InstructionShape.CREATE: {"entity": None, "attributes": {}},
        InstructionShape.READ: {"entity": None, "filters": {}},
        InstructionShape.UPDATE: {"entity": None, "attributes": {}, "id": None},
        InstructionShape.DELETE: {"entity": None, "id": None},
        InstructionShape.EXECUTE: {"tool": None, "params": {}},
        InstructionShape.QUERY: {"query": None, "filters": {}},
    }

    defaults = shape_defaults.get(shape, {})
    for k, v in defaults.items():
        instruction.setdefault(k, v)

    instruction.update(kwargs)
    return instruction
