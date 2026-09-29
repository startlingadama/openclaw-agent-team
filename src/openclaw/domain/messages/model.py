"""Agent-to-agent messages (ARCHITECTURE section 11)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentMessage:
    sender: str
    recipient: str
    task_id: str
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
