from openclaw.domain.tasks.approval import Approval
from openclaw.domain.tools.model import RiskLevel, ToolCall
from openclaw.infrastructure.channels.telegram.formatting import (
    MAX_ARGUMENTS,
    approval_keyboard,
    approval_text,
    parse_callback,
    parse_command,
    split_message,
    to_telegram_html,
)


def test_a_short_text_is_one_piece_and_an_empty_one_is_none():
    assert split_message("hello") == ["hello"]
    assert split_message("   ") == []


def test_a_long_text_is_cut_at_a_blank_line_first():
    text = "a" * 60 + "\n\n" + "b" * 60
    assert split_message(text, limit=100) == ["a" * 60, "b" * 60]


def test_then_at_a_line_break_then_at_a_space_then_anywhere():
    assert split_message("a" * 60 + "\n" + "b" * 60, limit=100) == ["a" * 60, "b" * 60]
    assert split_message("a" * 60 + " " + "b" * 60, limit=100) == ["a" * 60, "b" * 60]
    assert split_message("x" * 250, limit=100) == ["x" * 100, "x" * 100, "x" * 50]


def test_no_piece_exceeds_the_limit_and_nothing_is_lost():
    text = " ".join(f"word{i}" for i in range(2000))
    pieces = split_message(text, limit=500)
    assert all(len(p) <= 500 for p in pieces)
    assert " ".join(pieces).split() == text.split()


def test_commands():
    assert parse_command("hello") == (None, "")
    assert parse_command("/help") == ("help", "")
    assert parse_command("/Run@MyBot writer  write a note") == ("run", "writer  write a note")
    assert parse_command("/run writer\nline 1\nline 2") == ("run", "writer\nline 1\nline 2")


def test_callback_data_round_trip():
    keyboard = approval_keyboard("abc123")
    buttons = keyboard["inline_keyboard"][0]
    assert [parse_callback(b["callback_data"]) for b in buttons] == [
        ("abc123", True),
        ("abc123", False),
    ]
    assert all(
        len(b["callback_data"].encode()) <= 64
        for b in approval_keyboard("f" * 32)["inline_keyboard"][0]
    )


def test_bad_callback_data_is_refused():
    for data in (None, 5, "", "ap:x", "ap::y", "zz:abc:y", "ap:abc:maybe", "ap:a:b:y"):
        assert parse_callback(data) is None


def test_the_approval_text_names_the_agent_the_tool_and_the_arguments():
    approval = Approval(
        "e1", "writer", ToolCall("docs.compile_pdf", {"path": "r/a.tex"}), RiskLevel.WRITE, "why"
    )
    text = approval_text(approval)
    for part in ("writer", "docs.compile_pdf", "write", "why", "r/a.tex"):
        assert part in text


def test_long_arguments_are_truncated():
    approval = Approval(
        "e1", "x", ToolCall("t", {"body": "z" * 10_000}), RiskLevel.EXTERNAL_COMMUNICATION, "r"
    )
    text = approval_text(approval)
    assert "truncated" in text and len(text) < MAX_ARGUMENTS + 500


# -- Markdown -> Telegram HTML ---------------------------------------------------------------
def test_headings_bold_italic_strike_and_code():
    text = "## Title\n**bold** *it* ~~old~~ `x < y` snake_case_name 5 * 7 = 35"
    assert to_telegram_html(text) == (
        "<b>Title</b>\n<b>bold</b> <i>it</i> <s>old</s> <code>x &lt; y</code> "
        "snake_case_name 5 * 7 = 35"
    )


def test_special_characters_are_escaped_and_the_text_is_never_lost():
    assert to_telegram_html("a < b & c > d <tag>") == "a &lt; b &amp; c &gt; d &lt;tag&gt;"
    plain = "(~300 mots) - 270-330 | %PDF-1.3"
    assert to_telegram_html(plain) == plain


def test_a_two_column_table_becomes_a_list_without_its_header():
    table = "| Element | Value |\n|---|---|\n| Pages | 1 |\n| Words | **280** |"
    assert to_telegram_html(table) == "\u2022 Pages: 1\n\u2022 Words: <b>280</b>"


def test_a_wider_table_keeps_its_column_names():
    table = "| Name | Size | Pages |\n|---|---|---|\n| a.pdf | 25 | 1 |"
    assert to_telegram_html(table) == "\u2022 a.pdf \u2014 Size: 25 \u2014 Pages: 1"


def test_lists_quotes_links_and_rules():
    text = "- one\n  - two\n---\n> quoted\n[doc](https://x.io/a_b?x=1&y=2)"
    assert to_telegram_html(text) == (
        "\u2022 one\n  \u25e6 two\n\n<blockquote>quoted</blockquote>\n"
        '<a href="https://x.io/a_b?x=1&amp;y=2">doc</a>'
    )


def test_code_blocks_are_kept_verbatim_and_escaped():
    assert to_telegram_html("```python\nif a < b:\n    **x**\n```") == (
        '<pre><code class="language-python">if a &lt; b:\n    **x**</code></pre>'
    )
    assert to_telegram_html("```\nunclosed") == "<pre>unclosed</pre>"


def test_a_code_block_cut_by_the_limit_is_closed_and_reopened():
    text = "```\n" + "line\n" * 40 + "```"
    pieces = split_message(text, limit=60)
    assert len(pieces) > 1 and all(len(p) <= 60 for p in pieces)
    assert all(p.startswith("```") and p.endswith("```") for p in pieces)
    assert sum(to_telegram_html(p).count("<pre>") for p in pieces) == len(pieces)
