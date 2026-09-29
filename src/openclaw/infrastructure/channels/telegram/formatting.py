"""Rendering for Telegram: Markdown -> Telegram HTML, message splitting, approval text, callback
data.

Agent answers are Markdown written by an LLM, and Telegram understands none of it (no headings,
no tables, and MarkdownV2 refuses a message on one unescaped character). `to_telegram_html`
converts the common subset to the HTML tags Telegram supports and escapes everything else, so
arbitrary text is safe. The adapter still falls back to plain text if Telegram refuses a message.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from openclaw.domain.tasks.approval import Approval

# Telegram's limit is 4096 characters; the margin covers how it counts some characters.
MAX_MESSAGE = 4000
MAX_ARGUMENTS = 1500
CALLBACK_PREFIX = "ap"

HELP_TEXT = (
    "OpenClaw Agent Team\n\n"
    "Send a message and the team's supervisor takes care of it.\n\n"
    "/agents - list the agents\n"
    "/run <agent> <task> - give a task to one agent directly\n"
    "/help - this help\n\n"
    "Actions that need your approval (sending, publishing) come with "
    "Approve / Reject buttons."
)


_FENCE_PAD = 8  # room kept to close a code block at the end of a piece and reopen it in the next


def _fence_lines(text: str) -> int:
    return sum(1 for line in text.split("\n") if line.strip().startswith("```"))


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Cut `text` into pieces of at most `limit` characters, preferably at a blank line, then at
    a line break, then at a space. A code block cut in two is closed and reopened, so each piece
    stays valid Markdown."""
    rest = text.strip()
    reserve = _FENCE_PAD if "```" in rest else 0
    chunks: list[str] = []
    reopen = False  # a code block is open at the start of `rest`
    while True:
        prefix = "```\n" if reopen else ""
        if len(prefix) + len(rest) <= limit:
            if rest:
                chunks.append(prefix + rest)
            return chunks
        room = limit - reserve - len(prefix)
        floor = room // 2
        cut = rest.rfind("\n\n", 0, room)
        if cut < floor:
            cut = rest.rfind("\n", 0, room)
        if cut < floor:
            cut = rest.rfind(" ", 0, room)
        if cut < floor:
            cut = room
        piece = rest[:cut].rstrip()
        still_open = reopen ^ (_fence_lines(piece) % 2 == 1)
        chunks.append(prefix + piece + ("\n```" if still_open else ""))
        reopen = still_open
        rest = rest[cut:].lstrip("\n") if reopen else rest[cut:].lstrip()


# -- Markdown -> Telegram HTML ---------------------------------------------------------------
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_PLACEHOLDER = re.compile("\x00(\\d+)\x00")
_BOLD_MARKERS = re.compile(r"\*\*|__")

_MARKS = (
    (re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*"), r"<b><i>\1</i></b>"),
    (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"), r"<b>\1</b>"),
    (re.compile(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)"), r"<b>\1</b>"),
    (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), r"<s>\1</s>"),
    (re.compile(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])"), r"<i>\1</i>"),
    (re.compile(r"(?<![\w_])_(?![\s_])(.+?)(?<![\s_])_(?![\w_])"), r"<i>\1</i>"),
)


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _marks(escaped: str) -> str:
    for pattern, replacement in _MARKS:
        escaped = pattern.sub(replacement, escaped)
    return escaped


def _inline(text: str) -> str:
    """Bold, italic, strike-through, `code` and links of one line; everything else escaped."""
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    text = text.replace("\x00", "")  # the stash markers must not come from the text itself
    text = _CODE_SPAN.sub(lambda m: keep(f"<code>{_escape(m.group(1))}</code>"), text)
    text = _escape(text)

    def link(match: re.Match[str]) -> str:
        url = match.group(2).replace('"', "%22")
        return keep(f'<a href="{url}">{_marks(match.group(1))}</a>')

    text = _marks(_LINK.sub(link, text))
    while _PLACEHOLDER.search(text):  # a link label can hold a code span: restore until stable
        text = _PLACEHOLDER.sub(lambda m: stash[int(m.group(1))], text)
    return text


def _cells(line: str) -> list[str]:
    row = line.strip()
    row = row.removeprefix("|").removesuffix("|")
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", row)]


def _table(rows: list[list[str]]) -> list[str]:
    """A Markdown table as a list: Telegram has no tables and a monospace grid overflows a phone."""
    header, body = rows[0], rows[1:]
    width = len(header)
    lines: list[str] = []
    for row in body:
        row = (row + [""] * width)[: max(width, len(row))]
        if width == 1:
            lines.append(f"• {_inline(row[0])}")
        elif width == 2:
            lines.append(f"• {_inline(row[0])}: {_inline(row[1])}")
        else:
            details = [
                f"{_inline(header[i])}: {_inline(cell)}"
                for i, cell in enumerate(row[1:], start=1)
                if cell and i < width
            ]
            lines.append(" — ".join([f"• {_inline(row[0])}", *details]))
    return lines


def to_telegram_html(text: str) -> str:
    """Markdown written by an agent -> the HTML subset Telegram accepts (`parse_mode="HTML"`).

    Handled: headings (bold), **bold**, *italic*, ~~strike~~, `code`, fenced code blocks, links,
    bullet lists, block quotes and tables (as lists). Anything else is text and is escaped."""
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            language = re.sub(r"[^\w+#.-]", "", stripped[3:])
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # the closing fence (an unclosed block simply ends with the text)
            code = _escape("\n".join(body))
            if language:
                out.append(f'<pre><code class="language-{language}">{code}</code></pre>')
            else:
                out.append(f"<pre>{code}</pre>")
            continue
        if (
            "|" in line
            and i + 1 < len(lines)
            and "|" in lines[i + 1]
            and _TABLE_SEPARATOR.match(lines[i + 1])
        ):
            rows = [_cells(line)]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            if len(rows) > 1:
                out.extend(_table(rows))
            else:  # a header with no row
                out.append("<b>" + " | ".join(_inline(cell) for cell in rows[0]) + "</b>")
            continue
        if _QUOTE.match(line):
            quoted: list[str] = []
            while i < len(lines) and (m := _QUOTE.match(lines[i])):
                quoted.append(_inline(m.group(1)))
                i += 1
            out.append("<blockquote>" + "\n".join(quoted) + "</blockquote>")
            continue
        i += 1
        if _RULE.match(line):
            out.append("")
        elif heading := _HEADING.match(line):
            title = _BOLD_MARKERS.sub("", heading.group(1))  # the heading is bold already
            out.append(f"<b>{_inline(title)}</b>")
        elif bullet := _BULLET.match(line):
            level = len(bullet.group(1).expandtabs(4)) // 2
            marker = "•" if level == 0 else "◦"
            out.append(f"{'  ' * level}{marker} {_inline(bullet.group(2))}")
        else:
            out.append(_inline(line))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def parse_command(text: str) -> tuple[str | None, str]:
    """`/run@MyBot writer do x` -> ("run", "writer do x"); no command -> (None, "")."""
    if not text.startswith("/"):
        return None, ""
    parts = text.split(None, 1)
    name = parts[0][1:].split("@", 1)[0].lower()
    return name, parts[1].strip() if len(parts) > 1 else ""


def approval_text(approval: Approval) -> str:
    call = approval.call
    arguments = json.dumps(dict(call.arguments), ensure_ascii=False, default=str, indent=2)
    if len(arguments) > MAX_ARGUMENTS:
        arguments = arguments[:MAX_ARGUMENTS] + "\n... (truncated)"
    return (
        "Approval needed\n"
        f"Agent: {approval.agent_id}\n"
        f"Tool: {call.name}\n"
        f"Risk: {approval.risk_level}\n"
        f"Reason: {approval.reason}\n\n"
        f"{arguments}"
    )


def approval_keyboard(approval_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "Approve", "callback_data": f"{CALLBACK_PREFIX}:{approval_id}:y"},
                {"text": "Reject", "callback_data": f"{CALLBACK_PREFIX}:{approval_id}:n"},
            ]
        ]
    }


def parse_callback(data: object) -> tuple[str, bool] | None:
    """`ap:<id>:y` -> (id, True); anything else -> None."""
    if not isinstance(data, str):
        return None
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX or parts[2] not in ("y", "n") or not parts[1]:
        return None
    return parts[1], parts[2] == "y"
