"""Mutable state of one ReAct run."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from openclaw.domain.skills.model import Skill
from openclaw.domain.tasks.execution import ExecutionStatus


class ObservationKind(StrEnum):
    USER_REQUEST = "user_request"
    SKILL_LOADED = "skill_loaded"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    POLICY_DENIED = "policy_denied"
    APPROVAL_REJECTED = "approval_rejected"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Observation:
    kind: ObservationKind
    content: str
    source: str | None = None


@dataclass(slots=True)
class RunState:
    request: str
    observations: list[Observation] = field(default_factory=list)
    loaded_skills: dict[str, Skill] = field(default_factory=dict)
    failed_calls: dict[str, str] = field(default_factory=dict)  # call signature -> error
    steps: int = 0
    status: ExecutionStatus | None = None
    answer: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        self.observations.append(Observation(ObservationKind.USER_REQUEST, self.request))

    @property
    def finished(self) -> bool:
        return self.status is not None

    def add_observation(
        self, kind: ObservationKind, content: str, source: str | None = None
    ) -> None:
        self.observations.append(Observation(kind, content, source))

    def load_skill(self, skill: Skill) -> None:
        self.loaded_skills[skill.metadata.id] = skill

    def finish(self, answer: str) -> None:
        self.status, self.answer = ExecutionStatus.COMPLETED, answer

    def fail(self, error: str, status: ExecutionStatus = ExecutionStatus.FAILED) -> None:
        self.status, self.error = status, error
