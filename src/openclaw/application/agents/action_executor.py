"""Executes tool calls. Retries are limited to READ tools (REQUIREMENTS section 23)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openclaw.domain.agents.model import AgentId
from openclaw.domain.shared.errors import OpenClawError, ToolError
from openclaw.domain.tools.model import RiskLevel, ToolCall, ToolSpec
from openclaw.domain.tools.ports import ToolPort


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 1  # no retry unless a policy asks for it


class ActionExecutor:
    def __init__(self, tools: ToolPort, retry: RetryPolicy | None = None) -> None:
        self._tools = tools
        self._retry = retry or RetryPolicy()

    async def execute(self, call: ToolCall, spec: ToolSpec, caller: AgentId) -> Any:
        spec.validate_arguments(call.arguments)
        # Anything that is not a pure read is never repeated automatically.
        attempts = self._retry.max_attempts if spec.risk_level is RiskLevel.READ else 1
        for attempt in range(1, attempts + 1):
            try:
                return await self._tools.execute(call, caller)
            except OpenClawError as exc:
                if exc.retryable and attempt < attempts:
                    continue
                raise
            except Exception as exc:
                raise ToolError(f"{call.name} failed: {type(exc).__name__}: {exc}") from exc
        raise AssertionError("unreachable")  # pragma: no cover
