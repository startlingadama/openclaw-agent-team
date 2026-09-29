"""Confinement of document paths to one directory (ADR-026).

Shared by the document tools and by the WebChat download and listing routes: all receive paths
that come from an LLM or from a URL, so all go through the same checks. A path is relative to the
documents directory; it is refused when it is absolute, holds `..` or a dotfile, has another
extension than the ones asked for, or resolves (symbolic links included) outside the directory.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from openclaw.domain.shared.errors import ValidationError

DOWNLOAD_EXTENSIONS = frozenset({".md", ".pdf", ".tex"})
MAX_LISTED = 2000  # a listing never walks or returns more documents than this


@dataclass(frozen=True, slots=True)
class DocumentEntry:
    """One document of the directory, as the listing and the task results show it."""

    path: str  # relative to the documents directory, always with `/`
    name: str
    kind: str  # "md", "tex" or "pdf"
    size: int  # bytes
    modified: float  # POSIX timestamp


class DocumentFiles:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def resolve(self, relative: str, extensions: Iterable[str]) -> Path:
        """The path inside the directory, or a ValidationError."""
        if os.path.isabs(relative) or "\x00" in relative or "\\" in relative:
            raise ValidationError("path must be relative to the documents directory")
        parts = Path(relative).parts
        if not parts or ".." in parts or any(p.startswith(".") for p in parts):
            raise ValidationError("path must stay inside the documents directory")
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValidationError("path leaves the documents directory")
        if path.suffix.lower() not in set(extensions):
            raise ValidationError("this type of document is not handled")
        return path

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def downloadable(self, relative: str) -> Path | None:
        """The Markdown, LaTeX or PDF file to serve, or None (missing, refused, outside)."""
        try:
            path = self.resolve(relative, DOWNLOAD_EXTENSIONS)
        except ValidationError:
            return None
        return path if path.is_file() else None

    def describe(self, relative: str) -> DocumentEntry | None:
        """The entry of a document that may be downloaded, or None."""
        path = self.downloadable(relative)
        if path is None:
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        return DocumentEntry(
            path=self.relative(path),
            name=path.name,
            kind=path.suffix.lower().lstrip("."),
            size=stat.st_size,
            modified=stat.st_mtime,
        )

    def listing(self) -> list[DocumentEntry]:
        """Every document that may be downloaded, newest first.

        Nothing is followed out of the directory: symbolic links, dotfiles and dot-directories
        are skipped, and only the extensions of `DOWNLOAD_EXTENSIONS` are listed (the LaTeX
        auxiliary files stay out). It goes through `describe`, so a document is listed exactly
        when it can be downloaded.
        """
        found: list[DocumentEntry] = []
        if not self.root.is_dir():
            return found
        for current, dirs, names in os.walk(self.root, followlinks=False):
            base = Path(current)
            dirs[:] = sorted(
                d for d in dirs if not d.startswith(".") and not (base / d).is_symlink()
            )
            for name in sorted(names):
                if name.startswith(".") or (base / name).is_symlink():
                    continue
                entry = self.describe((base / name).relative_to(self.root).as_posix())
                if entry is None:
                    continue
                found.append(entry)
                if len(found) >= MAX_LISTED:
                    return sorted(found, key=_newest_first)
        return sorted(found, key=_newest_first)


def _newest_first(entry: DocumentEntry) -> tuple[float, str]:
    return (-entry.modified, entry.path)
