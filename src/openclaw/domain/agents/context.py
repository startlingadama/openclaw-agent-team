"""Everything the LLM sees when taking a decision."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from openclaw.domain.agents.model import AgentProfile
from openclaw.domain.agents.state import Observation
from openclaw.domain.memory.model import MemoryLayer
from openclaw.domain.skills.model import Skill, SkillMetadata
from openclaw.domain.tools.model import ToolSpec


@dataclass(frozen=True, slots=True)
class DecisionContext:
    agent_id: str
    profile: AgentProfile
    memory: Mapping[MemoryLayer, str]  # AGENT = MEMORY.md, USER = USER.md
    available_skills: tuple[SkillMetadata, ...]  # name + description only
    loaded_skills: tuple[Skill, ...]  # full instructions, only once selected
    tools: tuple[ToolSpec, ...]  # only tools the agent is permitted to use
    observations: tuple[Observation, ...]
    step: int
    final_step: bool = False  # step limit reached: the only possible decision is Finish
