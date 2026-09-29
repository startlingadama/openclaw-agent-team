"""Explicit ReAct runtime (ADR-004).

Application component (ARCHITECTURE sections 3, 17, 21); its ports live in the domain.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from openclaw.application.agents.action_executor import ActionExecutor, RetryPolicy
from openclaw.application.agents.context_builder import ContextBuilder
from openclaw.application.agents.lineage import current_execution, execution_scope
from openclaw.application.agents.skill_resolver import SkillResolver
from openclaw.application.agents.tool_resolver import ToolResolver
from openclaw.application.memory.manager import MemoryManager
from openclaw.domain.agents.context import DecisionContext
from openclaw.domain.agents.decision import CallTool, Decision, Finish, UseSkill
from openclaw.domain.agents.model import Agent, AgentStatus
from openclaw.domain.agents.ports import LLMPort
from openclaw.domain.agents.state import ObservationKind, RunState
from openclaw.domain.memory.ports import MemoryRepository
from openclaw.domain.messages.model import AgentMessage
from openclaw.domain.shared.errors import AgentError, ApprovalError, OpenClawError, SkillError
from openclaw.domain.skills.ports import SkillRepository
from openclaw.domain.tasks.approval import Approval, ApprovalStatus
from openclaw.domain.tasks.execution import EventType, Execution, ExecutionEvent, ExecutionStatus
from openclaw.domain.tasks.ports import ApprovalPort, EventSink
from openclaw.domain.tools.policy import PolicyEngine, PolicyOutcome
from openclaw.domain.tools.ports import ToolPort


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    # None = unbounded, exactly as the loop in REQUIREMENTS section 11. Opt-in safety guard.
    max_steps: int | None = None


class _Recorder:
    """Per-run event log, so one runtime instance can serve concurrent runs."""

    def __init__(
        self,
        execution_id: str,
        task_id: str,
        agent_id: str,
        sink: EventSink | None,
        clock: Callable[[], datetime],
    ) -> None:
        self.execution_id, self._task_id, self._agent_id = execution_id, task_id, agent_id
        self._sink, self._clock = sink, clock
        self.events: list[ExecutionEvent] = []

    async def emit(self, type_: EventType, **data: Any) -> None:
        event = ExecutionEvent(
            self.execution_id, self._task_id, self._agent_id, type_, self._clock(), data
        )
        self.events.append(event)
        if self._sink is not None:
            await self._sink.record(event)


class AgentRuntime:
    def __init__(
        self,
        *,
        llm: LLMPort,
        tools: ToolPort,
        skills: SkillRepository,
        memory: MemoryRepository,
        approvals: ApprovalPort,
        sink: EventSink | None = None,
        policy: PolicyEngine | None = None,
        retry: RetryPolicy | None = None,
        config: RuntimeConfig | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config or RuntimeConfig()
        if self._config.max_steps is not None and self._config.max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        self._llm = llm
        self._approvals = approvals
        self._sink = sink
        self._policy = policy or PolicyEngine()
        self._clock = clock
        self._skill_resolver = SkillResolver(skills)
        self._tool_resolver = ToolResolver(tools)
        self._executor = ActionExecutor(tools, retry)
        self._memory = MemoryManager(memory)
        self._context_builder = ContextBuilder()

    async def run(
        self, agent: Agent, message: AgentMessage, *, execution_id: str | None = None
    ) -> Execution:
        if agent.status is not AgentStatus.ACTIVE:
            raise AgentError(f"Agent '{agent.id}' is not active")
        execution_id = execution_id or uuid.uuid4().hex
        parent_id = current_execution()  # set when this run is a delegation of another run
        with execution_scope(execution_id, message.task_id):  # a delegation or script sees this run
            return await self._execute(agent, message, execution_id, parent_id)

    async def _execute(
        self, agent: Agent, message: AgentMessage, execution_id: str, parent_id: str | None
    ) -> Execution:
        rec = _Recorder(execution_id, message.task_id, agent.id, self._sink, self._clock)
        state = RunState(message.content)
        await rec.emit(
            EventType.TASK_STARTED,
            sender=message.sender,
            request=message.content,
            parent_execution_id=parent_id,
        )
        await rec.emit(EventType.AGENT_SELECTED)

        try:
            memory = await self._memory.load(agent.id)
            skills = await self._skill_resolver.available(agent)
            tools = self._tool_resolver.visible_specs(agent)

            while not state.finished:  # the ReAct loop
                max_steps = self._config.max_steps
                if max_steps is not None and state.steps >= max_steps:
                    await self._conclude(agent, memory, skills, tools, state, rec, max_steps)
                    break
                context = self._observe(agent, memory, skills, tools, state)
                decision = await self._reason(context)
                state.steps += 1
                await rec.emit(EventType.LLM_DECISION, **_describe(decision))
                await self._act(agent, decision, state, rec)
        except OpenClawError as exc:
            await rec.emit(EventType.ERROR, error_type=type(exc).__name__, error=str(exc))
            state.fail(str(exc))

        assert state.status is not None
        await rec.emit(
            EventType.FINAL_RESULT,
            status=state.status,
            answer=state.answer,
            error=state.error,
            steps=state.steps,
        )
        return Execution(
            id=execution_id,
            task_id=message.task_id,
            agent_id=agent.id,
            status=state.status,
            answer=state.answer,  # only the final answer; raw LLM reasoning is never exposed
            error=state.error,
            steps=state.steps,
            events=tuple(rec.events),
        )

    # -- step limit -----------------------------------------------------------------------
    async def _conclude(
        self, agent, memory, skills, tools, state: RunState, rec: _Recorder, max_steps: int
    ) -> None:
        """One last step where the only possible decision is `finish`, so that the work done so
        far is not lost. The run stays MAX_STEPS_EXCEEDED (the answer is partial); if the model
        cannot or will not conclude, there is no answer, as before."""
        state.fail(f"no final answer after {max_steps} steps", ExecutionStatus.MAX_STEPS_EXCEEDED)
        context = replace(self._observe(agent, memory, skills, tools, state), final_step=True)
        try:
            decision = await self._reason(context)
        except OpenClawError as exc:
            await rec.emit(EventType.ERROR, error_type=type(exc).__name__, error=str(exc))
            return
        await rec.emit(EventType.LLM_DECISION, **_describe(decision), final_step=True)
        if isinstance(decision, Finish):
            state.answer = decision.answer
            state.error = f"step limit of {max_steps} reached: the answer is based on partial work"

    # -- observe --------------------------------------------------------------------------
    def _observe(self, agent, memory, skills, tools, state) -> DecisionContext:
        return self._context_builder.build(
            agent=agent, memory=memory, skills=skills, tools=tools, state=state
        )

    # -- reason ---------------------------------------------------------------------------
    async def _reason(self, context: DecisionContext) -> Decision:
        return await self._llm.decide(context)

    # -- act ------------------------------------------------------------------------------
    async def _act(self, agent: Agent, decision: Decision, state: RunState, rec: _Recorder) -> None:
        if isinstance(decision, Finish):
            state.finish(decision.answer)
        elif isinstance(decision, UseSkill):
            await self._load_skill(agent, decision, state, rec)
        else:
            await self._call_tool(agent, decision, state, rec)

    async def _load_skill(
        self, agent: Agent, decision: UseSkill, state: RunState, rec: _Recorder
    ) -> None:
        try:
            skill = await self._skill_resolver.load(agent, decision.skill_id)
        except SkillError as exc:
            await rec.emit(EventType.ERROR, error_type="SkillError", error=str(exc))
            state.add_observation(ObservationKind.ERROR, str(exc), decision.skill_id)
            return
        state.load_skill(skill)
        await rec.emit(EventType.SKILL_LOADED, skill=decision.skill_id)
        state.add_observation(
            ObservationKind.SKILL_LOADED, f"Skill '{decision.skill_id}' loaded.", decision.skill_id
        )

    async def _call_tool(
        self, agent: Agent, decision: CallTool, state: RunState, rec: _Recorder
    ) -> None:
        call = decision.call
        spec = self._tool_resolver.spec_for(call.name)
        verdict = self._policy.evaluate(agent.tool_permissions, spec, call)

        if verdict.outcome is PolicyOutcome.DENY:
            await rec.emit(EventType.POLICY_DENIED, tool=call.name, reason=verdict.reason)
            state.add_observation(
                ObservationKind.POLICY_DENIED,
                f"Tool '{call.name}' was denied: {verdict.reason}.",
                call.name,
            )
            return

        assert spec is not None
        signature = _signature(call)
        if signature in state.failed_calls:
            # The same call already failed in this run: repeating it (and asking the human to
            # approve it again) cannot help, so the model is told instead of the tool being run.
            await rec.emit(EventType.DUPLICATE_CALL, tool=call.name, input=dict(call.arguments))
            state.add_observation(
                ObservationKind.TOOL_ERROR,
                f"Tool '{call.name}' was not run: this exact call already failed "
                f"({state.failed_calls[signature]}). Do not repeat it: change the arguments, "
                "use another source, or answer with what you already have.",
                call.name,
            )
            return
        if verdict.outcome is PolicyOutcome.REQUIRE_APPROVAL:
            approval = await self._ask_human(
                Approval(rec.execution_id, agent.id, call, spec.risk_level, verdict.reason), rec
            )
            if approval.status is not ApprovalStatus.APPROVED:  # anything but APPROVED: no run
                suffix = f" ({approval.comment})" if approval.comment else ""
                state.add_observation(
                    ObservationKind.APPROVAL_REJECTED,
                    f"The human rejected the call to '{call.name}'{suffix}. It was not executed.",
                    call.name,
                )
                return

        skills = list(state.loaded_skills)
        await rec.emit(
            EventType.TOOL_CALLED, tool=call.name, skills=skills, input=dict(call.arguments)
        )
        started = time.perf_counter()
        try:
            output = await self._executor.execute(call, spec, agent.id)
        except OpenClawError as exc:
            await rec.emit(
                EventType.TOOL_RESULT,
                tool=call.name,
                skills=skills,
                status="error",
                error=str(exc),
                duration_ms=_ms(started),
            )
            state.failed_calls[signature] = str(exc)
            state.add_observation(
                ObservationKind.TOOL_ERROR, f"Tool '{call.name}' failed: {exc}", call.name
            )
            return

        text = _stringify(output)
        await rec.emit(
            EventType.TOOL_RESULT,
            tool=call.name,
            skills=skills,
            status="success",
            duration_ms=_ms(started),
            output=text,
        )
        state.add_observation(ObservationKind.TOOL_RESULT, text, call.name)

    async def _ask_human(self, approval: Approval, rec: _Recorder) -> Approval:
        call = approval.call
        await rec.emit(
            EventType.APPROVAL_REQUESTED,
            tool=call.name,
            input=dict(call.arguments),
            reason=approval.reason,
        )
        try:
            resolved = await self._approvals.request(approval)
        except ApprovalError as exc:  # never execute without a positive answer
            resolved = approval.resolve(False, str(exc))
        await rec.emit(
            EventType.APPROVAL_RESOLVED,
            tool=call.name,
            approval=resolved.status,
            comment=resolved.comment,
        )
        return resolved


def _describe(decision: Decision) -> dict[str, Any]:
    if isinstance(decision, Finish):
        return {"decision": "finish"}
    if isinstance(decision, UseSkill):
        return {"decision": "use_skill", "skill": decision.skill_id}
    return {
        "decision": "call_tool",
        "tool": decision.call.name,
        "input": dict(decision.call.arguments),
    }


def _signature(call: Any) -> str:
    return f"{call.name}:{json.dumps(dict(call.arguments), sort_keys=True, default=str)}"


def _stringify(output: Any) -> str:
    return (
        output if isinstance(output, str) else json.dumps(output, default=str, ensure_ascii=False)
    )


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
