from __future__ import annotations

from typing import Protocol

from openclaw.domain.teams.team import Team


class TeamRepository(Protocol):
    """Where team definitions come from (`teams/<id>/team.yaml` in infrastructure)."""

    async def get(self, team_id: str) -> Team:
        """Return the team. Raises TeamError if it is unknown or its definition is invalid."""
        ...

    async def list_ids(self) -> list[str]:
        """Ids of every defined team, sorted."""
        ...
