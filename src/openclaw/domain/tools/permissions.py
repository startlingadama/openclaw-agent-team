"""Least-privilege tool permissions (REQUIREMENTS section 10, ADR-009)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolPermissions:
    """`allowed` tools run directly; `approval_required` tools are permitted but need approval.

    `optional` marks permitted tools (a subset of the two sets above) whose absence is tolerated:
    when no provider serves one, the agent runs without it instead of being refused. It never
    widens what the agent may use.
    """

    allowed: frozenset[str] = frozenset()
    approval_required: frozenset[str] = frozenset()
    optional: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        stray = self.optional - self.allowed - self.approval_required
        if stray:
            raise ValueError(f"optional tools must also be permitted: {', '.join(sorted(stray))}")

    @classmethod
    def of(
        cls,
        allowed: Iterable[str] = (),
        approval_required: Iterable[str] = (),
        optional: Iterable[str] = (),
    ) -> ToolPermissions:
        return cls(frozenset(allowed), frozenset(approval_required), frozenset(optional))

    def permits(self, tool_name: str) -> bool:
        return tool_name in self.allowed or tool_name in self.approval_required

    def lists_approval(self, tool_name: str) -> bool:
        return tool_name in self.approval_required

    def is_optional(self, tool_name: str) -> bool:
        return tool_name in self.optional
