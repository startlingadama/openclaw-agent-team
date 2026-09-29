"""WebChatApprovalController: each request has its own id, even inside one execution."""

import asyncio

import pytest

from openclaw.domain.tasks.approval import Approval, ApprovalStatus
from openclaw.domain.tools.model import RiskLevel, ToolCall
from openclaw.infrastructure.channels.webchat import WebChatApprovalController


def approval(tool="code.execute", execution_id="exec-1"):
    return Approval(execution_id, "code-executor", ToolCall(tool), RiskLevel.WRITE, "why")


async def wait_pending(controller, count):
    for _ in range(200):
        if len(controller.pending()) >= count:
            return controller.pending()
        await asyncio.sleep(0.01)
    raise AssertionError("approval never became pending")


def test_two_approvals_of_one_execution_one_after_the_other_have_different_ids():
    async def go():
        controller = WebChatApprovalController()
        controller.register_execution("exec-1", "task-9")
        ids = []
        for tool in ("code.execute", "code.terminal"):
            waiting = asyncio.create_task(controller.request(approval(tool)))
            (pending,) = await wait_pending(controller, 1)
            assert pending["action"] == tool and pending["taskId"] == "task-9"
            assert pending["executionId"] == "exec-1"
            ids.append(pending["id"])
            controller.resolve(pending["id"], True)
            assert (await waiting).status is ApprovalStatus.APPROVED
            assert controller.pending() == []
        assert ids[0] != ids[1] and "exec-1" not in ids

    asyncio.run(go())


def test_two_approvals_pending_together_do_not_overwrite_each_other():
    async def go():
        controller = WebChatApprovalController()
        first = asyncio.create_task(controller.request(approval("code.execute")))
        second = asyncio.create_task(controller.request(approval("code.terminal")))
        pending = await wait_pending(controller, 2)
        by_action = {p["action"]: p["id"] for p in pending}
        assert len(set(by_action.values())) == 2
        controller.resolve(by_action["code.terminal"], False, "no")
        controller.resolve(by_action["code.execute"], True)
        assert (await first).status is ApprovalStatus.APPROVED
        assert (await second).status is ApprovalStatus.REJECTED

    asyncio.run(go())


def test_resolving_an_unknown_or_already_answered_id_is_an_error():
    controller = WebChatApprovalController()
    with pytest.raises(KeyError):
        controller.resolve("exec-1", True)  # the execution id is no longer an approval id
