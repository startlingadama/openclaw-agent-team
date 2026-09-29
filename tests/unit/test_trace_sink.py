"""FileTraceSink and CompositeEventSink: files written from events alone, no runtime."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from openclaw.domain.tasks.execution import EventType as E
from openclaw.domain.tasks.execution import ExecutionEvent
from openclaw.infrastructure.observability.composite import CompositeEventSink
from openclaw.infrastructure.observability.trace import FileTraceSink

T0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)


def ev(execution, agent, type_, seconds=0, task=None, **data):
    return ExecutionEvent(
        execution, task or f"task-{execution}", agent, type_, T0 + timedelta(seconds=seconds), data
    )


def feed(sink, *events):
    async def go():
        for event in events:
            await sink.record(event)

    asyncio.run(go())


def only_dir(root):
    (directory,) = [d for d in root.iterdir() if d.is_dir()]
    return directory


def lines(directory):
    return [json.loads(x) for x in (directory / "events.jsonl").read_text().splitlines()]


def simple_run(sink, answer="the answer", status="completed", error=None):
    feed(
        sink,
        ev(
            "e1",
            "ceo",
            E.TASK_STARTED,
            sender="user",
            request="Compare A and B",
            parent_execution_id=None,
        ),
        ev("e1", "ceo", E.LLM_DECISION, 1, decision="finish"),
        ev("e1", "ceo", E.FINAL_RESULT, 2, status=status, answer=answer, error=error, steps=1),
    )


def test_one_directory_per_execution_with_the_three_files(tmp_path):
    simple_run(FileTraceSink(tmp_path / "executions"))
    directory = only_dir(tmp_path / "executions")
    assert directory.name == "20260928T100000_ceo_e1"
    assert sorted(p.name for p in directory.iterdir()) == [
        "events.jsonl",
        "execution.json",
        "result.md",
    ]

    events = lines(directory)
    assert [e["type"] for e in events] == ["task_started", "llm_decision", "final_result"]
    assert events[0]["data"]["request"] == "Compare A and B"
    assert events[0]["ts"] == "2026-09-28T10:00:00+00:00"

    summary = json.loads((directory / "execution.json").read_text())
    assert summary["status"] == "completed" and summary["steps"] == 1
    assert (summary["agent_id"], summary["sender"]) == ("ceo", "user")
    assert summary["parent_execution_id"] is None and summary["children"] == []
    assert summary["duration_ms"] == 2000 and summary["events"] == 3

    result = (directory / "result.md").read_text()
    assert "# ceo: completed" in result
    assert "Compare A and B" in result and "the answer" in result


def test_a_run_in_progress_is_visible_as_running(tmp_path):
    sink = FileTraceSink(tmp_path)
    feed(sink, ev("e1", "ceo", E.TASK_STARTED, sender="user", request="x"))
    summary = json.loads((only_dir(tmp_path) / "execution.json").read_text())
    assert summary["status"] == "running" and summary["finished_at"] is None
    assert not (only_dir(tmp_path) / "result.md").exists()


def test_a_failed_run_keeps_its_error_and_partial_answer(tmp_path):
    simple_run(FileTraceSink(tmp_path), None, "max_steps_exceeded", "no answer in 25")
    directory = only_dir(tmp_path)
    summary = json.loads((directory / "execution.json").read_text())
    assert summary["status"] == "max_steps_exceeded" and summary["error"] == "no answer in 25"
    result = (directory / "result.md").read_text()
    assert "(no answer)" in result and "error: no answer in 25" in result


def test_a_delegation_is_linked_both_ways(tmp_path):
    sink = FileTraceSink(tmp_path)
    feed(
        sink,
        ev("ceo1", "ceo", E.TASK_STARTED, sender="user", request="Compare A and B"),
        ev(
            "res1",
            "google-research",
            E.TASK_STARTED,
            1,
            task="t-res",
            sender="ceo",
            request="Find facts",
            parent_execution_id="ceo1",
        ),
        ev(
            "res1",
            "google-research",
            E.FINAL_RESULT,
            2,
            status="completed",
            answer="facts",
            error=None,
            steps=3,
        ),
        ev(
            "ceo1",
            "ceo",
            E.FINAL_RESULT,
            3,
            status="completed",
            answer="synthesis",
            error=None,
            steps=2,
        ),
    )
    ceo = next(d for d in tmp_path.iterdir() if "_ceo_" in d.name)
    research = next(d for d in tmp_path.iterdir() if "_google-research_" in d.name)

    parent = json.loads((ceo / "execution.json").read_text())
    assert parent["children"] == [
        {
            "execution_id": "res1",
            "task_id": "t-res",
            "agent_id": "google-research",
            "status": "completed",
            "trace": research.name,
        }
    ]
    child = json.loads((research / "execution.json").read_text())
    assert child["parent_execution_id"] == "ceo1" and child["sender"] == "ceo"
    assert f"../{research.name}/result.md" in (ceo / "result.md").read_text()
    assert "part of execution: `ceo1`" in (research / "result.md").read_text()


def test_interleaved_executions_do_not_mix(tmp_path):
    sink = FileTraceSink(tmp_path)
    feed(
        sink,
        ev("a", "github", E.TASK_STARTED, sender="user", request="A"),
        ev("b", "linkedin", E.TASK_STARTED, sender="user", request="B"),
        ev(
            "b", "linkedin", E.FINAL_RESULT, 1, status="completed", answer="B!", error=None, steps=1
        ),
        ev("a", "github", E.FINAL_RESULT, 2, status="failed", answer=None, error="boom", steps=1),
    )
    by_agent = {d.name.split("_")[1]: d for d in tmp_path.iterdir()}
    assert "B!" in (by_agent["linkedin"] / "result.md").read_text()
    assert json.loads((by_agent["github"] / "execution.json").read_text())["status"] == "failed"
    assert {e["execution_id"] for e in lines(by_agent["github"])} == {"a"}


def test_long_values_are_cut_and_odd_values_are_serialized(tmp_path):
    sink = FileTraceSink(tmp_path, max_field_chars=10)
    feed(
        sink,
        ev("e1", "ceo", E.TASK_STARTED, sender="user", request="r"),
        ev(
            "e1",
            "ceo",
            E.TOOL_RESULT,
            1,
            tool="web.open",
            output="x" * 25,
            input={
                "nested": [
                    "y" * 12,
                    {
                        1,
                    },
                ],
                "when": T0,
            },
            error=None,
        ),
    )
    data = lines(only_dir(tmp_path))[1]["data"]
    assert data["output"] == "x" * 10 + "...[truncated, 15 more characters]"
    assert data["input"]["nested"][0].startswith("y" * 10 + "...[truncated")
    assert data["input"]["nested"][1] == [1] and data["input"]["when"].startswith("2026-09-28")


def test_a_write_failure_never_breaks_the_run(tmp_path, caplog):
    blocked = tmp_path / "executions"
    blocked.write_text("a file, not a directory")
    sink = FileTraceSink(blocked)
    simple_run(sink)  # would raise if the failure escaped
    warnings = [r for r in caplog.records if "not written" in r.getMessage()]
    assert len(warnings) == 1  # said once for the execution, not once per event


def test_the_trace_is_private(tmp_path):
    simple_run(FileTraceSink(tmp_path / "executions"))
    assert (tmp_path / "executions").stat().st_mode & 0o077 == 0
    assert only_dir(tmp_path / "executions").stat().st_mode & 0o077 == 0


class Recorder:
    def __init__(self, error=None):
        self.events, self.error = [], error

    async def record(self, event):
        self.events.append(event)
        if self.error:
            raise self.error


def test_composite_reaches_every_sink_even_if_one_fails(caplog):
    broken, healthy = Recorder(RuntimeError("boom")), Recorder()
    feed(CompositeEventSink(broken, healthy), ev("e1", "ceo", E.TASK_STARTED))
    assert len(broken.events) == 1 and len(healthy.events) == 1
    assert "Recorder failed" in caplog.text
