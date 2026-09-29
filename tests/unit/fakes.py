"""Test doubles for the runtime ports."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from openclaw.application.agents.runtime import AgentRuntime, RuntimeConfig
from openclaw.domain.agents.decision import Decision
from openclaw.domain.agents.model import Agent, AgentProfile
from openclaw.domain.shared.errors import SkillError
from openclaw.domain.skills.model import Skill, SkillMetadata
from openclaw.domain.tasks.approval import Approval
from openclaw.domain.tasks.execution import ExecutionEvent
from openclaw.domain.tools.model import RiskLevel, ToolCall, ToolSpec
from openclaw.domain.tools.permissions import ToolPermissions


class ScriptedLLM:
    def __init__(self, script: Sequence[Decision | Exception], repeat_last: bool = False) -> None:
        self.script = list(script)
        self.repeat_last = repeat_last
        self.contexts: list[Any] = []

    async def decide(self, context):
        self.contexts.append(context)
        if not self.script:
            raise AssertionError("LLM script exhausted")
        item = self.script[0] if self.repeat_last and len(self.script) == 1 else self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeTools:
    def __init__(self) -> None:
        self.specs: dict[str, ToolSpec] = {}
        self.handlers: dict[str, Callable[[ToolCall], Any]] = {}
        self.calls: list[ToolCall] = []
        self.callers: list[str] = []

    def add(self, name, risk=RiskLevel.READ, handler=None, required=()):
        self.specs[name] = ToolSpec(
            name, f"{name} tool", {"required": list(required)}, risk_level=risk
        )
        self.handlers[name] = handler or (lambda call: {"ok": True})

    def get_spec(self, name):
        return self.specs.get(name)

    async def execute(self, call, caller=None):
        self.calls.append(call)
        self.callers.append(caller)
        return self.handlers[call.name](call)


class FakeSkills:
    def __init__(self, skills: dict[str, str] | None = None) -> None:
        self.skills = skills or {}
        self.loaded: list[str] = []

    async def discover(self):
        return await self.list_metadata(list(self.skills))

    async def list_metadata(self, skill_ids):
        return [SkillMetadata(i, i.split("/")[-1], f"desc of {i}") for i in skill_ids]

    async def load(self, skill_id):
        if skill_id not in self.skills:
            raise SkillError(f"unknown skill {skill_id}")
        self.loaded.append(skill_id)
        meta = SkillMetadata(skill_id, skill_id.split("/")[-1], "d")
        return Skill(meta, self.skills[skill_id])


class FakeMemory:
    def __init__(self, text: str = "# Memory\n", user: str = "# User\n") -> None:
        self.text, self.user = text, user

    async def read(self, reference):
        return self.user if reference.layer.value == "user" else self.text


class FakeApprovals:
    def __init__(
        self, approved: bool = True, error: Exception | None = None, leave_pending: bool = False
    ) -> None:
        self.approved, self.error, self.leave_pending = approved, error, leave_pending
        self.requests: list[Approval] = []

    async def request(self, approval):
        self.requests.append(approval)
        if self.error:
            raise self.error
        return approval if self.leave_pending else approval.resolve(self.approved, "because")


class ListSink:
    def __init__(self) -> None:
        self.events: list[ExecutionEvent] = []

    async def record(self, event):
        self.events.append(event)


def make_agent(allowed=(), approval_required=(), skills=()) -> Agent:
    return Agent(
        id="test",
        profile=AgentProfile(soul="# Soul", instructions="# Instructions"),
        skills=tuple(skills),
        tool_permissions=ToolPermissions.of(allowed, approval_required),
    )


class Harness:
    def __init__(
        self, script, *, repeat_last=False, approvals=None, config=None, skills=None, retry=None
    ):
        self.llm = ScriptedLLM(script, repeat_last)
        self.tools = FakeTools()
        self.skills = FakeSkills(skills)
        self.memory = FakeMemory()
        self.approvals = approvals or FakeApprovals(approved=False)
        self.sink = ListSink()
        self.runtime = AgentRuntime(
            llm=self.llm,
            tools=self.tools,
            skills=self.skills,
            memory=self.memory,
            approvals=self.approvals,
            retry=retry,
            sink=self.sink,
            config=config or RuntimeConfig(),
        )
