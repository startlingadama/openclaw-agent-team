"""The documents of a task, collected from its events, and their delivery to the chat (ADR-030)."""

import asyncio
from datetime import UTC, datetime

import pytest

from openclaw.domain.tasks.execution import EventType, ExecutionEvent
from openclaw.infrastructure.channels.telegram import (
    TaskDocuments,
    TelegramAdapter,
    TelegramConfig,
    TelegramError,
)
from openclaw.infrastructure.channels.telegram.documents import (
    COMPILE_TOOL,
    MAX_TASKS,
    WRITE_TOOLS,
)
from openclaw.infrastructure.tools.docs.files import DocumentFiles
from openclaw.infrastructure.tools.docs.provider import _SPECS
from tests.unit.test_telegram_adapter import CHAT, USER, FakeBackend, FakeClient, Outcome

NOW = datetime(2026, 9, 29, tzinfo=UTC)


def event(kind, tool, *, task="t1", execution="e1", **data):
    return ExecutionEvent(execution, task, "writer", kind, NOW, {"tool": tool, **data})


def called(tool, path, **kw):
    return event(EventType.TOOL_CALLED, tool, input={"path": path}, **kw)


def done(tool, status="success", **kw):
    return event(EventType.TOOL_RESULT, tool, status=status, **kw)


@pytest.fixture
def root(tmp_path):
    (tmp_path / "docs").mkdir()
    return tmp_path / "docs"


def collector(root):
    return TaskDocuments(DocumentFiles(root))


def feed(documents, *events):
    async def go():
        for item in events:
            await documents.record(item)

    asyncio.run(go())


def names(documents, task="t1"):
    return [d.name for d in documents.take(task)]


def test_the_tool_names_are_the_ones_of_the_docs_provider():
    assert WRITE_TOOLS | {COMPILE_TOOL} <= set(_SPECS)


def test_a_written_file_is_collected_once(root):
    (root / "note.md").write_text("# Hi")
    documents = collector(root)
    feed(documents, called("docs.write", "note.md"), done("docs.write"))
    (found,) = documents.take("t1")
    assert (found.name, found.path, found.size) == ("note.md", root.resolve() / "note.md", 4)
    assert documents.take("t1") == []


def test_a_failed_call_does_not_count(root):
    (root / "old.md").write_text("x")
    documents = collector(root)
    feed(documents, called("docs.write", "old.md"), done("docs.write", status="error"))
    assert names(documents) == []


def test_a_successful_compilation_counts_for_the_source_and_the_pdf(root):
    (root / "r").mkdir()
    (root / "r" / "a.tex").write_text("x")
    (root / "r" / "a.pdf").write_bytes(b"%PDF")
    documents = collector(root)
    feed(documents, called(COMPILE_TOOL, "r/a.tex"), done(COMPILE_TOOL))
    assert names(documents) == ["a.tex", "a.pdf"]


def test_a_file_written_then_patched_is_listed_once_in_first_position(root):
    for name in ("a.md", "b.md"):
        (root / name).write_text("x")
    documents = collector(root)
    feed(
        documents,
        called("docs.write", "a.md"),
        done("docs.write"),
        called("docs.write", "b.md"),
        done("docs.write"),
        called("docs.patch", "a.md"),
        done("docs.patch"),
    )
    assert names(documents) == ["a.md", "b.md"]


def test_other_tools_and_other_tasks_are_ignored(root):
    (root / "a.md").write_text("x")
    documents = collector(root)
    feed(
        documents,
        called("docs.read", "a.md"),
        done("docs.read"),
        called("web.search", "a.md"),
        called("docs.write", "a.md", task="other"),
        done("docs.write", task="other"),
    )
    assert names(documents, "t1") == []
    assert names(documents, "other") == ["a.md"]


def test_a_delegated_run_of_the_same_task_counts(root):
    (root / "a.md").write_text("x")
    documents = collector(root)
    feed(
        documents,
        called("docs.write", "a.md", execution="child"),
        done("docs.write", execution="child"),
    )
    assert names(documents) == ["a.md"]


def started(execution, task, parent=None):
    return event(
        EventType.TASK_STARTED, None, task=task, execution=execution, parent_execution_id=parent
    )


def test_the_files_of_a_delegated_run_belong_to_the_task_at_the_root(root):
    for name in ("a.md", "b.md"):
        (root / name).write_text("x")
    documents = collector(root)
    feed(
        documents,
        started("ceo-run", "user-task"),
        started("writer-run", "child-task", parent="ceo-run"),
        called("docs.write", "a.md", task="child-task", execution="writer-run"),
        done("docs.write", task="child-task", execution="writer-run"),
        started("nested-run", "grandchild-task", parent="writer-run"),
        called("docs.write", "b.md", task="grandchild-task", execution="nested-run"),
        done("docs.write", task="grandchild-task", execution="nested-run"),
    )
    assert names(documents, "child-task") == []
    assert names(documents, "user-task") == ["a.md", "b.md"]


def test_a_run_without_a_parent_keeps_its_own_task(root):
    (root / "a.md").write_text("x")
    documents = collector(root)
    feed(
        documents,
        started("run", "task"),
        called("docs.write", "a.md", task="task", execution="run"),
        done("docs.write", task="task", execution="run"),
    )
    assert names(documents, "task") == ["a.md"]


def test_a_result_is_paired_with_the_call_of_its_own_execution(root):
    (root / "a.md").write_text("x")
    (root / "b.md").write_text("x")
    documents = collector(root)
    feed(
        documents,
        called("docs.write", "a.md", execution="one"),
        called("docs.write", "b.md", execution="two"),
        done("docs.write", status="error", execution="two"),
        done("docs.write", execution="one"),
    )
    assert names(documents) == ["a.md"]


@pytest.mark.parametrize("path", ["../secret.md", "/etc/passwd", "a.exe", ".hidden.md", "a\\b.md"])
def test_a_path_the_download_check_refuses_is_never_collected(root, tmp_path, path):
    (tmp_path / "secret.md").write_text("no")
    documents = collector(root)
    feed(documents, called("docs.write", path), done("docs.write"))
    assert names(documents) == []


def test_a_symlink_to_outside_is_never_collected(root, tmp_path):
    (tmp_path / "outside.md").write_text("no")
    (root / "link.md").symlink_to(tmp_path / "outside.md")
    documents = collector(root)
    feed(documents, called("docs.write", "link.md"), done("docs.write"))
    assert names(documents) == []


def test_a_file_that_is_gone_is_skipped(root):
    documents = collector(root)
    feed(documents, called("docs.write", "gone.md"), done("docs.write"))
    assert names(documents) == []


def test_uncollected_tasks_are_bounded(root):
    documents = collector(root)
    for number in range(MAX_TASKS + 5):
        feed(documents, called("docs.write", "a.md", task=f"t{number}"))
    assert len(documents._tasks) == MAX_TASKS


# -- delivery by the adapter -----------------------------------------------------------------
class Doc:
    def __init__(self, path, size=None):
        self.path, self.name = path, path.name
        self.size = path.stat().st_size if size is None else size


class Files:
    def __init__(self, *docs, fail=False):
        self.docs, self.fail, self.asked = docs, fail, []

    def take(self, task_id):
        self.asked.append(task_id)
        if self.fail:
            raise RuntimeError("boom")
        return self.docs


class UploadingClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.documents, self.fail_upload = [], False

    async def send_document(self, chat_id, name, content, mime_type="application/octet-stream"):
        if self.fail_upload:
            raise TelegramError("sendDocument: nope")
        self.documents.append((chat_id, name, content, mime_type))
        return {"message_id": 1}


def run_task(files, client=None, outcome=None):
    client = client or UploadingClient()
    adapter = TelegramAdapter(
        client=client,
        backend=FakeBackend(outcome or Outcome()),
        approvals=None,
        config=TelegramConfig("t", frozenset({USER}), approval_timeout=5.0),
        documents=files,
    )
    from openclaw.infrastructure.channels.telegram.messages import InboundMessage

    asyncio.run(adapter._run(InboundMessage(CHAT, USER, "hi", 1), None, "hi"))
    return client


def test_the_files_follow_the_answer(tmp_path):
    pdf = tmp_path / "r.pdf"
    md = tmp_path / "r.md"
    pdf.write_bytes(b"%PDF-1")
    md.write_text("# R")
    files = Files(Doc(md), Doc(pdf))
    client = run_task(files)
    assert client.texts() == ["done"]
    assert client.documents == [
        (CHAT, "r.md", b"# R", "text/markdown"),
        (CHAT, "r.pdf", b"%PDF-1", "application/pdf"),
    ]
    assert len(files.asked) == 1


def test_files_are_sent_even_when_the_task_did_not_complete(tmp_path):
    md = tmp_path / "r.md"
    md.write_text("x")
    client = run_task(Files(Doc(md)), outcome=Outcome(status="failed", answer=None, error="late"))
    assert "The task failed" in client.texts()[0]
    assert [d[1] for d in client.documents] == ["r.md"]


def test_without_a_document_source_or_without_files_nothing_is_sent(tmp_path):
    assert run_task(None).documents == []
    assert run_task(Files()).documents == []


def test_a_file_that_is_too_large_is_reported_not_sent(tmp_path):
    big = tmp_path / "big.pdf"
    big.write_bytes(b"x")
    client = run_task(Files(Doc(big, size=51 * 1024 * 1024)))
    assert client.documents == []
    assert client.texts()[-1] == "big.pdf is too large for Telegram (limit 50 MB)."


def test_a_refused_upload_is_reported_and_the_next_file_is_still_sent(tmp_path):
    first, second = tmp_path / "a.md", tmp_path / "b.md"
    first.write_text("a")
    second.write_text("b")
    client = UploadingClient()
    client.fail_upload = True
    run_task(Files(Doc(first)), client)
    assert client.texts()[-1] == "Could not send a.md."
    client = run_task(Files(Doc(tmp_path / "gone.md", size=1), Doc(second)))
    assert client.texts()[-1] == "Could not send gone.md."
    assert [d[1] for d in client.documents] == ["b.md"]


def test_a_failing_document_source_never_loses_the_answer(tmp_path):
    client = run_task(Files(fail=True))
    assert client.texts() == ["done"] and client.documents == []
