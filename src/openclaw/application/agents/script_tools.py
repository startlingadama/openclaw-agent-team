"""Tool calls made by a script of the sandbox (ADR-025, ARCHITECTURE section 15.1).

Same rules as a direct call of the agent, applied by the same components:

    script -> ScriptToolBroker -> PolicyEngine -> approval? -> ToolPort

The caller is the agent that runs the script; its permissions decide (ADR-009), so a script
never gains a tool. What a script does is part of the execution trace of the run that started it
(ADR-024): the events carry `via: "script"`. Outside a run there is nothing to attach to and
nothing is executed.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from openclaw.application.agents.action_executor import ActionExecutor, RetryPolicy
from openclaw.application.agents.lineage import current_execution, current_task
from openclaw.domain.agents.model import AgentId
from openclaw.domain.agents.ports import AgentRepository
from openclaw.domain.shared.errors import ApprovalError, OpenClawError, ToolError
from openclaw.domain.tasks.approval import Approval, ApprovalStatus
from openclaw.domain.tasks.execution import EventType, ExecutionEvent
from openclaw.domain.tasks.ports import ApprovalPort, EventSink
from openclaw.domain.tools.model import ToolCall
from openclaw.domain.tools.policy import PolicyEngine, PolicyOutcome
from openclaw.domain.tools.ports import ToolPort

_VIA = {"via": "script"}


class ScriptToolBroker:
    def __init__(
        self,
        *,
        agents: AgentRepository,
        tools: ToolPort,
        approvals: ApprovalPort,
        sink: EventSink | None = None,
        policy: PolicyEngine | None = None,
        retry: RetryPolicy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._agents = agents
        self._tools = tools
        self._approvals = approvals
        self._sink = sink
        self._policy = policy or PolicyEngine()
        self._executor = ActionExecutor(tools, retry)
        self._clock = clock

    async def call(self, call: ToolCall, caller: AgentId) -> Any:
        execution_id = current_execution()
        if execution_id is None:
            raise ToolError("a script can only call tools during an agent run")
        task_id = current_task() or ""
        agent = await self._agents.get(caller)
        spec = self._tools.get_spec(call.name)
        verdict = self._policy.evaluate(agent.tool_permissions, spec, call)

        async def emit(type_: EventType, **data: Any) -> None:
            if self._sink is not None:
                event = ExecutionEvent(
                    execution_id, task_id, agent.id, type_, self._clock(), {**_VIA, **data}
                )
                await self._sink.record(event)

        if verdict.outcome is PolicyOutcome.DENY:
            await emit(EventType.POLICY_DENIED, tool=call.name, reason=verdict.reason)
            raise ToolError(f"Tool '{call.name}' was denied: {verdict.reason}")

        assert spec is not None
        if verdict.outcome is PolicyOutcome.REQUIRE_APPROVAL:
            await emit(
                EventType.APPROVAL_REQUESTED,
                tool=call.name,
                input=dict(call.arguments),
                reason=verdict.reason,
            )
            request = Approval(execution_id, agent.id, call, spec.risk_level, verdict.reason)
            try:
                resolved = await self._approvals.request(request)
            except ApprovalError as exc:  # never execute without a positive answer
                resolved = request.resolve(False, str(exc))
            await emit(
                EventType.APPROVAL_RESOLVED,
                tool=call.name,
                approval=resolved.status,
                comment=resolved.comment,
            )
            if resolved.status is not ApprovalStatus.APPROVED:
                suffix = f" ({resolved.comment})" if resolved.comment else ""
                raise ToolError(f"The human rejected the call to '{call.name}'{suffix}")

        await emit(EventType.TOOL_CALLED, tool=call.name, input=dict(call.arguments))
        started = time.perf_counter()
        try:
            output = await self._executor.execute(call, spec, agent.id)
        except OpenClawError as exc:
            await emit(
                EventType.TOOL_RESULT,
                tool=call.name,
                status="error",
                error=str(exc),
                duration_ms=_ms(started),
            )
            raise
        await emit(
            EventType.TOOL_RESULT,
            tool=call.name,
            status="success",
            duration_ms=_ms(started),
            output=output if isinstance(output, str) else repr(output)[:2000],
        )
        return output


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
