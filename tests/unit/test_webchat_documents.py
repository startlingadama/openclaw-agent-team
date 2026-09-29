from openclaw.infrastructure.channels.webchat.documents import (
    history,
    produced_paths,
    task_documents,
)
from openclaw.infrastructure.tools.docs.files import DocumentFiles


def _call(tool, path, *, status="ok"):
    return [
        {"type": "tool.called", "label": tool, "status": "running", "input": {"path": path}},
        {"type": "tool.completed", "label": tool, "status": status},
    ]


def test_a_document_counts_once_its_tool_call_completed_without_error():
    events = [
        *_call("docs.write", "a.md"),
        *_call("docs.write", "failed.md", status="error"),
        *_call("docs.patch", "a.md"),
        *_call("docs.read", "read-only.md"),
        *_call("web.search", "x.md"),
    ]
    assert produced_paths(events) == ["a.md"]


def test_a_successful_compilation_produces_the_source_and_the_pdf():
    events = [*_call("docs.compile_pdf", "report/report.tex")]
    assert produced_paths(events) == ["report/report.tex", "report/report.pdf"]
    assert produced_paths(_call("docs.compile_pdf", "r/r.tex", status="error")) == []


def test_a_call_without_a_path_or_a_result_produces_nothing():
    called_only = [{"type": "tool.called", "label": "docs.write", "input": {"path": "a.md"}}]
    no_path = [
        {"type": "tool.called", "label": "docs.write", "input": "not a mapping"},
        {"type": "tool.completed", "label": "docs.write", "status": "ok"},
    ]
    assert produced_paths(called_only) == []
    assert produced_paths(no_path) == []


def test_history_and_task_documents_only_show_files_that_exist(tmp_path):
    (tmp_path / "doc").mkdir()
    (tmp_path / "doc" / "doc.md").write_text("# Doc", encoding="utf-8")
    files = DocumentFiles(tmp_path)
    events = [*_call("docs.write", "doc/doc.md"), *_call("docs.write", "gone.md")]

    assert [d["path"] for d in task_documents(files, events)] == ["doc/doc.md"]

    tasks = [
        {"id": "new", "agentId": "writer", "title": "second", "events": events},
        {"id": "old", "agentId": "ceo", "title": "first", "events": events},
    ]
    (listed,) = history(files, tasks)
    assert listed["path"] == "doc/doc.md" and listed["taskId"] == "new"  # the latest task wins
    assert listed["kind"] == "md" and listed["folder"] == "doc"
