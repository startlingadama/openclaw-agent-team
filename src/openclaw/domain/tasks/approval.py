"""Approval entity (ADR-003, ADR-015, REQUIREMENTS section 20)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from openclaw.domain.tools.model import RiskLevel, ToolCall


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class Approval:
    execution_id: str
    agent_id: str
    call: ToolCall
    risk_level: RiskLevel
    reason: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    comment: str = ""

    def resolve(self, approved: bool, comment: str = "") -> Approval:
        status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        return replace(self, status=status, comment=comment)
