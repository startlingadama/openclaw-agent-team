"""Team entity and orchestration patterns (REQUIREMENTS sections 15-17, ADR-010)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from openclaw.domain.agents.model import AgentId
from openclaw.domain.shared.errors import ValidationError


class TeamPattern(StrEnum):
    SUPERVISOR = "supervisor"  # hub-and-spoke, first pattern (ADR-010)
    PEER_TO_PEER = "peer_to_peer"
    SHARED_VAULT = "shared_vault"


@dataclass(frozen=True, slots=True)
class Team:
    id: str
    pattern: TeamPattern
    members: tuple[AgentId, ...] = ()
    supervisor: AgentId | None = None

    def __post_init__(self) -> None:
        if self.pattern is TeamPattern.SUPERVISOR and self.supervisor is None:
            raise ValidationError(f"Team '{self.id}' uses the supervisor pattern but has none")
