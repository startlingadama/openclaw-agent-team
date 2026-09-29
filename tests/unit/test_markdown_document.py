from openclaw.infrastructure.memory.markdown.document import find_lines, replace_section

DOC = """# Memory

## Important Facts

The user prefers Python.

## Previous Tasks

old task

## Lessons

learned
"""


def test_replace_existing_section_keeps_the_rest():
    out = replace_section(DOC, "Previous Tasks", "new task")
    assert "## Previous Tasks\n\nnew task\n\n## Lessons" in out
    assert "old task" not in out
    assert "The user prefers Python." in out and out.endswith("learned\n")


def test_missing_section_is_appended():
    out = replace_section(DOC, "Contacts", "alice")
    assert out.endswith("## Lessons\n\nlearned\n\n## Contacts\n\nalice\n")


def test_empty_document_gets_the_section():
    assert replace_section("", "Facts", "x") == "## Facts\n\nx\n"


def test_empty_body_empties_the_section():
    out = replace_section(DOC, "Important Facts", "")
    assert "## Important Facts\n\n## Previous Tasks" in out


def test_subsections_belong_to_their_section():
    doc = "## A\n\n### sub\n\ntext\n\n## B\n\nb\n"
    assert replace_section(doc, "A", "new") == "## A\n\nnew\n\n## B\n\nb\n"


def test_headings_inside_code_fences_are_ignored():
    doc = "## A\n\n```\n## B\n```\n\n## C\n\nc\n"
    out = replace_section(doc, "A", "x")
    assert out == "## A\n\nx\n\n## C\n\nc\n"


def test_crlf_files_keep_their_line_endings():
    out = replace_section("## A\r\n\r\nold\r\n", "A", "new")
    assert out == "## A\r\n\r\nnew\r\n"


def test_find_lines_reports_section_and_line_number():
    assert find_lines(DOC, "PYTHON") == [("Important Facts", 5, "The user prefers Python.")]
    assert find_lines(DOC, "nothing") == []


def test_remove_section_returns_remaining_text_and_block():
    from openclaw.infrastructure.memory.markdown.document import remove_section

    remaining, block = remove_section(DOC, "Previous Tasks")
    assert block == "## Previous Tasks\n\nold task"
    assert (
        remaining
        == "# Memory\n\n## Important Facts\n\nThe user prefers Python.\n\n## Lessons\n\nlearned\n"
    )


def test_remove_last_and_only_sections():
    from openclaw.infrastructure.memory.markdown.document import remove_section

    remaining, block = remove_section(DOC, "Lessons")
    assert block == "## Lessons\n\nlearned" and remaining.endswith("old task\n")
    assert remove_section("## A\n\nx\n", "A") == ("", "## A\n\nx")
    assert remove_section(DOC, "Nope") is None


def test_get_section_returns_the_block_or_empty():
    from openclaw.infrastructure.memory.markdown.document import get_section

    assert get_section(DOC, "Previous Tasks") == "## Previous Tasks\n\nold task"
    assert get_section(DOC, "Missing") == ""
