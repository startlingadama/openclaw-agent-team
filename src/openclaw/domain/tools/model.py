"""Tool domain model (ARCHITECTURE section 8)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from openclaw.domain.shared.errors import ValidationError


class RiskLevel(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    EXTERNAL_COMMUNICATION = "external_communication"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.READ

    def validate_arguments(self, arguments: Mapping[str, Any]) -> None:
        """Minimal input validation: required keys only (full schema validation comes later)."""
        missing = [k for k in self.input_schema.get("required", ()) if k not in arguments]
        if missing:
            raise ValidationError(
                f"Tool '{self.name}' is missing required arguments: {', '.join(missing)}"
            )


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
