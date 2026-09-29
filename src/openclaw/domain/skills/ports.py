from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from openclaw.domain.skills.model import Skill, SkillMetadata


class SkillRepository(Protocol):
    async def discover(self) -> list[SkillMetadata]:
        """Skill registry: name + description of every available skill (progressive disclosure)."""
        ...

    async def list_metadata(self, skill_ids: Sequence[str]) -> list[SkillMetadata]: ...

    async def load(self, skill_id: str) -> Skill:
        """Load the complete SKILL.md. Raises SkillError if unknown."""
        ...
