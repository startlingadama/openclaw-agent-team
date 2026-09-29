from __future__ import annotations

from openclaw.domain.agents.model import Agent
from openclaw.domain.shared.errors import SkillError
from openclaw.domain.skills.model import Skill, SkillMetadata
from openclaw.domain.skills.ports import SkillRepository


class SkillResolver:
    """Progressive disclosure: metadata first, full SKILL.md only on demand (ADR-007)."""

    def __init__(self, repository: SkillRepository) -> None:
        self._repository = repository

    async def available(self, agent: Agent) -> tuple[SkillMetadata, ...]:
        return tuple(await self._repository.list_metadata(agent.skills))

    async def load(self, agent: Agent, skill_id: str) -> Skill:
        if skill_id not in agent.skills:
            raise SkillError(f"Skill '{skill_id}' is not assigned to agent '{agent.id}'")
        return await self._repository.load(skill_id)
