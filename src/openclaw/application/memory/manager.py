from __future__ import annotations

from openclaw.domain.memory.model import MemoryLayer, MemoryReference
from openclaw.domain.memory.ports import MemoryRepository


class MemoryManager:
    """An agent only ever reaches its own memory (REQUIREMENTS section 16)."""

    def __init__(self, repository: MemoryRepository) -> None:
        self._repository = repository

    async def load(self, agent_id: str) -> dict[MemoryLayer, str]:
        """Agent memory (MEMORY.md) and user memory (USER.md), loaded at startup."""
        return {
            layer: await self._repository.read(MemoryReference(layer, agent_id))
            for layer in (MemoryLayer.AGENT, MemoryLayer.USER)
        }
