import pytest

from openclaw.domain.shared.errors import ValidationError
from openclaw.domain.tools.model import RiskLevel, ToolCall, ToolSpec
from openclaw.domain.tools.permissions import ToolPermissions
from openclaw.domain.tools.policy import PolicyEngine, PolicyOutcome

engine = PolicyEngine()


def spec(risk=RiskLevel.READ):
    return ToolSpec("t", "d", risk_level=risk)


def test_unpermitted_tool_is_denied():
    d = engine.evaluate(ToolPermissions.of(), spec(), ToolCall("t"))
    assert d.outcome is PolicyOutcome.DENY


def test_unknown_tool_is_denied():
    d = engine.evaluate(ToolPermissions.of(["t"]), None, ToolCall("t"))
    assert d.outcome is PolicyOutcome.DENY


def test_read_tool_is_allowed():
    d = engine.evaluate(ToolPermissions.of(["t"]), spec(), ToolCall("t"))
    assert d.outcome is PolicyOutcome.ALLOW


def test_write_tool_allowed_unless_listed():
    perms = ToolPermissions.of(["t"])
    assert (
        engine.evaluate(perms, spec(RiskLevel.WRITE), ToolCall("t")).outcome is PolicyOutcome.ALLOW
    )
    perms = ToolPermissions.of(approval_required=["t"])
    outcome = engine.evaluate(perms, spec(RiskLevel.WRITE), ToolCall("t")).outcome
    assert outcome is PolicyOutcome.REQUIRE_APPROVAL


@pytest.mark.parametrize("risk", [RiskLevel.DESTRUCTIVE, RiskLevel.EXTERNAL_COMMUNICATION])
def test_risky_tools_always_need_approval(risk):
    d = engine.evaluate(ToolPermissions.of(["t"]), spec(risk), ToolCall("t"))
    assert d.outcome is PolicyOutcome.REQUIRE_APPROVAL


def test_missing_required_argument_is_a_validation_error():
    s = ToolSpec("t", "d", {"required": ["q"]})
    with pytest.raises(ValidationError):
        s.validate_arguments({})
    s.validate_arguments({"q": 1})


def test_optional_is_a_marker_on_permitted_tools_only():
    perms = ToolPermissions.of(["a"], ["b"], optional=["b"])
    assert perms.is_optional("b") and not perms.is_optional("a")
    assert perms.permits("b") and perms.lists_approval("b")  # still permitted, still approved
    assert not ToolPermissions.of(["a"]).is_optional("a")  # nothing is optional by default


def test_an_optional_tool_that_is_not_permitted_is_a_domain_error():
    with pytest.raises(ValueError, match="optional tools must also be permitted"):
        ToolPermissions.of(["a"], optional=["z"])


def test_optional_does_not_let_a_call_through_when_the_tool_is_unknown():
    d = PolicyEngine().evaluate(ToolPermissions.of(["t"], optional=["t"]), None, ToolCall("t"))
    assert d.outcome is PolicyOutcome.DENY
