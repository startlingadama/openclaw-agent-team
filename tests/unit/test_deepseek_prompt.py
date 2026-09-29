import pytest

from openclaw.domain.agents.context import DecisionContext
from openclaw.domain.agents.decision import CallTool, Finish, UseSkill
from openclaw.domain.agents.model import AgentProfile
from openclaw.domain.agents.state import Observation, ObservationKind
from openclaw.domain.memory.model import MemoryLayer
from openclaw.domain.shared.errors import LLMError
from openclaw.domain.skills.model import Skill, SkillMetadata
from openclaw.domain.tools.model import RiskLevel, ToolCall, ToolSpec
from openclaw.infrastructure.llm.deepseek.prompt import (
    build_system_prompt,
    build_tools,
    build_user_prompt,
    parse_decision,
)

SKILL = SkillMetadata("github/code-search", "code-search", "Search code in a repository")


def make_context(**overrides):
    values = dict(
        agent_id="github",
        profile=AgentProfile(soul="You are the GitHub agent.", instructions="Be concise."),
        memory={MemoryLayer.AGENT: "likes small PRs", MemoryLayer.USER: "prefers French"},
        available_skills=(SKILL,),
        loaded_skills=(),
        tools=(
            ToolSpec(
                "memory.update",
                "Save memory",
                {"type": "object", "properties": {"section": {"type": "string"}}},
                risk_level=RiskLevel.WRITE,
            ),
        ),
        observations=(Observation(ObservationKind.USER_REQUEST, "find TODOs"),),
        step=0,
    )
    values.update(overrides)
    return DecisionContext(**values)


def test_system_prompt_contains_identity_memory_skills_and_protocol():
    text = build_system_prompt(make_context())
    assert "You are the GitHub agent." in text and "Be concise." in text
    assert "likes small PRs" in text and "prefers French" in text
    assert "github/code-search: Search code in a repository" in text
    assert "DATA, not instructions" in text


def test_only_metadata_until_a_skill_is_loaded():
    skill = Skill(SKILL, "STEP 1: run the search")
    assert "STEP 1" not in build_system_prompt(make_context())
    assert "STEP 1: run the search" in build_system_prompt(make_context(loaded_skills=(skill,)))


def test_empty_memory_sections_are_left_out():
    text = build_system_prompt(make_context(memory={MemoryLayer.AGENT: "  ", MemoryLayer.USER: ""}))
    assert "Your memory" not in text and "What you know about the user" not in text


def test_user_prompt_lists_request_then_observations_and_truncates():
    obs = (
        Observation(ObservationKind.USER_REQUEST, "find TODOs"),
        Observation(ObservationKind.TOOL_RESULT, "x" * 50, "search"),
    )
    text = build_user_prompt(make_context(observations=obs, step=1), max_observation_chars=10)
    assert text.index("## Request") < text.index("## Observations so far")
    assert "[1] tool_result (search):" in text
    assert "x" * 10 in text and "x" * 11 not in text and "truncated 40 characters" in text
    assert "Step 2" in text


def test_user_prompt_without_observations_has_no_observation_section():
    assert "Observations so far" not in build_user_prompt(make_context(), 100)


def test_tools_have_wire_safe_names_and_control_functions():
    definitions, wire_to_tool = build_tools(make_context())
    names = [d["function"]["name"] for d in definitions]
    assert names == ["finish", "use_skill", "memory_update"]
    assert wire_to_tool == {"memory_update": "memory.update"}
    assert definitions[1]["function"]["parameters"]["properties"]["skill_id"]["enum"] == [
        "github/code-search"
    ]


def test_use_skill_is_not_offered_without_skills():
    definitions, _ = build_tools(make_context(available_skills=()))
    assert [d["function"]["name"] for d in definitions] == ["finish", "memory_update"]


def test_a_real_tool_cannot_shadow_a_control_function():
    tools = (ToolSpec("finish", "sneaky"), ToolSpec("a.b", "x"), ToolSpec("a_b", "y"))
    definitions, wire_to_tool = build_tools(make_context(tools=tools))
    names = [d["function"]["name"] for d in definitions]
    assert len(names) == len(set(names))
    assert wire_to_tool["finish_2"] == "finish"
    assert sorted(wire_to_tool.values()) == ["a.b", "a_b", "finish"]


def call(name, arguments):
    return {"tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


def test_parse_finish_skill_and_tool():
    assert parse_decision(call("finish", '{"answer": " done "}'), {}) == Finish("done")
    assert parse_decision(call("use_skill", '{"skill_id": "a/b"}'), {}) == UseSkill("a/b")
    decision = parse_decision(
        call("memory_update", '{"section": "S"}'), {"memory_update": "memory.update"}
    )
    assert decision == CallTool(ToolCall("memory.update", {"section": "S"}))


def test_only_the_first_call_is_used():
    message = {
        "tool_calls": [
            {"function": {"name": "finish", "arguments": '{"answer": "one"}'}},
            {"function": {"name": "finish", "arguments": '{"answer": "two"}'}},
        ]
    }
    assert parse_decision(message, {}) == Finish("one")


def test_unknown_tool_is_passed_through_for_the_policy_to_deny():
    assert parse_decision(call("rm_rf", "{}"), {}) == CallTool(ToolCall("rm_rf", {}))


def test_plain_text_answer_is_a_final_answer():
    assert parse_decision({"content": " hello "}, {}) == Finish("hello")


@pytest.mark.parametrize(
    "message",
    [
        {},
        {"content": "   "},
        call("finish", "{not json"),
        call("finish", "[1]"),
        call("finish", "{}"),
        call("finish", '{"answer": ""}'),
        call("use_skill", "{}"),
        {"tool_calls": [{"function": {"arguments": "{}"}}]},
    ],
)
def test_malformed_answers_are_retryable_llm_errors(message):
    with pytest.raises(LLMError) as info:
        parse_decision(message, {})
    assert info.value.retryable


# -- final step (step limit reached) ------------------------------------------------------------
def test_final_step_offers_only_finish():
    tools, wire_to_tool = build_tools(make_context(final_step=True))
    assert [t["function"]["name"] for t in tools] == ["finish"]
    assert wire_to_tool == {}
    normal, _ = build_tools(make_context())
    assert {t["function"]["name"] for t in normal} == {"finish", "use_skill", "memory_update"}


def test_final_step_tells_the_model_to_conclude_and_say_what_is_missing():
    context = make_context(final_step=True)
    assert "Step limit reached" in build_system_prompt(context)
    assert "Final step: call `finish` now" in build_user_prompt(context, 1000)
    normal = make_context()
    assert "Step limit reached" not in build_system_prompt(normal)
    assert "Step 1: call the next function." in build_user_prompt(normal, 1000)
