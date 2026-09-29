"""Skill domain model (ARCHITECTURE section 7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

SkillId = NewType("SkillId", str)


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    """What is loaded at initialization (progressive disclosure, ADR-007)."""

    id: SkillId  # e.g. "github/repository-analysis"
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class Skill:
    metadata: SkillMetadata
    instructions: str
    scripts: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    assets: tuple[str, ...] = ()
