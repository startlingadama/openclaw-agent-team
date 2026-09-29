"""Skill loader: Agent Skills directories on disk, with progressive disclosure (ADR-007).

Layout (REQUIREMENTS sections 6 and 24), the skill id being the path under the skills root:

    skills/github/repository-analysis/SKILL.md   -> "github/repository-analysis"
    skills/new-skill/SKILL.md                    -> "new-skill"
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from pathlib import Path

import yaml

from openclaw.domain.shared.errors import SkillError
from openclaw.domain.skills.model import Skill, SkillId, SkillMetadata

SKILL_FILE = "SKILL.md"
REQUIRED_SECTIONS = ("Purpose", "When to use", "Procedure", "Constraints", "Expected Output")
_OPTIONAL_DIRS = ("scripts", "references", "assets")
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_HEADING = re.compile(r"^##\s+(.+?)\s*$")


class SkillLoader:
    """Implements the SkillRepository port. Nothing is cached: files stay the source of truth."""

    def __init__(self, skills_dir: Path | str) -> None:
        self._root = Path(skills_dir)

    async def discover(self) -> list[SkillMetadata]:
        """Registry view: every skill under the root, sorted by id. A broken skill is an error."""
        return await asyncio.to_thread(self._discover)

    async def list_metadata(self, skill_ids: Sequence[str]) -> list[SkillMetadata]:
        """Name and description only: the full SKILL.md is not returned."""
        return [await asyncio.to_thread(self._metadata, sid) for sid in skill_ids]

    async def load(self, skill_id: str) -> Skill:
        """The complete SKILL.md, loaded only once the agent selects the skill."""
        return await asyncio.to_thread(self._load, skill_id)

    async def file_catalog(self, skill_id: str) -> list[dict[str, str]]:
        """List real skill files and sizes without eagerly loading their contents."""
        return await asyncio.to_thread(self._file_catalog, skill_id)

    # -- internals ------------------------------------------------------------------------
    def _directory(self, skill_id: str) -> Path:
        segments = skill_id.split("/")
        if not all(_SEGMENT.match(s) for s in segments):  # blocks path traversal
            raise SkillError(f"invalid skill id: {skill_id!r}")
        directory = self._root.joinpath(*segments)
        if not (directory / SKILL_FILE).is_file():
            raise SkillError(f"unknown skill: {skill_id}")
        return directory

    def _discover(self) -> list[SkillMetadata]:
        if not self._root.is_dir():
            return []
        ids = sorted(
            f.parent.relative_to(self._root).as_posix()
            for f in self._root.rglob(SKILL_FILE)
            if not any(part.startswith(".") for part in f.relative_to(self._root).parts)
        )
        return [self._metadata(skill_id) for skill_id in ids]

    def _metadata(self, skill_id: str) -> SkillMetadata:
        directory = self._directory(skill_id)
        front, _ = _split(directory / SKILL_FILE, skill_id)
        return _metadata_from(front, skill_id)

    def _load(self, skill_id: str) -> Skill:
        directory = self._directory(skill_id)
        front, body = _split(directory / SKILL_FILE, skill_id)
        metadata = _metadata_from(front, skill_id)
        missing = [s for s in REQUIRED_SECTIONS if s.lower() not in _sections(body)]
        if missing:
            raise SkillError(f"skill {skill_id} is missing sections: {', '.join(missing)}")
        found = {name: _files(directory / name) for name in _OPTIONAL_DIRS}
        return Skill(metadata, body, found["scripts"], found["references"], found["assets"])

    def _file_catalog(self, skill_id: str) -> list[dict[str, str]]:
        directory = self._directory(skill_id)
        paths = [directory / SKILL_FILE]
        for name in _OPTIONAL_DIRS:
            paths.extend(directory / relative for relative in _files(directory / name))
        catalog = []
        for path in paths:
            relative = path.relative_to(directory).as_posix()
            if relative == SKILL_FILE:
                kind = "instruction"
            elif relative.startswith("scripts/"):
                kind = "script"
            elif relative.startswith("references/"):
                kind = "reference"
            else:
                kind = "asset"
            size_kb = path.stat().st_size / 1024
            catalog.append(
                {
                    "path": relative,
                    "kind": kind,
                    "size": f"{size_kb:.1f} KB" if size_kb else "<0.1 KB",
                }
            )
        return sorted(catalog, key=lambda item: item["path"])


def _split(path: Path, skill_id: str) -> tuple[object, str]:
    """Split a SKILL.md into its parsed YAML frontmatter and its Markdown body."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError(f"skill {skill_id}: SKILL.md must start with a YAML frontmatter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise SkillError(f"skill {skill_id}: frontmatter is not closed by '---'") from None
    try:
        front = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise SkillError(f"skill {skill_id}: invalid frontmatter: {exc}") from exc
    return front, "\n".join(lines[end + 1 :]).strip()


def _metadata_from(front: object, skill_id: str) -> SkillMetadata:
    fields = front if isinstance(front, dict) else {}
    name, description = fields.get("name"), fields.get("description")
    for key, value in (("name", name), ("description", description)):
        if not isinstance(value, str) or not value.strip():
            raise SkillError(f"skill {skill_id}: frontmatter needs a non-empty '{key}'")
    return SkillMetadata(SkillId(skill_id), name.strip(), description.strip())


def _sections(body: str) -> set[str]:
    titles: set[str] = set()
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        elif not in_fence and (m := _HEADING.match(line)):
            titles.add(m.group(1).lower())
    return titles


def _files(directory: Path) -> tuple[str, ...]:
    """Relative paths only (contents are never read here); hidden files are skipped."""
    if not directory.is_dir():
        return ()
    root = directory.parent
    return tuple(
        sorted(
            p.relative_to(root).as_posix()
            for p in directory.rglob("*")
            if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts)
        )
    )
