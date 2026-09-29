"""Tool registry: tools are global and independent from agents (REQUIREMENTS section 9).

Which agent may use which tool is decided by permissions and the PolicyEngine, not here.
Adding a tool means adding a provider (its port/adapter), never changing the runtime.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from openclaw.domain.agents.model import AgentId
from openclaw.domain.shared.errors import ToolError, ValidationError
from openclaw.domain.tools.model import ToolCall, ToolSpec


class ToolProvider(Protocol):
    """A ToolPort that declares the tool names it serves."""

    tool_names: frozenset[str]

    def get_spec(self, name: str) -> ToolSpec | None: ...

    async def execute(self, call: ToolCall, caller: AgentId) -> Any: ...


class ToolRegistry:
    """Implements ToolPort by dispatching each tool name to the provider that serves it."""

    def __init__(self, providers: Iterable[ToolProvider] = ()) -> None:
        self._providers: dict[str, ToolProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: ToolProvider) -> None:
        """All-or-nothing: a duplicate or inconsistent name registers none of the provider."""
        for name in provider.tool_names:
            if name in self._providers:
                raise ValidationError(f"tool already registered: {name}")
            spec = provider.get_spec(name)
            if spec is None or spec.name != name:
                raise ValidationError(f"provider does not describe the tool it declares: {name}")
        for name in provider.tool_names:
            self._providers[name] = provider

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def get_spec(self, name: str) -> ToolSpec | None:
        provider = self._providers.get(name)
        return provider.get_spec(name) if provider else None

    async def execute(self, call: ToolCall, caller: AgentId) -> Any:
        provider = self._providers.get(call.name)
        if provider is None:
            raise ToolError(f"unknown tool: {call.name}")
        return await provider.execute(call, caller)
