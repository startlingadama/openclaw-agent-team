"""FileExecutionHistory and ReadHistory on traces written by the real FileTraceSink."""

import asyncio

import pytest

from openclaw.application.tasks.history import ReadHistory
from openclaw.domain.shared.errors import HistoryError
from openclaw.domain.tasks.execution import EventType as E
from openclaw.infrastructure.observability.history import FileExecutionHistory
from openclaw.infrastructure.observability.trace import FileTraceSink
from tests.unit.test_trace_sink import ev, feed


def run(coro):
    return asyncio.run(coro)


def write_run(
    sink,
    execution,
    agent,
    start,
    *,
    parent=None,
    request="ask",
    answer="done",
    status="completed",
    task=None,
    finish=True,
):
    events = [
        ev(
            execution,
            agent,
            E.TASK_STARTED,
            start,
            task=task,
            sender="ceo" if parent else "user",
            request=request,
            parent_execution_id=parent,
        ),
    ]
    if finish:
        events.append(
            ev(
                execution,
                agent,
                E.FINAL_RESULT,
                start + 1,
                task=task,
                status=status,
                answer=answer,
                error=None,
                steps=2,
            )
        )
    feed(sink, *events)


@pytest.fixture
def root(tmp_path):
    return tmp_path / "executions"


@pytest.fixture
def delegated(root):
    sink = FileTraceSink(root)
    write_run(sink, "aaa111", "github", 0, request="old one")
    feed(sink, ev("ceo222", "ceo", E.TASK_STARTED, 10, sender="user", request="Compare A and B"))
    write_run(
        sink, "res333", "google-research", 11, parent="ceo222", request="Find facts", answer="facts"
    )
    feed(
        sink,
        ev(
            "ceo222",
            "ceo",
            E.FINAL_RESULT,
            13,
            status="completed",
            answer="synthesis",
            error=None,
            steps=3,
        ),
    )
    return FileExecutionHistory(root)


def test_summaries_are_newest_first_with_the_request(delegated):
    found = run(delegated.summaries())
    assert [s.execution_id for s in found] == ["res333", "ceo222", "aaa111"]
    assert [s.request for s in found] == ["Find facts", "Compare A and B", "old one"]
    assert found[0].parent_execution_id == "ceo222" and found[0].status == "completed"


def test_roots_only_and_limit(delegated):
    assert [s.execution_id for s in run(delegated.summaries(roots_only=True))] == [
        "ceo222",
        "aaa111",
    ]
    assert [s.execution_id for s in run(delegated.summaries(1, roots_only=True))] == ["ceo222"]


def test_no_directory_means_no_history(tmp_path):
    assert run(FileExecutionHistory(tmp_path / "nothing").summaries()) == []


def test_find_by_execution_id_task_id_or_trace_name(delegated):
    assert run(delegated.find("ceo2")).execution_id == "ceo222"
    assert run(delegated.find("task-res")).execution_id == "res333"  # task id prefix
    trace = run(delegated.find("aaa111")).trace
    assert run(delegated.find(trace)).execution_id == "aaa111"


def test_find_errors_are_explicit(delegated):
    with pytest.raises(HistoryError, match="no execution matches 'zzz'"):
        run(delegated.find("zzz"))
    with pytest.raises(HistoryError, match="needed"):
        run(delegated.find("  "))
    with pytest.raises(HistoryError, match=r"matches 3 executions: res333.*ceo222.*aaa111"):
        run(delegated.find("task-"))  # every task id starts with "task-" here


def test_record_returns_request_answer_and_events(delegated):
    summary = run(delegated.find("ceo222"))
    record = run(delegated.record(summary))
    assert (record.request, record.answer) == ("Compare A and B", "synthesis")
    assert [e.type for e in record.events] == [E.TASK_STARTED, E.FINAL_RESULT]
    assert record.events[0].timestamp == summary.started_at


def test_a_line_cut_by_a_crash_is_ignored(root, delegated):
    summary = run(delegated.find("aaa111"))
    with (root / summary.trace / "events.jsonl").open("a") as stream:
        stream.write('{"ts": "2026-09-28T10:00')  # the process died mid-write
    record = run(delegated.record(summary))
    assert record.answer == "done" and len(record.events) == 2


def test_an_unreadable_trace_is_skipped_but_the_others_remain(root, delegated, caplog):
    broken = root / "20200101T000000_x_broken"
    broken.mkdir()
    (broken / "execution.json").write_text("{not json")
    assert [s.execution_id for s in run(delegated.summaries())] == ["res333", "ceo222", "aaa111"]
    assert "20200101T000000_x_broken" in caplog.text
    assert run(delegated.find("aaa")).agent_id == "github"


def test_a_run_that_never_ended_reads_as_running(root):
    sink = FileTraceSink(root)
    write_run(sink, "lost1", "ceo", 0, finish=False)
    (summary,) = run(FileExecutionHistory(root).summaries())
    assert summary.status == "running" and summary.finished_at is None


def test_tree_nests_delegations_under_their_supervisor(delegated):
    tree = run(ReadHistory(delegated).tree("ceo222"))
    assert tree.record.answer == "synthesis"
    (child,) = tree.children
    assert (child.record.summary.agent_id, child.record.answer) == ("google-research", "facts")
    assert child.children == ()


def test_tree_of_a_delegation_alone_is_that_delegation(delegated):
    tree = run(ReadHistory(delegated).tree("res333"))
    assert tree.record.summary.parent_execution_id == "ceo222" and tree.children == ()


def test_children_are_found_even_if_the_supervisor_never_ended(root):
    sink = FileTraceSink(root)
    feed(sink, ev("ceo1", "ceo", E.TASK_STARTED, 0, sender="user", request="x"))  # then killed
    write_run(sink, "m1", "github", 1, parent="ceo1")
    write_run(sink, "m2", "linkedin", 3, parent="ceo1")
    tree = run(ReadHistory(FileExecutionHistory(root)).tree("ceo1"))
    assert tree.record.summary.status == "running"
    assert [c.record.summary.execution_id for c in tree.children] == ["m1", "m2"]  # oldest first


def test_a_corrupted_parent_loop_does_not_recurse_forever(root):
    sink = FileTraceSink(root)
    write_run(sink, "a", "ceo", 0, parent="b")
    write_run(sink, "b", "github", 1, parent="a")
    tree = run(ReadHistory(FileExecutionHistory(root)).tree("a"))
    (child,) = tree.children
    assert child.record.summary.execution_id == "b" and child.children == ()


def test_recent_hides_delegations_unless_asked(delegated):
    history = ReadHistory(delegated)
    assert [s.execution_id for s in run(history.recent())] == ["ceo222", "aaa111"]
    assert len(run(history.recent(delegations=True))) == 3
