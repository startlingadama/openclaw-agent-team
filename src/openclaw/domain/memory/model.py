"""Memory layers (REQUIREMENTS section 14) and references to a memory document."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from openclaw.domain.agents.model import AgentId
from openclaw.domain.shared.errors import ValidationError


class MemoryLayer(StrEnum):
    WORKING = "working"
    SESSION = "session"
    AGENT = "agent"
    USER = "user"
    SHARED_TEAM = "shared_team"


_PER_AGENT_LAYERS = frozenset({MemoryLayer.AGENT, MemoryLayer.USER})


@dataclass(frozen=True, slots=True)
class MemoryReference:
    """Points to one memory document: a layer, and the owning agent for private layers."""

    layer: MemoryLayer
    agent_id: AgentId | None = None

    def __post_init__(self) -> None:
        if self.layer in _PER_AGENT_LAYERS and self.agent_id is None:
            raise ValidationError(f"The '{self.layer}' memory layer requires an agent_id")


@dataclass(frozen=True, slots=True)
class MemoryHit:
    reference: MemoryReference
    section: str | None
    line: int
    text: str


class MemoryOperation(StrEnum):
    WRITE = "write"
    UPDATE = "update"
    ARCHIVE = "archive"


@dataclass(frozen=True, slots=True)
class MemoryChange:
    """One entry of a memory document's history (REQUIREMENTS section 13).

    - WRITE:   `before` / `after` are the whole document; `section` is None.
    - UPDATE:  `before` / `after` are the `## section` block ('' when it did not exist).
    - ARCHIVE: `before` is the archived block, `after` is ''.
    """

    reference: MemoryReference
    operation: MemoryOperation
    section: str | None
    timestamp: datetime
    before: str
    after: str
