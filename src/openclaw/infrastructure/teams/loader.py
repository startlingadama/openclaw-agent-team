"""YamlTeamRepository: builds a `Team` from `teams/<id>/team.yaml` (REQUIREMENTS sections 15-17).

    team:
      id: default            # must match the directory name
      pattern: supervisor    # supervisor | peer_to_peer | shared_vault
      supervisor: ceo        # required for the supervisor pattern
      members: [github, ...]

This file is the only source of truth for team membership. Whether a member is a defined agent
is checked when a task is delegated to it, not here.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from openclaw.domain.agents.model import AgentId
from openclaw.domain.shared.errors import TeamError, ValidationError
from openclaw.domain.teams.team import Team, TeamPattern

TEAM_FILE = "team.yaml"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class YamlTeamRepository:
    """Implements the TeamRepository port. Nothing is cached: files stay the source of truth."""

    def __init__(self, teams_dir: Path | str) -> None:
        self._root = Path(teams_dir)

    async def get(self, team_id: str) -> Team:
        if not _ID.match(team_id):  # blocks path traversal
            raise TeamError(f"invalid team id: {team_id!r}")
        path = self._root / team_id / TEAM_FILE
        if not path.is_file():
            known = ", ".join(await self.list_ids()) or "none"
            raise TeamError(f"unknown team '{team_id}' (available: {known})")
        return await asyncio.to_thread(self._load, team_id, path)

    async def list_ids(self) -> list[str]:
        return await asyncio.to_thread(self._list_ids)

    # -- internals ------------------------------------------------------------------------
    def _list_ids(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(p.parent.name for p in self._root.glob(f"*/{TEAM_FILE}"))

    def _load(self, team_id: str, path: Path) -> Team:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise TeamError(f"team '{team_id}': {TEAM_FILE} cannot be read ({exc})") from exc
        config = _mapping(_mapping(data, team_id).get("team"), team_id)
        if config.get("id") != team_id:
            raise TeamError(
                f"team '{team_id}': {TEAM_FILE} declares id {config.get('id')!r}, "
                "which must match the directory name"
            )
        try:
            pattern = TeamPattern(config.get("pattern", TeamPattern.SUPERVISOR))
        except ValueError:
            valid = ", ".join(p.value for p in TeamPattern)
            raise TeamError(
                f"team '{team_id}': unknown pattern {config.get('pattern')!r} (valid: {valid})"
            ) from None
        supervisor = _agent_id(config.get("supervisor"), "supervisor", team_id, optional=True)
        members = tuple(
            _agent_id(m, "members", team_id) for m in _list(config.get("members"), team_id)
        )
        if len(set(members)) != len(members):
            raise TeamError(f"team '{team_id}': a member is listed twice")
        if supervisor is not None and supervisor in members:
            raise TeamError(f"team '{team_id}': the supervisor '{supervisor}' cannot be a member")
        try:
            return Team(team_id, pattern, members, supervisor)
        except ValidationError as exc:
            raise TeamError(str(exc)) from exc


def _mapping(value: Any, team_id: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TeamError(f"team '{team_id}': expected a mapping in {TEAM_FILE}")
    return value


def _list(value: Any, team_id: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TeamError(f"team '{team_id}': 'members' must be a list of agent ids")
    return value


def _agent_id(value: Any, label: str, team_id: str, *, optional: bool = False) -> AgentId | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _ID.match(value.strip()):
        raise TeamError(f"team '{team_id}': invalid agent id in '{label}': {value!r}")
    return AgentId(value.strip())
