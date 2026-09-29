"""Which team a run belongs to, and whether it is already inside a delegation.

Both live in context variables: they follow the asynchronous call chain of one run and never leak
into a concurrent one. An LLM cannot set them: only the application code around a run can.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_active_team: ContextVar[str | None] = ContextVar("openclaw_active_team", default=None)
_delegating: ContextVar[bool] = ContextVar("openclaw_delegating", default=False)


@contextmanager
def team_scope(team_id: str) -> Iterator[None]:
    """Runs started inside this block delegate within `team_id` instead of the default team."""
    token = _active_team.set(team_id)
    try:
        yield
    finally:
        _active_team.reset(token)


def active_team() -> str | None:
    return _active_team.get()


def is_delegating() -> bool:
    return _delegating.get()


@contextmanager
def delegating() -> Iterator[None]:
    token = _delegating.set(True)
    try:
        yield
    finally:
        _delegating.reset(token)
