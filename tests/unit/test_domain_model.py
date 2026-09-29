import dataclasses

import pytest

from openclaw.domain.agents.model import AgentId
from openclaw.domain.memory.model import MemoryLayer
from openclaw.domain.messages.model import AgentMessage
from openclaw.domain.shared.errors import ValidationError
from openclaw.domain.skills.model import SkillId
from openclaw.domain.tasks.task import Task
from openclaw.domain.teams.team import Team, TeamPattern


def test_task_carries_the_section_18_fields():
    names = [f.name for f in dataclasses.fields(Task)]
    assert names == [
        "task_id", "from_agent", "to_agent", "objective", "context",
        "constraints", "required_skills", "required_tools", "deadline", "approval_policy",
    ]  # fmt: skip


def test_task_example_from_the_spec():
    t = Task(
        "research-001",
        AgentId("ceo"),
        AgentId("research"),
        "Analyze competitors.",
        required_skills=(SkillId("competitive-research"),),
    )
    assert t.to_agent == "research" and t.required_skills == ("competitive-research",)


def test_supervisor_team_requires_a_supervisor():
    with pytest.raises(ValidationError):
        Team("t", TeamPattern.SUPERVISOR, members=(AgentId("github"),))
    Team("t", TeamPattern.SUPERVISOR, (AgentId("github"),), supervisor=AgentId("ceo"))
    Team("t", TeamPattern.PEER_TO_PEER, (AgentId("a"), AgentId("b")))


def test_memory_layers_match_section_14():
    assert {m.value for m in MemoryLayer} == {"working", "session", "agent", "user", "shared_team"}


def test_agent_message_matches_section_11():
    m = AgentMessage("ceo", "research", "task-123", "Research competitors")
    assert (m.sender, m.recipient, m.task_id, m.metadata) == (
        "ceo", "research", "task-123", {}
    )  # fmt: skip
