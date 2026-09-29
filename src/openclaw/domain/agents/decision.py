"""What the LLM decides at each step of the ReAct loop."""

from __future__ import annotations

from dataclasses import dataclass

from openclaw.domain.tools.model import ToolCall


@dataclass(frozen=True, slots=True)
class Finish:
    answer: str


@dataclass(frozen=True, slots=True)
class UseSkill:
    skill_id: str


@dataclass(frozen=True, slots=True)
class CallTool:
    call: ToolCall


Decision = Finish | UseSkill | CallTool
