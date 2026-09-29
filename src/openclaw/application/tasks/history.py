"""Use case: look back at past executions and at what each one delegated."""

from __future__ import annotations

from dataclasses import dataclass

from openclaw.domain.tasks.history import ExecutionHistory, ExecutionRecord, ExecutionSummary


@dataclass(frozen=True, slots=True)
class ExecutionNode:
    """An execution and, under it, the executions its delegations started."""

    record: ExecutionRecord
    children: tuple[ExecutionNode, ...] = ()


class ReadHistory:
    def __init__(self, history: ExecutionHistory) -> None:
        self._history = history

    async def recent(self, limit: int = 20, *, delegations: bool = False) -> list[ExecutionSummary]:
        """Newest first. A delegation is part of its supervisor's run, so it is left out unless
        asked for."""
        return await self._history.summaries(limit, roots_only=not delegations)

    async def tree(self, ref: str) -> ExecutionNode:
        """The execution `ref` designates (any execution, not only a root) with its delegations
        below it, oldest first. Children are found by `parent_execution_id`, so they are still
        there when the supervisor's own summary was never finalized."""
        root = await self._history.find(ref)
        everything = await self._history.summaries()
        return await self._node(root, everything, {root.execution_id})

    async def _node(
        self, summary: ExecutionSummary, everything: list[ExecutionSummary], seen: set[str]
    ) -> ExecutionNode:
        record = await self._history.record(summary)
        below = sorted(
            (s for s in everything if s.parent_execution_id == summary.execution_id),
            key=lambda s: s.started_at,
        )
        children = []
        for child in below:
            if child.execution_id in seen:  # a corrupted trace must not loop
                continue
            seen.add(child.execution_id)
            children.append(await self._node(child, everything, seen))
        return ExecutionNode(record, tuple(children))
