from __future__ import annotations

from collections.abc import Mapping

from openclaw.domain.agents.context import DecisionContext
from openclaw.domain.agents.model import Agent
from openclaw.domain.agents.state import RunState
from openclaw.domain.memory.model import MemoryLayer
from openclaw.domain.skills.model import SkillMetadata
from openclaw.domain.tools.model import ToolSpec


class ContextBuilder:
    def build(
        self,
        *,
        agent: Agent,
        memory: Mapping[MemoryLayer, str],
        skills: tuple[SkillMetadata, ...],
        tools: tuple[ToolSpec, ...],
        state: RunState,
    ) -> DecisionContext:
        return DecisionContext(
            agent_id=agent.id,
            profile=agent.profile,
            memory=memory,
            available_skills=skills,
            loaded_skills=tuple(state.loaded_skills.values()),
            tools=tools,
            observations=tuple(state.observations),
            step=state.steps,
        )
