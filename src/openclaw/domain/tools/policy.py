"""Policy engine: decides whether a tool call is denied, allowed or needs approval."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from openclaw.domain.tools.model import RiskLevel, ToolCall, ToolSpec
from openclaw.domain.tools.permissions import ToolPermissions


class PolicyOutcome(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    outcome: PolicyOutcome
    reason: str = ""


DEFAULT_ALWAYS_APPROVE = frozenset({RiskLevel.DESTRUCTIVE, RiskLevel.EXTERNAL_COMMUNICATION})


class PolicyEngine:
    def __init__(self, always_approve: frozenset[RiskLevel] = DEFAULT_ALWAYS_APPROVE) -> None:
        self._always_approve = always_approve

    def evaluate(
        self, permissions: ToolPermissions, spec: ToolSpec | None, call: ToolCall
    ) -> PolicyDecision:
        if not permissions.permits(call.name):
            return PolicyDecision(PolicyOutcome.DENY, "tool not permitted for this agent")
        if spec is None:
            return PolicyDecision(PolicyOutcome.DENY, "unknown tool")
        if permissions.lists_approval(call.name):
            return PolicyDecision(PolicyOutcome.REQUIRE_APPROVAL, "listed in approval_required")
        if spec.risk_level in self._always_approve:
            return PolicyDecision(
                PolicyOutcome.REQUIRE_APPROVAL, f"risk level '{spec.risk_level}' requires approval"
            )
        return PolicyDecision(PolicyOutcome.ALLOW)
