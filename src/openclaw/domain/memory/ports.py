from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from openclaw.domain.memory.model import MemoryChange, MemoryHit, MemoryReference


class MemoryRepository(Protocol):
    """REQUIREMENTS section 13."""

    async def read(self, reference: MemoryReference) -> str:
        """Return the document content ('' if nothing has been written yet)."""
        ...

    async def write(self, reference: MemoryReference, content: str) -> None:
        """Replace the whole document."""
        ...

    async def update(self, reference: MemoryReference, section: str, content: str) -> None:
        """Replace the body of a `## section`, creating the section if it does not exist."""
        ...

    async def archive(self, reference: MemoryReference, section: str) -> None:
        """Move a `## section` out of the live memory into the archive."""
        ...

    async def search(self, query: str, references: Sequence[MemoryReference]) -> list[MemoryHit]:
        """Case-insensitive text search (no vector index, ADR-018)."""
        ...

    async def history(
        self, reference: MemoryReference, limit: int | None = None
    ) -> list[MemoryChange]:
        """Changes made to one document, newest first. Read-only: never alters the memory."""
        ...
