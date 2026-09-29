"""Pure Markdown helpers: `##` sections and text search. No I/O."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE = ("```", "~~~")


@dataclass(frozen=True, slots=True)
class _Heading:
    index: int
    level: int
    title: str


def _headings(lines: list[str]) -> Iterator[_Heading]:
    in_fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(_FENCE):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if match:
            yield _Heading(i, len(match.group(1)), match.group(2))


def detect_eol(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _span(lines: list[str], section: str) -> tuple[int, int] | None:
    """Line range [start, end) of the `## section` block, subsections included."""
    headings = list(_headings(lines))
    target = next((h for h in headings if h.level == 2 and h.title == section), None)
    if target is None:
        return None
    end = next((h.index for h in headings if h.index > target.index and h.level <= 2), len(lines))
    return target.index, end


def get_section(text: str, section: str) -> str:
    """The `## section` block (heading included, trailing blank lines dropped), or ''."""
    lines = text.splitlines()
    span = _span(lines, section)
    if span is None:
        return ""
    block = lines[span[0] : span[1]]
    while block and not block[-1].strip():
        block.pop()
    return detect_eol(text).join(block)


def replace_section(text: str, section: str, body: str) -> str:
    """Replace the body of the `## section` heading; append the section if it is missing."""
    eol = detect_eol(text)
    lines = text.splitlines()
    block = [f"## {section}", ""]
    if body.strip():
        block += [*body.strip("\r\n").splitlines(), ""]

    span = _span(lines, section)
    if span is None:
        while lines and not lines[-1].strip():
            lines.pop()
        result = lines + ([""] if lines else []) + block
    else:
        result = lines[: span[0]] + block + lines[span[1] :]
    return eol.join(result).rstrip("\r\n") + eol


def remove_section(text: str, section: str) -> tuple[str, str] | None:
    """Cut the `## section` block out. Returns (remaining text, removed block) or None."""
    eol = detect_eol(text)
    lines = text.splitlines()
    span = _span(lines, section)
    if span is None:
        return None
    block = lines[span[0] : span[1]]
    while block and not block[-1].strip():
        block.pop()
    remaining = eol.join(lines[: span[0]] + lines[span[1] :]).rstrip("\r\n")
    return (remaining + eol if remaining else ""), eol.join(block)


def find_lines(text: str, query: str) -> list[tuple[str | None, int, str]]:
    """Return (section, line_number, text) for each line containing `query` (case-insensitive)."""
    needle = query.lower()
    lines = text.splitlines()
    section_at: dict[int, str | None] = {}
    current: str | None = None
    for h in _headings(lines):
        section_at[h.index] = h.title if h.level == 2 else (None if h.level == 1 else current)
    hits = []
    for i, line in enumerate(lines):
        current = section_at.get(i, current)
        if needle in line.lower():
            hits.append((current, i + 1, line.strip()))
    return hits
