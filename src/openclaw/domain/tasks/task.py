"""Task entity: a delegated unit of work (REQUIREMENTS section 18)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from openclaw.domain.agents.model import AgentId
from openclaw.domain.skills.model import SkillId


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    from_agent: AgentId
    to_agent: AgentId
    objective: str
    context: Mapping[str, Any] = field(default_factory=dict)
    constraints: tuple[str, ...] = ()
    required_skills: tuple[SkillId, ...] = ()
    required_tools: tuple[str, ...] = ()
    deadline: datetime | None = None
    approval_policy: str | None = None
