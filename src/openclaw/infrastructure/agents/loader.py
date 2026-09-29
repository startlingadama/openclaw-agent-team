"""YamlAgentRepository: builds an `Agent` from `agents/<id>/agent.yaml` and its Markdown profile.

Layout (ARCHITECTURE sections 4 and 20, ADR-017):

    agents/<id>/agent.yaml   -> skills, tool permissions (`tools.allowed`, `approval_required`,
                                `optional`), optional `agent.role` label
    agents/<id>/SOUL.md      -> AgentProfile.soul (required)
    agents/<id>/AGENTS.md    -> AgentProfile.instructions
    agents/<id>/HEARTBEAT.md -> AgentProfile.heartbeat

MEMORY.md and USER.md are memory (layers `agent` / `user`), read by the memory repository.
The profile always lives in `agents/<id>`: the `agent.profile` key of agent.yaml is not a
second location, so the directory name and `agent.id` must agree.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from openclaw.domain.agents.model import Agent, AgentId, AgentProfile
from openclaw.domain.shared.errors import AgentError
from openclaw.domain.tools.permissions import ToolPermissions

AGENT_FILE = "agent.yaml"
MAX_ROLE_LENGTH = 40
_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class YamlAgentRepository:
    """Implements the AgentRepository port. Nothing is cached: files stay the source of truth."""

    def __init__(self, agents_dir: Path | str) -> None:
        self._root = Path(agents_dir)

    async def get(self, agent_id: str) -> Agent:
        if not _AGENT_ID.match(agent_id):  # blocks path traversal
            raise AgentError(f"invalid agent id: {agent_id!r}")
        directory = self._root / agent_id
        if not (directory / AGENT_FILE).is_file():
            known = ", ".join(await self.list_ids()) or "none"
            raise AgentError(f"unknown agent '{agent_id}' (available: {known})")
        return await asyncio.to_thread(self._load, agent_id, directory)

    async def list_ids(self) -> list[str]:
        return await asyncio.to_thread(self._list_ids)

    # -- internals ------------------------------------------------------------------------
    def _list_ids(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(p.parent.name for p in self._root.glob(f"*/{AGENT_FILE}"))

    def _load(self, agent_id: str, directory: Path) -> Agent:
        config = _read_yaml(directory / AGENT_FILE, agent_id)
        header = _mapping(config.get("agent"), "agent", agent_id)
        declared = header.get("id")
        if declared != agent_id:
            raise AgentError(
                f"agent '{agent_id}': {AGENT_FILE} declares id {declared!r}, "
                "which must match the directory name"
            )
        tools = _mapping(config.get("tools"), "tools", agent_id)
        allowed = _names(tools.get("allowed"), "tools.allowed", agent_id)
        approval_required = _names(
            tools.get("approval_required"), "tools.approval_required", agent_id
        )
        optional = _names(tools.get("optional"), "tools.optional", agent_id)
        stray = sorted(set(optional) - set(allowed) - set(approval_required))
        if stray:
            raise AgentError(
                f"agent '{agent_id}': 'tools.optional' lists tools that are not permitted "
                f"(add them to allowed or approval_required): {', '.join(stray)}"
            )
        soul = _read_text(directory / "SOUL.md")
        if not soul.strip():
            raise AgentError(f"agent '{agent_id}': SOUL.md is missing or empty")
        return Agent(
            id=AgentId(agent_id),
            profile=AgentProfile(
                soul=soul,
                instructions=_read_text(directory / "AGENTS.md"),
                heartbeat=_read_text(directory / "HEARTBEAT.md"),
            ),
            skills=tuple(_names(config.get("skills"), "skills", agent_id)),
            role=_role(header.get("role"), agent_id),
            tool_permissions=ToolPermissions.of(
                allowed=allowed, approval_required=approval_required, optional=optional
            ),
        )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _read_yaml(path: Path, agent_id: str) -> Mapping[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AgentError(f"agent '{agent_id}': {AGENT_FILE} cannot be read ({exc})") from exc
    return _mapping(data, AGENT_FILE, agent_id)


def _mapping(value: Any, label: str, agent_id: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AgentError(f"agent '{agent_id}': '{label}' must be a mapping")
    return value


def _role(value: Any, agent_id: str) -> str:
    """The optional display label: a short, non-empty, single-line text (never an instruction)."""
    if value is None:
        return ""
    label = value.strip() if isinstance(value, str) else ""
    if not label or "\n" in label or len(label) > MAX_ROLE_LENGTH:
        raise AgentError(
            f"agent '{agent_id}': 'agent.role' must be a single-line text of 1 to "
            f"{MAX_ROLE_LENGTH} characters"
        )
    return label


def _names(value: Any, label: str, agent_id: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise AgentError(f"agent '{agent_id}': '{label}' must be a list of names")
    return [v.strip() for v in value]
