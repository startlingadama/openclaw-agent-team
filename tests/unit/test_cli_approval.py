import asyncio

import pytest

from openclaw.domain.shared.errors import ApprovalError
from openclaw.domain.tasks.approval import Approval, ApprovalStatus
from openclaw.domain.tools.model import RiskLevel, ToolCall
from openclaw.infrastructure.channels.cli.approval import CliApprovalPort


def approval():
    call = ToolCall("github.create_issue", {"title": "t"})
    return Approval("e1", "github", call, RiskLevel.WRITE, "why")


def ask(port):
    return asyncio.run(port.request(approval()))


@pytest.mark.parametrize("answer", ["y", "YES", " oui "])
def test_yes_approves(answer):
    result = ask(CliApprovalPort(ask=lambda _: answer, interactive=lambda: True))
    assert result.status is ApprovalStatus.APPROVED


@pytest.mark.parametrize("answer", ["", "n", "no", "maybe"])
def test_anything_else_rejects(answer):
    result = ask(CliApprovalPort(ask=lambda _: answer, interactive=lambda: True))
    assert result.status is ApprovalStatus.REJECTED


def test_without_a_terminal_nothing_is_approved():
    asked = []
    port = CliApprovalPort(ask=lambda p: asked.append(p) or "y", interactive=lambda: False)
    with pytest.raises(ApprovalError):  # the runtime turns this into a rejection
        ask(port)
    assert asked == []


def test_the_request_is_shown_on_stderr(capsys):
    ask(CliApprovalPort(ask=lambda _: "n", interactive=lambda: True))
    err = capsys.readouterr().err
    assert "github.create_issue" in err and '"title": "t"' in err


def test_the_request_names_the_agent_that_asks(capsys):
    call = ToolCall("github.create_issue", {"title": "t"})
    sub_agent = Approval("e2", "github", call, RiskLevel.WRITE, "why")
    port = CliApprovalPort(ask=lambda _: "n", interactive=lambda: True)
    asyncio.run(port.request(sub_agent))
    assert "Approval needed from [github]: github.create_issue" in capsys.readouterr().err
