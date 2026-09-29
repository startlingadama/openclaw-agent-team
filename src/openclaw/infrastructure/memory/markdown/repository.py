"""MarkdownMemoryRepository: Markdown files are the source of truth (ADR-005)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from openclaw.domain.memory.model import (
    MemoryChange,
    MemoryHit,
    MemoryLayer,
    MemoryOperation,
    MemoryReference,
)
from openclaw.domain.shared.errors import ValidationError
from openclaw.infrastructure.memory.markdown.document import (
    detect_eol,
    find_lines,
    get_section,
    remove_section,
    replace_section,
)

_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class MarkdownMemoryRepository:
    """Layout (ARCHITECTURE sections 4 and 12):

    - AGENT       -> <agents_dir>/<agent_id>/MEMORY.md
    - USER        -> <agents_dir>/<agent_id>/USER.md
    - SHARED_TEAM -> <workspace_dir>/shared/MEMORY.md

    Archived sections go to `archive/<file>-<date>.md` next to the source file.
    History (one JSON object per line, oldest first) goes to `history/<file>.jsonl` next to the
    source file; a change that alters nothing is not recorded.

    Working and session memory have no Markdown location in the specs yet.
    """

    def __init__(
        self,
        agents_dir: Path | str,
        workspace_dir: Path | str,
        clock: Callable[[], date] = lambda: datetime.now(UTC).date(),
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._agents_dir = Path(agents_dir)
        self._workspace_dir = Path(workspace_dir)
        self._clock = clock
        self._now = now
        self._locks: dict[Path, asyncio.Lock] = {}

    # -- port -----------------------------------------------------------------------------
    async def read(self, reference: MemoryReference) -> str:
        return await asyncio.to_thread(self._read, self._path(reference))

    async def write(self, reference: MemoryReference, content: str) -> None:
        path = self._path(reference)
        async with self._lock(path):
            before = await asyncio.to_thread(self._read, path)
            await asyncio.to_thread(self._write, path, content)
            await self._record(reference, path, MemoryOperation.WRITE, None, before, content)

    async def update(self, reference: MemoryReference, section: str, content: str) -> None:
        self._check_section(section)
        path = self._path(reference)
        async with self._lock(path):
            current = await asyncio.to_thread(self._read, path)
            updated = replace_section(current, section, content)
            await asyncio.to_thread(self._write, path, updated)
            await self._record(
                reference,
                path,
                MemoryOperation.UPDATE,
                section,
                get_section(current, section),
                get_section(updated, section),
            )

    async def archive(self, reference: MemoryReference, section: str) -> None:
        """Move a section to `archive/<file>-<date>.md`; several archives of a day are appended."""
        self._check_section(section)
        path = self._path(reference)
        async with self._lock(path):
            current = await asyncio.to_thread(self._read, path)
            removed = remove_section(current, section)
            if removed is None:
                raise ValidationError(f"section not found: {section}")
            remaining, block = removed
            target = path.parent / "archive" / f"{path.stem}-{self._clock().isoformat()}.md"
            existing = await asyncio.to_thread(self._read, target)
            eol = detect_eol(current)
            merged = (existing.rstrip("\r\n") + eol + eol if existing.strip() else "") + block + eol
            # archive first: if the second write fails the section is duplicated, never lost
            await asyncio.to_thread(self._write, target, merged)
            await asyncio.to_thread(self._write, path, remaining)
            await self._record(reference, path, MemoryOperation.ARCHIVE, section, block, "")

    async def history(
        self, reference: MemoryReference, limit: int | None = None
    ) -> list[MemoryChange]:
        """Changes of one document, newest first."""
        if limit is not None and limit < 1:
            raise ValidationError("limit must be a positive integer")
        path = self._path(reference)
        raw = await asyncio.to_thread(self._read, self._history_path(path))
        changes = [self._parse_change(reference, line) for line in raw.splitlines() if line.strip()]
        changes.reverse()
        return changes if limit is None else changes[:limit]

    async def search(self, query: str, references: Sequence[MemoryReference]) -> list[MemoryHit]:
        if not query.strip():
            raise ValidationError("search query must not be empty")
        hits: list[MemoryHit] = []
        for reference in references:
            text = await asyncio.to_thread(self._read, self._path(reference))
            hits += [MemoryHit(reference, s, n, t) for s, n, t in find_lines(text, query)]
        return hits

    # -- internals ------------------------------------------------------------------------
    def _path(self, reference: MemoryReference) -> Path:
        layer = reference.layer
        if layer is MemoryLayer.SHARED_TEAM:
            return self._workspace_dir / "shared" / "MEMORY.md"
        if layer in (MemoryLayer.AGENT, MemoryLayer.USER):
            agent_id = reference.agent_id or ""
            if not _AGENT_ID.match(agent_id):  # blocks path traversal (REQUIREMENTS section 21)
                raise ValidationError(f"invalid agent id: {agent_id!r}")
            workspace = self._agents_dir / agent_id
            if not workspace.is_dir():
                raise ValidationError(f"unknown agent workspace: {agent_id}")
            return workspace / ("MEMORY.md" if layer is MemoryLayer.AGENT else "USER.md")
        raise ValidationError(f"memory layer '{layer}' is not backed by Markdown files yet")

    @staticmethod
    def _history_path(path: Path) -> Path:
        return path.parent / "history" / f"{path.stem}.jsonl"

    async def _record(
        self,
        reference: MemoryReference,
        path: Path,
        operation: MemoryOperation,
        section: str | None,
        before: str,
        after: str,
    ) -> None:
        """Append to the history, inside the document lock. The memory change is already
        applied: a failure here is raised, never swallowed, so a missing entry is visible."""
        if before == after:
            return
        entry = {
            "timestamp": self._now().isoformat(),
            "operation": operation.value,
            "section": section,
            "before": before,
            "after": after,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        await asyncio.to_thread(self._append, self._history_path(path), line)

    @staticmethod
    def _parse_change(reference: MemoryReference, line: str) -> MemoryChange:
        try:
            data = json.loads(line)
            return MemoryChange(
                reference=reference,
                operation=MemoryOperation(data["operation"]),
                section=data["section"],
                timestamp=datetime.fromisoformat(data["timestamp"]),
                before=data["before"],
                after=data["after"],
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ValidationError(f"corrupted memory history entry: {exc}") from exc

    @staticmethod
    def _append(path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="") as f:
            f.write(line)

    @staticmethod
    def _check_section(section: str) -> None:
        if not section.strip() or "\n" in section:
            raise ValidationError("section must be a non-empty single-line heading")

    def _lock(self, path: Path) -> asyncio.Lock:
        return self._locks.setdefault(path, asyncio.Lock())

    @staticmethod
    def _read(path: Path) -> str:
        try:
            with path.open(encoding="utf-8", newline="") as f:  # newline="": keep CRLF as is
                return f.read()
        except FileNotFoundError:
            return ""

    @staticmethod
    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            os.replace(tmp, path)  # atomic: readers never see a half-written file
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
