import asyncio

import pytest

from openclaw.domain.memory.model import MemoryLayer, MemoryReference
from openclaw.domain.shared.errors import ValidationError
from openclaw.infrastructure.memory.markdown.repository import MarkdownMemoryRepository

AGENT = MemoryReference(MemoryLayer.AGENT, "github")
USER = MemoryReference(MemoryLayer.USER, "github")
SHARED = MemoryReference(MemoryLayer.SHARED_TEAM)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "agents" / "github").mkdir(parents=True)
    (tmp_path / "agents" / "github" / "MEMORY.md").write_text("# Memory\n\n## Lessons\n\nfirst\n")
    return MarkdownMemoryRepository(tmp_path / "agents", tmp_path / "workspace")


def run(coro):
    return asyncio.run(coro)


def test_read_existing_and_missing_documents(repo):
    assert "first" in run(repo.read(AGENT))
    assert run(repo.read(USER)) == ""  # nothing written yet
    assert run(repo.read(SHARED)) == ""


def test_write_replaces_the_document_on_disk(repo, tmp_path):
    run(repo.write(AGENT, "# Memory\n\nreplaced\n"))
    assert (tmp_path / "agents/github/MEMORY.md").read_text() == "# Memory\n\nreplaced\n"
    assert not list((tmp_path / "agents/github").glob("*.tmp"))


def test_update_changes_only_one_section(repo):
    run(repo.update(AGENT, "Lessons", "second"))
    run(repo.update(AGENT, "Important Facts", "likes Python"))
    text = run(repo.read(AGENT))
    assert "second" in text and "first" not in text and "likes Python" in text


def test_layers_map_to_the_spec_locations(repo, tmp_path):
    run(repo.write(USER, "# User\n"))
    run(repo.write(SHARED, "# Shared\n"))
    assert (tmp_path / "agents/github/USER.md").exists()
    assert (tmp_path / "workspace/shared/MEMORY.md").exists()


def test_search_across_layers(repo):
    run(repo.write(USER, "# User\n\nPreferences: Python\n"))
    hits = run(repo.search("python", [AGENT, USER]))
    assert [(h.reference.layer, h.line) for h in hits] == [(MemoryLayer.USER, 3)]
    assert run(repo.search("first", [AGENT]))[0].section == "Lessons"


def test_agent_isolation_and_input_validation(repo):
    for bad in ("../github", "a/b", "", "..", "x y"):
        with pytest.raises(ValidationError):
            run(repo.read(MemoryReference(MemoryLayer.AGENT, bad)))
    with pytest.raises(ValidationError):  # unknown agent workspace is not created implicitly
        run(repo.write(MemoryReference(MemoryLayer.AGENT, "ghost"), "x"))
    with pytest.raises(ValidationError):
        run(repo.search("  ", [AGENT]))


def test_layers_without_a_markdown_location_are_refused(repo):
    for layer in (MemoryLayer.WORKING, MemoryLayer.SESSION):
        with pytest.raises(ValidationError):
            run(repo.read(MemoryReference(layer)))


def test_private_layers_require_an_agent_id():
    with pytest.raises(ValidationError):
        MemoryReference(MemoryLayer.AGENT)


def test_concurrent_updates_do_not_lose_sections(repo):
    async def go():
        await asyncio.gather(*(repo.update(AGENT, f"S{i}", f"body {i}") for i in range(10)))

    run(go())
    text = run(repo.read(AGENT))
    assert all(f"## S{i}" in text for i in range(10))


def _dated(repo, day):
    from datetime import date

    repo._clock = lambda: date.fromisoformat(day)
    return repo


def test_archive_moves_a_section_next_to_the_source_file(repo, tmp_path):
    run(repo.write(AGENT, "# Memory\n\n## Previous Tasks\n\nold task\n\n## Lessons\n\nfirst\n"))
    _dated(repo, "2026-09-28")
    run(repo.archive(AGENT, "Previous Tasks"))
    live = (tmp_path / "agents/github/MEMORY.md").read_text()
    assert "Previous Tasks" not in live and "## Lessons\n\nfirst" in live
    archived = (tmp_path / "agents/github/archive/MEMORY-2026-09-28.md").read_text()
    assert archived == "## Previous Tasks\n\nold task\n"


def test_archives_of_the_same_day_are_appended(repo, tmp_path):
    run(repo.write(AGENT, "## A\n\na\n\n## B\n\nb\n"))
    _dated(repo, "2026-09-28")
    run(repo.archive(AGENT, "A"))
    run(repo.archive(AGENT, "B"))
    archived = (tmp_path / "agents/github/archive/MEMORY-2026-09-28.md").read_text()
    assert archived == "## A\n\na\n\n## B\n\nb\n"
    assert run(repo.read(AGENT)) == ""


def test_archive_uses_the_file_name_of_each_layer(repo, tmp_path):
    _dated(repo, "2026-09-28")
    run(repo.write(USER, "## Projects\n\nx\n"))
    run(repo.write(SHARED, "## Facts\n\ny\n"))
    run(repo.archive(USER, "Projects"))
    run(repo.archive(SHARED, "Facts"))
    assert (tmp_path / "agents/github/archive/USER-2026-09-28.md").exists()
    assert (tmp_path / "workspace/shared/archive/MEMORY-2026-09-28.md").exists()


def test_archiving_a_missing_section_changes_nothing(repo, tmp_path):
    before = run(repo.read(AGENT))
    with pytest.raises(ValidationError):
        run(repo.archive(AGENT, "Nope"))
    assert run(repo.read(AGENT)) == before
    assert not (tmp_path / "agents/github/archive").exists()


# -- history ------------------------------------------------------------------------------
def _at(repo, iso):
    from datetime import datetime

    repo._now = lambda: datetime.fromisoformat(iso)
    return repo


def test_history_is_empty_before_any_change(repo):
    assert run(repo.history(AGENT)) == []


def test_history_records_write_update_and_archive_newest_first(repo):
    from openclaw.domain.memory.model import MemoryOperation

    _at(repo, "2026-09-28T10:00:00+00:00")
    run(repo.write(AGENT, "# Memory v2\n\n## Lessons\n\nfirst\n"))
    _at(repo, "2026-09-28T11:00:00+00:00")
    run(repo.update(AGENT, "Lessons", "second"))
    _at(repo, "2026-09-28T12:00:00+00:00")
    run(repo.archive(AGENT, "Lessons"))

    changes = run(repo.history(AGENT))
    assert [c.operation for c in changes] == [
        MemoryOperation.ARCHIVE,
        MemoryOperation.UPDATE,
        MemoryOperation.WRITE,
    ]
    archive, update, write = changes
    assert (archive.section, archive.before, archive.after) == (
        "Lessons",
        "## Lessons\n\nsecond",
        "",
    )
    assert (update.section, update.before, update.after) == (
        "Lessons",
        "## Lessons\n\nfirst",
        "## Lessons\n\nsecond",
    )
    assert (
        write.section is None
        and write.before.startswith("# Memory\n")
        and write.after.startswith("# Memory v2")
    )
    assert archive.timestamp.hour == 12 and all(c.reference == AGENT for c in changes)


def test_update_creating_a_section_has_an_empty_before(repo):
    run(repo.update(AGENT, "Important Facts", "likes Python"))
    (change,) = run(repo.history(AGENT))
    assert change.before == "" and change.after == "## Important Facts\n\nlikes Python"


def test_changes_that_alter_nothing_are_not_recorded(repo):
    run(repo.update(AGENT, "Lessons", "second"))
    run(repo.update(AGENT, "Lessons", "second"))
    run(repo.write(AGENT, run(repo.read(AGENT))))
    assert len(run(repo.history(AGENT))) == 1


def test_history_limit(repo):
    for i in range(5):
        run(repo.update(AGENT, "Lessons", f"v{i}"))
    latest = run(repo.history(AGENT, limit=2))
    assert [c.after for c in latest] == ["## Lessons\n\nv4", "## Lessons\n\nv3"]
    with pytest.raises(ValidationError):
        run(repo.history(AGENT, limit=0))


def test_history_is_kept_per_document_next_to_the_source(repo, tmp_path):
    run(repo.write(USER, "# User\n\nnote é\n"))
    run(repo.write(SHARED, "# Shared\n"))
    assert run(repo.history(AGENT)) == []
    assert len(run(repo.history(USER))) == 1 and len(run(repo.history(SHARED))) == 1
    assert run(repo.history(USER))[0].after == "# User\n\nnote é\n"  # unicode round-trip
    assert (tmp_path / "agents/github/history/USER.jsonl").exists()
    assert (tmp_path / "workspace/shared/history/MEMORY.jsonl").exists()
    assert (
        run(repo.read(AGENT)) == "# Memory\n\n## Lessons\n\nfirst\n"
    )  # history never touches memory


def test_history_validates_the_reference_like_every_other_operation(repo):
    with pytest.raises(ValidationError):
        run(repo.history(MemoryReference(MemoryLayer.AGENT, "../github")))
    with pytest.raises(ValidationError):
        run(repo.history(MemoryReference(MemoryLayer.SESSION)))


def test_a_corrupted_history_is_reported_explicitly(repo, tmp_path):
    run(repo.update(AGENT, "Lessons", "second"))
    history = tmp_path / "agents/github/history/MEMORY.jsonl"
    history.write_text(history.read_text() + "not json\n")
    with pytest.raises(ValidationError):
        run(repo.history(AGENT))


def test_concurrent_updates_are_all_recorded(repo):
    async def go():
        await asyncio.gather(*(repo.update(AGENT, f"S{i}", f"body {i}") for i in range(10)))

    run(go())
    assert len(run(repo.history(AGENT))) == 10
