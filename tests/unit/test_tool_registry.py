import asyncio

import pytest

from openclaw.domain.shared.errors import ToolError, ValidationError
from openclaw.domain.tools.model import RiskLevel, ToolCall, ToolSpec
from openclaw.domain.tools.registry import ToolRegistry


class Provider:
    def __init__(self, *names, mismatch=False):
        self.tool_names = frozenset(names)
        self.mismatch = mismatch
        self.calls = []

    def get_spec(self, name):
        if name not in self.tool_names:
            return None
        return ToolSpec("other" if self.mismatch else name, "d", risk_level=RiskLevel.READ)

    async def execute(self, call, caller):
        self.calls.append((call.name, caller))
        return f"{call.name} by {caller}"


def test_dispatches_each_tool_to_its_provider_with_the_caller():
    a, b = Provider("github.search_code"), Provider("web.search", "web.open")
    registry = ToolRegistry([a, b])
    assert registry.names == ("github.search_code", "web.open", "web.search")
    out = asyncio.run(registry.execute(ToolCall("web.open"), "research"))
    assert out == "web.open by research" and b.calls == [("web.open", "research")] and a.calls == []
    assert registry.get_spec("web.search").name == "web.search"


def test_unknown_tool():
    registry = ToolRegistry([Provider("a.b")])
    assert registry.get_spec("nope") is None
    with pytest.raises(ToolError):
        asyncio.run(registry.execute(ToolCall("nope"), "x"))


def test_duplicate_names_are_refused_and_register_nothing():
    registry = ToolRegistry([Provider("a.one")])
    with pytest.raises(ValidationError):
        registry.register(Provider("a.two", "a.one"))
    assert registry.names == ("a.one",)  # all-or-nothing


def test_provider_must_describe_what_it_declares():
    with pytest.raises(ValidationError):
        ToolRegistry([Provider("a.one", mismatch=True)])
