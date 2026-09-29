"""The dedicated tool through which an agent writes its own memory."""

from __future__ import annotations

from typing import Any

from openclaw.domain.agents.model import AgentId
from openclaw.domain.memory.model import MemoryLayer, MemoryReference
from openclaw.domain.memory.ports import MemoryRepository
from openclaw.domain.shared.errors import ToolError, ValidationError
from openclaw.domain.tools.model import RiskLevel, ToolCall, ToolSpec

MEMORY_UPDATE = "memory.update"

_LAYERS = {"agent": MemoryLayer.AGENT, "user": MemoryLayer.USER}

_SPEC = ToolSpec(
    name=MEMORY_UPDATE,
    description=(
        "Save durable information in your own Markdown memory. Replaces the body of the "
        "'## section' (created if missing), so include what you want to keep. "
        "layer 'agent' is MEMORY.md, layer 'user' is USER.md. Never store secrets."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "layer": {"type": "string", "enum": list(_LAYERS), "default": "agent"},
            "section": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["section", "content"],
    },
    output_schema={"type": "string"},
    risk_level=RiskLevel.WRITE,
)


class MemoryToolProvider:
    """A ToolPort provider. The memory owner is always the caller, never an LLM argument
    (REQUIREMENTS section 16: agents MUST NOT manipulate another agent's memory)."""

    tool_names = frozenset({MEMORY_UPDATE})

    def __init__(self, repository: MemoryRepository) -> None:
        self._repository = repository

    def get_spec(self, name: str) -> ToolSpec | None:
        return _SPEC if name == MEMORY_UPDATE else None

    async def execute(self, call: ToolCall, caller: AgentId) -> Any:
        if call.name != MEMORY_UPDATE:
            raise ToolError(f"unknown memory tool: {call.name}")
        section, content = call.arguments.get("section"), call.arguments.get("content")
        layer = call.arguments.get("layer", "agent")
        if not isinstance(section, str) or not isinstance(content, str):
            raise ValidationError("'section' and 'content' must be strings")
        if layer not in _LAYERS:
            raise ValidationError(f"'layer' must be one of: {', '.join(_LAYERS)}")
        await self._repository.update(MemoryReference(_LAYERS[layer], caller), section, content)
        return f"Memory updated: {layer} / {section}"
