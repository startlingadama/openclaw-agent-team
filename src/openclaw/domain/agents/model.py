"""Agent aggregate (ARCHITECTURE section 6). An agent is not the LLM."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NewType

from openclaw.domain.tools.permissions import ToolPermissions

AgentId = NewType("AgentId", str)


class AgentStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class AgentProfile:
    soul: str  # SOUL.md
    instructions: str = ""  # AGENTS.md
    heartbeat: str = ""  # HEARTBEAT.md


@dataclass(frozen=True, slots=True)
class Agent:
    id: AgentId
    profile: AgentProfile
    skills: tuple[str, ...] = ()
    tool_permissions: ToolPermissions = ToolPermissions()
    status: AgentStatus = AgentStatus.ACTIVE
    role: str = ""  # display label only (WebChat), never sent to the LLM; empty = not set
