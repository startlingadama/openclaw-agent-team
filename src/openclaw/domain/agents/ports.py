from __future__ import annotations

from typing import Protocol

from openclaw.domain.agents.context import DecisionContext
from openclaw.domain.agents.decision import Decision
from openclaw.domain.agents.model import Agent


class LLMPort(Protocol):
    """Provider-agnostic LLM (ADR-011). DeepSeekAdapter implements it in infrastructure."""

    async def decide(self, context: DecisionContext) -> Decision:
        """Return the next decision. Raises LLMError / AuthenticationError on failure."""
        ...


class AgentRepository(Protocol):
    """Where agent definitions come from (agent.yaml + Markdown profile in infrastructure)."""

    async def get(self, agent_id: str) -> Agent:
        """Return the agent. Raises AgentError if it is unknown or its definition is invalid."""
        ...

    async def list_ids(self) -> list[str]:
        """Ids of every defined agent, sorted."""
        ...
