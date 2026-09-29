"""`openclaw runs` and `openclaw show` on real trace files (written by FileTraceSink)."""

import pytest

from openclaw.domain.tasks.execution import EventType as E
from openclaw.entrypoints.cli import main
from openclaw.infrastructure.observability.trace import FileTraceSink
from tests.unit.test_trace_sink import ev, feed


@pytest.fixture
def home(tmp_path, monkeypatch):
    sink = FileTraceSink(tmp_path / "executions")
    feed(
        sink,
        ev("old111", "github", E.TASK_STARTED, 0, sender="user", request="Open an issue"),
        ev(
            "old111",
            "github",
            E.FINAL_RESULT,
            1,
            status="failed",
            answer=None,
            error="approval rejected",
            steps=4,
        ),
        ev("ceo222", "ceo", E.TASK_STARTED, 10, sender="user", request="Compare A and B"),
        ev(
            "res333",
            "google-research",
            E.TASK_STARTED,
            11,
            sender="ceo",
            request="Find facts\n\nContext:\nfor a board memo",
            parent_execution_id="ceo222",
        ),
        ev(
            "res333",
            "google-research",
            E.TOOL_CALLED,
            12,
            tool="web.search",
            input={"query": "A vs B"},
        ),
        ev(
            "res333",
            "google-research",
            E.FINAL_RESULT,
            13,
            status="completed",
            answer="line one\nline two",
            error=None,
            steps=2,
        ),
        ev(
            "ceo222",
            "ceo",
            E.FINAL_RESULT,
            15,
            status="completed",
            answer="synthesis",
            error=None,
            steps=3,
        ),
    )
    monkeypatch.setenv("OPENCLAW_HOME", str(tmp_path))
    return tmp_path


def out(capsys):
    return capsys.readouterr().out


def test_runs_lists_supervisor_runs_newest_first(home, capsys):
    assert main(["runs"]) == 0
    lines = out(capsys).splitlines()
    assert len(lines) == 2  # the delegation is part of ceo222
    assert lines[0].startswith("ceo222") and "completed" in lines[0] and "3 steps" in lines[0]
    assert lines[0].endswith("Compare A and B") and "2026-09-28 10:00:10Z" in lines[0]
    assert lines[1].startswith("old111") and "failed" in lines[1] and "Open an issue" in lines[1]


def test_runs_all_includes_delegations_and_marks_them(home, capsys):
    assert main(["runs", "--all"]) == 0
    lines = out(capsys).splitlines()
    assert [ln.split()[0] for ln in lines] == ["res333", "ceo222", "old111"]
    assert "(delegation) Find facts Context: for a board memo" in lines[0]  # one line per run


def test_runs_limit(home, capsys):
    assert main(["runs", "-n", "1"]) == 0
    assert len(out(capsys).splitlines()) == 1


def test_runs_limit_must_be_positive():
    with pytest.raises(SystemExit) as exc:
        main(["runs", "-n", "0"])
    assert exc.value.code == 2


def test_runs_with_no_history(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENCLAW_HOME", str(tmp_path))
    assert main(["runs"]) == 0
    assert "no execution recorded" in out(capsys)


def test_show_prints_who_asked_whom_as_a_tree(home, capsys):
    assert main(["show", "ceo2"]) == 0
    text = out(capsys)
    lines = text.splitlines()
    assert lines[0] == "user -> ceo: completed"
    assert "  request:\n    Compare A and B\n  answer:\n    synthesis" in text
    child = lines.index("    ceo -> google-research: completed")  # one level deeper
    assert lines[child + 1].lstrip().startswith("execution res333")
    assert "        Find facts" in text and "        Context:" in text  # multi-line kept, indented
    assert "        line one\n        line two" in text
    assert "events:" not in text


def test_show_a_delegation_says_what_it_belongs_to(home, capsys):
    assert main(["show", "res333"]) == 0
    text = out(capsys)
    assert text.startswith("ceo -> google-research: completed")
    assert "part of execution ceo222" in text


def test_show_a_failed_run_prints_its_error(home, capsys):
    assert main(["show", "old111"]) == 0
    text = out(capsys)
    assert "failed" in text and "error: approval rejected" in text and "(none)" in text


def test_show_events_lists_every_step_of_every_run(home, capsys):
    assert main(["show", "ceo222", "--events"]) == 0
    text = out(capsys)
    assert text.count("events:") == 2
    assert "tool_called tool=web.search input={'query': 'A vs B'}" in text
    assert "10:00:12 tool_called" in text  # the UTC time of the event


def test_show_unknown_or_ambiguous_execution_exits_2(home, capsys):
    assert main(["show", "nope"]) == 2
    assert "no execution matches 'nope'" in capsys.readouterr().err
    assert main(["show", "task-"]) == 2
    assert "matches 3 executions" in capsys.readouterr().err


def test_show_needs_an_execution():
    with pytest.raises(SystemExit) as exc:
        main(["show"])
    assert exc.value.code == 2


def test_reading_history_needs_no_llm_key(home, monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert main(["runs"]) == 0 and main(["show", "ceo222"]) == 0


def test_long_requests_are_clipped_in_the_list(tmp_path, monkeypatch, capsys):
    feed(
        FileTraceSink(tmp_path / "executions"),
        ev("long1", "ceo", E.TASK_STARTED, 0, sender="user", request="word " * 100),
    )
    monkeypatch.setenv("OPENCLAW_HOME", str(tmp_path))
    assert main(["runs"]) == 0
    line = out(capsys).strip()
    assert line.endswith("...") and len(line) < 160 and "running" in line
