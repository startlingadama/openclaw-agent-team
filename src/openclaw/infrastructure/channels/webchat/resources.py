"""Read-only WebChat resource queries and frontend DTO projections."""

from __future__ import annotations

from typing import Any

from openclaw.domain.agents.model import AgentId
from openclaw.domain.memory.model import MemoryLayer, MemoryReference
from openclaw.domain.shared.errors import AgentError
from openclaw.infrastructure.channels.webchat.serialization import (
    execution_summary,
    translate_event,
)
from openclaw.infrastructure.channels.webchat.task_store import TaskStore

DEFAULT_ROLE = "AI specialist"  # shown when an agent declares no `agent.role`


class WebChatResources:
    def __init__(self, *, app: Any, tasks: TaskStore, approvals: Any) -> None:
        self.app = app
        self.tasks = tasks
        self.approvals = approvals

    async def health(self) -> dict[str, Any]:
        return {"status": "ok", "version": "0.1.0", "llm": "DeepSeek"}

    async def list_agents(self) -> list[dict[str, Any]]:
        ids = await self.app.agents.list_ids()
        result = []
        for agent_id in ids:
            agent = await self.app.agents.get(agent_id)
            allowed_tools = (
                agent.tool_permissions.allowed | agent.tool_permissions.approval_required
            )
            result.append(
                {
                    "id": agent.id,
                    "name": _agent_name(agent_id),
                    "short": _agent_name(agent_id),
                    "role": agent.role or DEFAULT_ROLE,
                    "description": _first_line(agent.profile.instructions or agent.profile.soul),
                    "status": "online",
                    "currentTask": None,
                    "capabilities": list(agent.skills) or ["General assistance"],
                    "skills": [
                        {
                            "name": skill_id.split("/")[-1].replace("-", " ").title(),
                            "description": "Agent capability",
                            "status": "active",
                            "scripts": 1,
                            "references": 1,
                        }
                        for skill_id in agent.skills
                    ],
                    "tools": [
                        self._agent_tool(name, name in agent.tool_permissions.approval_required)
                        for name in sorted(allowed_tools)
                    ],
                    "tasksCount": self.tasks.count_for_agent(agent_id),
                }
            )
        return result

    async def _role_of(self, agent_id: str) -> str:
        """The label of an agent; a team member without an agent file never breaks the listing."""
        try:
            return (await self.app.agents.get(agent_id)).role or DEFAULT_ROLE
        except AgentError:
            return DEFAULT_ROLE

    def _agent_tool(self, name: str, approval_required: bool) -> dict[str, Any]:
        """One tool of an agent: its real risk level, and whether the agent needs approval."""
        spec = self.app.tools.get_spec(name)
        if spec is None:  # declared but not served: never shown as harmless
            return {"name": name, "description": name, "permission": "APPROVAL REQUIRED"}
        permission = _permission(spec.risk_level.value)
        if approval_required:
            permission = "APPROVAL REQUIRED"
        return {"name": name, "description": spec.description, "permission": permission}

    async def get_agent(self, agent_id: str) -> dict[str, Any]:
        for agent in await self.list_agents():
            if agent["id"] == agent_id:
                return agent
        raise KeyError(agent_id)

    async def list_teams(self) -> list[dict[str, Any]]:
        ids = await self.app.teams.list_ids()
        result = []
        for team_id in ids:
            team = await self.app.teams.get(team_id)
            lead = team.supervisor or (team.members[0] if team.members else "")
            roles = {member: await self._role_of(str(member)) for member in team.members}
            result.append(
                {
                    "id": team.id,
                    "name": team.id.replace("-", " ").title(),
                    "purpose": f"{team.pattern.value} orchestration team",
                    "lead": {"id": str(lead), "name": _agent_name(str(lead))},
                    "members": [
                        {
                            "agentId": member,
                            "role": "lead" if member == lead else "member",
                            "responsibility": roles[member],
                        }
                        for member in team.members
                    ],
                    "delegationRules": ["Supervisor routes tasks to the best specialist."],
                    "sharedMemory": ["workspace/shared/MEMORY.md"],
                    "recentTaskIds": [],
                }
            )
        return result

    async def get_team(self, team_id: str) -> dict[str, Any]:
        for team in await self.list_teams():
            if team["id"] == team_id:
                return team
        raise KeyError(team_id)

    async def list_executions(self) -> list[dict[str, Any]]:
        history = self.app.history
        if history is None:
            return []
        result = []
        for summary in await history.recent(20):
            node = await history.tree(summary.execution_id)
            result.append(execution_summary(summary, node.record.events))
        return result

    async def get_execution(self, execution_id: str) -> dict[str, Any]:
        history = self.app.history
        if history is None:
            raise KeyError(execution_id)
        node = await history.tree(execution_id)
        summary = node.record.summary
        return {
            **execution_summary(summary, node.record.events),
            "answer": node.record.answer,
            "error": summary.error,
        }

    async def get_execution_events(self, execution_id: str) -> list[dict[str, Any]]:
        history = self.app.history
        if history is None:
            raise KeyError(execution_id)
        node = await history.tree(execution_id)
        return [
            mapped for event in node.record.events if (mapped := translate_event(event)) is not None
        ]

    async def list_skills(self) -> list[dict[str, Any]]:
        metadata = await self.app.skills.discover()
        compatible: dict[str, list[str]] = {skill.id: [] for skill in metadata}
        for agent_id in await self.app.agents.list_ids():
            agent = await self.app.agents.get(agent_id)
            for skill_id in agent.skills:
                if skill_id in compatible:
                    compatible[skill_id].append(str(agent_id))
        return [
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "status": "active",
                "agents": compatible[skill.id],
                "files": await self.app.skills.file_catalog(skill.id),
            }
            for skill in metadata
        ]

    async def get_skill(self, skill_id: str) -> dict[str, Any]:
        metadata = next(
            (item for item in await self.app.skills.discover() if item.id == skill_id),
            None,
        )
        if metadata is None:
            raise KeyError(skill_id)
        compatible = []
        for agent_id in await self.app.agents.list_ids():
            agent = await self.app.agents.get(agent_id)
            if skill_id in agent.skills:
                compatible.append(str(agent_id))
        return {
            "id": metadata.id,
            "name": metadata.name,
            "description": metadata.description,
            "status": "active",
            "agents": compatible,
            "files": await self.app.skills.file_catalog(skill_id),
        }

    async def get_skill_instructions(self, skill_id: str) -> dict[str, str]:
        skill = await self.app.skills.load(skill_id)
        return {"instructions": skill.instructions}

    async def list_tools(self) -> list[dict[str, Any]]:
        allowed_by_agent: dict[str, set[str]] = {}
        for agent_id in await self.app.agents.list_ids():
            agent = await self.app.agents.get(agent_id)
            for name in agent.tool_permissions.allowed | agent.tool_permissions.approval_required:
                allowed_by_agent.setdefault(name, set()).add(str(agent_id))
        result = []
        for name in self.app.tools.names:
            spec = self.app.tools.get_spec(name)
            if spec is None:
                continue
            risk_level = spec.risk_level.value
            permission = _permission(risk_level)
            risk = {"read": "LOW", "write": "MEDIUM"}.get(risk_level, "HIGH")
            result.append(
                {
                    "id": name,
                    "group": name.split(".")[0],
                    "name": name,
                    "description": spec.description,
                    "agents": sorted(allowed_by_agent.get(name, set())),
                    "permission": permission,
                    "risk": risk,
                }
            )
        return result

    async def get_memory(self, agent_id: str) -> list[dict[str, Any]]:
        result = []
        for name, key in (("MEMORY.md", "agent"), ("USER.md", "user")):
            layer = MemoryLayer.AGENT if key == "agent" else MemoryLayer.USER
            reference = MemoryReference(layer, AgentId(agent_id))
            content = await self.app.memory.read(reference)
            result.append(
                {
                    "name": name,
                    "scope": "Agent Memory" if key == "agent" else "User Memory",
                    "updated": "just now",
                    "content": content,
                }
            )
        return result

    async def write_memory(self, agent_id: str, layer: str, content: str) -> list[dict[str, Any]]:
        memory_layer = {"agent": MemoryLayer.AGENT, "user": MemoryLayer.USER}.get(layer)
        if memory_layer is None:
            raise ValueError("memory layer must be 'agent' or 'user'")
        await self.app.memory.write(
            MemoryReference(memory_layer, AgentId(agent_id)),
            content,
        )
        return await self.get_memory(agent_id)

    async def list_approvals(self) -> list[dict[str, Any]]:
        return self.approvals.pending()

    async def approve_approval(self, approval_id: str) -> dict[str, Any]:
        resolved = self.approvals.resolve(approval_id, True)
        return {
            "id": approval_id,
            "status": "approved",
            "agentId": getattr(resolved, "agent_id", "agent"),
            "action": getattr(getattr(resolved, "call", None), "name", "tool call"),
        }

    async def reject_approval(self, approval_id: str, reason: str | None = None) -> dict[str, Any]:
        resolved = self.approvals.resolve(approval_id, False, reason or "")
        return {
            "id": approval_id,
            "status": "rejected",
            "agentId": getattr(resolved, "agent_id", "agent"),
            "action": getattr(getattr(resolved, "call", None), "name", "tool call"),
            "reason": reason or "",
        }


def _permission(risk_level: str) -> str:
    return {
        "read": "READ",
        "write": "WRITE",
        "destructive": "APPROVAL REQUIRED",
        "external_communication": "EXTERNAL ACTION",
    }.get(risk_level, "APPROVAL REQUIRED")


def _first_line(text: str) -> str:
    if not text:
        return "General assistant"
    return text.strip().splitlines()[0].strip()[:180] or "General assistant"


def _agent_name(agent_id: str) -> str:
    return agent_id.replace("-", " ").title() if agent_id else "Agent"
