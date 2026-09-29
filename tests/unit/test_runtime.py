import asyncio

import pytest

from openclaw.application.agents.action_executor import RetryPolicy
from openclaw.application.agents.runtime import RuntimeConfig
from openclaw.domain.agents.decision import CallTool, Finish, UseSkill
from openclaw.domain.agents.state import ObservationKind
from openclaw.domain.memory.model import MemoryLayer
from openclaw.domain.messages.model import AgentMessage
from openclaw.domain.shared.errors import (
    AgentError,
    ApprovalError,
    AuthenticationError,
    LLMError,
    ToolError,
)
from openclaw.domain.tasks.execution import EventType as E
from openclaw.domain.tasks.execution import ExecutionStatus
from openclaw.domain.tools.model import RiskLevel, ToolCall
from tests.unit.fakes import FakeApprovals, Harness, make_agent


def run(h, agent, request="do it"):
    return asyncio.run(h.runtime.run(agent, AgentMessage("user", agent.id, "task-1", request)))


def types(result):
    return [e.type for e in result.events]


def call(name="search", **args):
    return CallTool(ToolCall(name, args))


def test_finishes_immediately():
    h = Harness([Finish("hi")])
    r = run(h, make_agent())
    assert (r.status, r.answer, r.steps) == (ExecutionStatus.COMPLETED, "hi", 1)
    assert types(r) == [E.TASK_STARTED, E.AGENT_SELECTED, E.LLM_DECISION, E.FINAL_RESULT]
    assert {e.task_id for e in r.events} == {"task-1"}
    assert h.sink.events == list(r.events)


def test_read_tool_result_becomes_observation():
    h = Harness([call(q="x"), Finish("ok")])
    h.tools.add("search", handler=lambda c: {"items": [1]})
    r = run(h, make_agent(allowed=["search"]))
    assert r.status is ExecutionStatus.COMPLETED
    assert types(r) == [
        E.TASK_STARTED,
        E.AGENT_SELECTED,
        E.LLM_DECISION,
        E.TOOL_CALLED,
        E.TOOL_RESULT,
        E.LLM_DECISION,
        E.FINAL_RESULT,
    ]
    last = h.llm.contexts[-1].observations[-1]
    assert last.kind is ObservationKind.TOOL_RESULT and '"items"' in last.content


def test_unpermitted_tool_is_never_executed():
    h = Harness([call(), Finish("ok")])
    h.tools.add("search")
    r = run(h, make_agent())
    assert h.tools.calls == []
    assert E.POLICY_DENIED in types(r)
    assert h.llm.contexts[-1].observations[-1].kind is ObservationKind.POLICY_DENIED


def test_llm_only_sees_permitted_tools():
    h = Harness([Finish("ok")])
    h.tools.add("search")
    h.tools.add("secret")
    run(h, make_agent(allowed=["search"]))
    assert [t.name for t in h.llm.contexts[0].tools] == ["search"]


def test_approval_required_tool_runs_when_approved():
    approvals = FakeApprovals(approved=True)
    h = Harness([call("send"), Finish("sent")], approvals=approvals)
    h.tools.add("send", RiskLevel.WRITE)
    r = run(h, make_agent(approval_required=["send"]))
    assert len(approvals.requests) == 1 and len(h.tools.calls) == 1
    assert E.APPROVAL_REQUESTED in types(r) and E.APPROVAL_RESOLVED in types(r)


def test_rejected_approval_does_not_execute():
    h = Harness([call("send"), Finish("ok")], approvals=FakeApprovals(approved=False))
    h.tools.add("send", RiskLevel.WRITE)
    r = run(h, make_agent(approval_required=["send"]))
    assert h.tools.calls == []
    assert h.llm.contexts[-1].observations[-1].kind is ObservationKind.APPROVAL_REJECTED
    assert r.status is ExecutionStatus.COMPLETED


def test_destructive_tool_needs_approval_even_if_only_allowed():
    approvals = FakeApprovals(approved=False)
    h = Harness([call("rm"), Finish("ok")], approvals=approvals)
    h.tools.add("rm", RiskLevel.DESTRUCTIVE)
    run(h, make_agent(allowed=["rm"]))
    assert len(approvals.requests) == 1 and h.tools.calls == []


def test_approval_error_fails_closed():
    h = Harness([call("send"), Finish("ok")], approvals=FakeApprovals(error=ApprovalError("down")))
    h.tools.add("send", RiskLevel.WRITE)
    run(h, make_agent(approval_required=["send"]))
    assert h.tools.calls == []


def test_read_tools_are_retried_on_retryable_errors():
    attempts = []

    def flaky(c):
        attempts.append(1)
        if len(attempts) < 3:
            raise ToolError("boom", retryable=True)
        return "fine"

    h = Harness([call(), Finish("ok")], retry=RetryPolicy(max_attempts=3))
    h.tools.add("search", handler=flaky)
    r = run(h, make_agent(allowed=["search"]))
    assert len(attempts) == 3 and r.status is ExecutionStatus.COMPLETED


def test_write_tools_are_never_retried():
    attempts = []

    def flaky(c):
        attempts.append(1)
        raise ToolError("boom", retryable=True)

    h = Harness([call("write"), Finish("ok")])
    h.tools.add("write", RiskLevel.WRITE, handler=flaky)
    r = run(h, make_agent(allowed=["write"]))
    assert len(attempts) == 1
    assert h.llm.contexts[-1].observations[-1].kind is ObservationKind.TOOL_ERROR
    assert r.status is ExecutionStatus.COMPLETED


def test_invalid_arguments_do_not_reach_the_tool():
    h = Harness([call(), Finish("ok")])
    h.tools.add("search", required=["q"])
    run(h, make_agent(allowed=["search"]))
    assert h.tools.calls == []
    assert h.llm.contexts[-1].observations[-1].kind is ObservationKind.TOOL_ERROR


def test_max_steps_is_explicit():
    h = Harness([call()], repeat_last=True, config=RuntimeConfig(max_steps=3))
    h.tools.add("search")
    r = run(h, make_agent(allowed=["search"]))
    assert r.status is ExecutionStatus.MAX_STEPS_EXCEEDED and r.steps == 3 and r.error


def test_llm_error_fails_the_run():
    h = Harness([LLMError("api down")])
    r = run(h, make_agent())
    assert r.status is ExecutionStatus.FAILED and "api down" in r.error
    assert E.ERROR in types(r) and types(r)[-1] is E.FINAL_RESULT


def test_skill_progressive_disclosure():
    h = Harness(
        [UseSkill("github/repo"), Finish("done")], skills={"github/repo": "FULL INSTRUCTIONS"}
    )
    r = run(h, make_agent(skills=["github/repo"]))
    first, second = h.llm.contexts
    assert [s.id for s in first.available_skills] == ["github/repo"]
    assert first.loaded_skills == ()  # only name + description at first
    assert second.loaded_skills[0].instructions == "FULL INSTRUCTIONS"
    assert E.SKILL_LOADED in types(r)


def test_unassigned_skill_cannot_be_loaded():
    h = Harness([UseSkill("other/skill"), Finish("ok")], skills={"other/skill": "X"})
    run(h, make_agent(skills=["github/repo"]))
    assert h.skills.loaded == []
    assert h.llm.contexts[-1].observations[-1].kind is ObservationKind.ERROR


def test_memory_is_loaded_into_context():
    h = Harness([Finish("ok")])
    h.memory.text = "# Memory\nlikes python"
    h.memory.user = "# User\nprefers French"
    run(h, make_agent())
    memory = h.llm.contexts[0].memory
    assert "likes python" in memory[MemoryLayer.AGENT]
    assert "prefers French" in memory[MemoryLayer.USER]


def test_disabled_agent_is_refused():
    from dataclasses import replace

    from openclaw.domain.agents.model import AgentStatus

    h = Harness([Finish("ok")])
    with pytest.raises(AgentError):
        run(h, replace(make_agent(), status=AgentStatus.DISABLED))


def test_no_retry_unless_a_policy_asks_for_it():
    attempts = []

    def flaky(c):
        attempts.append(1)
        raise ToolError("boom", retryable=True)

    h = Harness([call(), Finish("ok")])
    h.tools.add("search", handler=flaky)
    run(h, make_agent(allowed=["search"]))
    assert len(attempts) == 1


def test_pending_approval_is_not_an_approval():
    h = Harness([call("send"), Finish("ok")], approvals=FakeApprovals(leave_pending=True))
    h.tools.add("send", RiskLevel.WRITE)
    run(h, make_agent(approval_required=["send"]))
    assert h.tools.calls == []


def test_authentication_error_from_a_tool_is_reported_as_a_tool_error():
    def denied(c):
        raise AuthenticationError("bad token")

    h = Harness([call(), Finish("ok")])
    h.tools.add("search", handler=denied)
    run(h, make_agent(allowed=["search"]))
    assert h.llm.contexts[-1].observations[-1].kind is ObservationKind.TOOL_ERROR


def test_trace_records_agent_skill_tool_input_output_duration_status():
    h = Harness([UseSkill("github/repo"), call(q="x"), Finish("ok")], skills={"github/repo": "I"})
    h.tools.add("search", handler=lambda c: "A" * 2000)
    r = run(h, make_agent(allowed=["search"], skills=["github/repo"]))
    done = next(e for e in r.events if e.type is E.TOOL_RESULT)
    assert done.agent_id == "test" and done.data["tool"] == "search"
    assert done.data["skills"] == ["github/repo"] and done.data["status"] == "success"
    assert done.data["output"] == "A" * 2000 and "duration_ms" in done.data
    called = next(e for e in r.events if e.type is E.TOOL_CALLED)
    assert called.data["input"] == {"q": "x"}


def test_raw_answer_is_the_only_llm_output_returned():
    h = Harness([Finish("final")])
    r = run(h, make_agent())
    assert r.answer == "final" and not hasattr(r, "reasoning")


def test_tools_receive_the_calling_agent_from_the_runtime():
    h = Harness([call(), Finish("ok")])
    h.tools.add("search")
    run(h, make_agent(allowed=["search"]))
    assert h.tools.callers == ["test"]


# -- an identical call that already failed is not run again ----------------------------------
def test_an_identical_failed_call_is_not_run_again():
    def refuse(c):
        raise ToolError("the site refuses automated access (HTTP 403)")

    same = call("open", url="https://x.test/a")
    other = call("open", url="https://x.test/b")
    h = Harness([same, same, other, Finish("ok")])
    h.tools.add("open", handler=refuse)
    r = run(h, make_agent(allowed=["open"]))

    assert [c.arguments["url"] for c in h.tools.calls] == ["https://x.test/a", "https://x.test/b"]
    assert E.DUPLICATE_CALL in types(r)
    # the model is told why, and what to do instead
    seen = [o for o in h.llm.contexts[2].observations if o.kind is ObservationKind.TOOL_ERROR]
    assert "already failed" in seen[-1].content and "403" in seen[-1].content
    assert r.status is ExecutionStatus.COMPLETED


def test_argument_order_does_not_hide_a_duplicate():
    def refuse(c):
        raise ToolError("nope")

    h = Harness([call("open", a=1, b=2), call("open", b=2, a=1), Finish("ok")])
    h.tools.add("open", handler=refuse)
    run(h, make_agent(allowed=["open"]))
    assert len(h.tools.calls) == 1


def test_a_successful_call_can_be_repeated():
    h = Harness([call("open", u="x"), call("open", u="x"), Finish("ok")])
    h.tools.add("open")
    r = run(h, make_agent(allowed=["open"]))
    assert len(h.tools.calls) == 2 and E.DUPLICATE_CALL not in types(r)


def test_a_duplicate_does_not_ask_for_approval_again():
    def refuse(c):
        raise ToolError("nope")

    approvals = FakeApprovals(approved=True)
    h = Harness([call("post", t="x"), call("post", t="x"), Finish("ok")], approvals=approvals)
    h.tools.add("post", RiskLevel.EXTERNAL_COMMUNICATION, handler=refuse)
    run(h, make_agent(allowed=["post"]))
    assert len(approvals.requests) == 1 and len(h.tools.calls) == 1


# -- the step limit gives the model one last step to conclude ---------------------------------
def test_step_limit_lets_the_model_conclude_with_what_it_has():
    h = Harness([call(), call(), Finish("partial answer")], config=RuntimeConfig(max_steps=2))
    h.tools.add("search")
    r = run(h, make_agent(allowed=["search"]))

    assert r.status is ExecutionStatus.MAX_STEPS_EXCEEDED  # still flagged: the work is partial
    assert r.answer == "partial answer"
    assert "step limit of 2" in r.error and r.steps == 2
    assert len(h.tools.calls) == 2  # the forced step ran no tool
    assert [c.final_step for c in h.llm.contexts] == [False, False, True]
    forced = [e for e in r.events if e.type is E.LLM_DECISION][-1]
    assert forced.data["final_step"] is True and forced.data["decision"] == "finish"
    assert types(r)[-1] is E.FINAL_RESULT


def test_step_limit_without_a_conclusion_gives_no_answer():
    # the model ignores the instruction and asks for another tool: it is not executed
    h = Harness([call(), call(), call()], config=RuntimeConfig(max_steps=2))
    h.tools.add("search")
    r = run(h, make_agent(allowed=["search"]))
    assert r.status is ExecutionStatus.MAX_STEPS_EXCEEDED and r.answer is None
    assert "no final answer after 2 steps" in r.error and len(h.tools.calls) == 2


def test_step_limit_survives_an_llm_error_on_the_last_step():
    h = Harness([call(), LLMError("api down")], config=RuntimeConfig(max_steps=1))
    h.tools.add("search")
    r = run(h, make_agent(allowed=["search"]))
    assert r.status is ExecutionStatus.MAX_STEPS_EXCEEDED and r.answer is None
    assert E.ERROR in types(r)


def test_no_forced_step_when_the_run_finishes_in_time():
    h = Harness([call(), Finish("done")], config=RuntimeConfig(max_steps=2))
    h.tools.add("search")
    r = run(h, make_agent(allowed=["search"]))
    assert r.status is ExecutionStatus.COMPLETED and len(h.llm.contexts) == 2
    assert not any(c.final_step for c in h.llm.contexts)
