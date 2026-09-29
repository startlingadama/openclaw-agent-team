"""TelegramAdapter and TelegramApprovalPort on a fake client and a fake backend (no network)."""

import asyncio
from dataclasses import dataclass

import pytest

from openclaw.domain.agents.decision import CallTool, Finish
from openclaw.domain.messages.model import AgentMessage
from openclaw.domain.shared.errors import ApprovalError, AuthenticationError
from openclaw.domain.tasks.approval import Approval, ApprovalStatus
from openclaw.domain.tools.model import RiskLevel, ToolCall
from openclaw.infrastructure.channels.telegram import (
    TelegramAdapter,
    TelegramApprovalPort,
    TelegramConfig,
    TelegramError,
    chat_scope,
    render_outcome,
)
from openclaw.infrastructure.channels.telegram.messages import OutboundMessage
from tests.unit.fakes import Harness, make_agent

CHAT, USER, OTHER = 500, 42, 43
CONFIG = TelegramConfig("t", frozenset({USER, OTHER}), team="default", approval_timeout=5.0)


@dataclass
class Outcome:
    status: str = "completed"
    answer: str | None = "done"
    error: str | None = None


class FakeClient:
    def __init__(self, updates=()):
        self.sent, self.edits, self.callbacks, self.actions = [], [], [], []
        self._updates = list(updates)
        self.fail_send = False
        self.refuse_html = False
        self.modes = []

    async def get_me(self):
        return {"username": "testbot"}

    async def get_updates(self, offset, timeout):
        if not self._updates:
            raise asyncio.CancelledError
        item = self._updates.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def send_message(self, chat_id, text, *, reply_markup=None, parse_mode=None):
        if self.fail_send:
            raise TelegramError("boom")
        if parse_mode == "HTML" and self.refuse_html:
            raise TelegramError("sendMessage: Bad Request: can't parse entities: nope")
        self.modes.append(parse_mode)
        self.sent.append((chat_id, text, reply_markup))
        return {"message_id": len(self.sent)}

    async def edit_message_text(self, chat_id, message_id, text):
        self.edits.append((chat_id, message_id, text))

    async def send_chat_action(self, chat_id, action="typing"):
        self.actions.append(chat_id)

    async def answer_callback_query(self, callback_id, text=""):
        self.callbacks.append((callback_id, text))

    def texts(self):
        return [text for _, text, _ in self.sent]


class FakeBackend:
    def __init__(self, outcome=None, gate=None, action=None):
        self.outcome = outcome or Outcome()
        self.gate = gate
        self.action = action
        self.calls = []

    async def run_team(self, text, task_id):
        self.calls.append(("team", text, task_id))
        if self.gate:
            await self.gate.wait()
        if self.action:
            await self.action()
        return self.outcome

    async def run_agent(self, agent_id, text, task_id):
        self.calls.append((agent_id, text, task_id))
        return self.outcome

    async def agents(self):
        return [("ceo", "Supervisor"), ("writer", ""), ("bad", "invalid definition")]


def build(backend=None, client=None):
    client = client or FakeClient()
    approvals = TelegramApprovalPort(client, timeout=CONFIG.approval_timeout)
    backend = backend or FakeBackend()
    adapter = TelegramAdapter(
        client=client, backend=backend, approvals=approvals, config=CONFIG, sleep=_no_sleep
    )
    return adapter, client, backend, approvals


async def _no_sleep(_seconds):
    return None


def message(text, *, user=USER, chat=CHAT, chat_type="private", is_bot=False, update_id=1):
    return {
        "update_id": update_id,
        "message": {
            "message_id": 10,
            "from": {"id": user, "is_bot": is_bot},
            "chat": {"id": chat, "type": chat_type},
            "text": text,
        },
    }


async def settle(adapter):
    await asyncio.gather(*list(adapter._running.values()))


def approval(name="email.send"):
    return Approval("exec-1", "google-email", ToolCall(name, {"to": "a@b.c"}), RiskLevel.WRITE, "r")


# -- who is served ----------------------------------------------------------------------
def test_only_authorized_users_in_private_chats_are_served():
    adapter, client, backend, _ = build()

    async def go():
        await adapter.handle_update(message("hi", user=999))
        await adapter.handle_update(message("hi", chat_type="group"))
        await adapter.handle_update(message("hi", is_bot=True))
        await adapter.handle_update({"update_id": 2, "message": {"chat": {"id": 1}}})
        await adapter.handle_update(message("   "))
        await adapter.handle_update({"update_id": 3, "edited_message": {}})

    asyncio.run(go())
    assert backend.calls == [] and client.sent == []


def test_a_button_pressed_by_an_unauthorized_user_is_ignored():
    adapter, client, _, _ = build()
    asyncio.run(
        adapter.handle_update(
            {"callback_query": {"id": "c", "from": {"id": 999}, "data": "ap:x:y"}}
        )
    )
    assert client.callbacks == []


# -- routing ----------------------------------------------------------------------------
def test_free_text_goes_to_the_team_and_the_answer_comes_back():
    adapter, client, backend, _ = build()

    async def go():
        await adapter.handle_update(message("compare A and B"))
        await settle(adapter)

    asyncio.run(go())
    kind, text, task_id = backend.calls[0]
    assert (kind, text) == ("team", "compare A and B") and task_id
    assert client.texts() == ["done"]


def test_the_markdown_of_an_answer_is_sent_as_telegram_html():
    adapter, client, _, _ = build()
    asyncio.run(adapter.deliver(OutboundMessage(CHAT, "## Done\n**42** < 43")))
    assert client.texts() == ["<b>Done</b>\n<b>42</b> &lt; 43"] and client.modes == ["HTML"]


def test_a_refused_formatting_is_sent_again_as_plain_text():
    adapter, client, _, _ = build()
    client.refuse_html = True
    asyncio.run(adapter.deliver(OutboundMessage(CHAT, "## Done")))
    assert client.texts() == ["## Done"] and client.modes == [None]


def test_run_gives_the_task_to_one_agent():
    adapter, client, backend, _ = build()

    async def go():
        await adapter.handle_update(message("/run writer draft a note\nwith two lines"))
        await settle(adapter)

    asyncio.run(go())
    assert backend.calls[0][:2] == ("writer", "draft a note\nwith two lines")


def test_run_without_a_task_shows_the_usage():
    adapter, client, backend, _ = build()
    asyncio.run(adapter.handle_update(message("/run writer")))
    assert backend.calls == [] and "Usage: /run" in client.texts()[0]


def test_help_agents_and_unknown_commands():
    adapter, client, backend, _ = build()

    async def go():
        for text in ("/start", "/help", "/agents", "/nope"):
            await adapter.handle_update(message(text))

    asyncio.run(go())
    texts = client.texts()
    assert "/run &lt;agent&gt; &lt;task&gt;" in texts[0] and texts[0] == texts[1]
    assert texts[2] == "ceo - Supervisor\nwriter\nbad - invalid definition"
    assert "Unknown command" in texts[3]
    assert backend.calls == []


def test_a_second_task_in_a_busy_chat_is_refused_then_accepted_when_free():
    gate_holder = {}

    async def go():
        gate_holder["gate"] = asyncio.Event()
        adapter, client, backend, _ = build(FakeBackend(gate=gate_holder["gate"]))
        await adapter.handle_update(message("first"))
        await asyncio.sleep(0)
        await adapter.handle_update(message("second"))
        assert "already running" in client.texts()[-1]
        assert [c[1] for c in backend.calls] == ["first"]
        gate_holder["gate"].set()
        await settle(adapter)
        await asyncio.sleep(0)
        await adapter.handle_update(message("third"))
        await settle(adapter)
        return backend.calls

    calls = asyncio.run(go())
    assert [c[1] for c in calls] == ["first", "third"]


def test_a_task_that_crashes_is_reported_without_details():
    class Crash(FakeBackend):
        async def run_team(self, text, task_id):
            raise RuntimeError("secret detail")

    adapter, client, _, _ = build(Crash())

    async def go():
        await adapter.handle_update(message("x"))
        await settle(adapter)

    asyncio.run(go())
    assert "Internal error" in client.texts()[0] and "secret" not in client.texts()[0]


def test_a_long_answer_is_sent_in_several_messages():
    adapter, client, _, _ = build(FakeBackend(Outcome(answer="word " * 3000)))

    async def go():
        await adapter.handle_update(message("x"))
        await settle(adapter)

    asyncio.run(go())
    assert len(client.sent) > 1 and all(len(t) <= 4000 for t in client.texts())


def test_typing_is_shown_while_the_task_runs():
    gate = {}

    async def go():
        gate["g"] = asyncio.Event()
        adapter, client, _, _ = build(FakeBackend(gate=gate["g"]))
        await adapter.handle_update(message("x"))
        await asyncio.sleep(0.01)
        gate["g"].set()
        await settle(adapter)
        return client.actions

    assert asyncio.run(go()) == [CHAT]


def test_render_outcome():
    assert render_outcome(Outcome()) == "done"
    assert "without an answer" in render_outcome(Outcome(answer=None))
    assert render_outcome(Outcome("failed", None, "LLM down")) == "The task failed: LLM down"
    partial = render_outcome(Outcome("failed", "partial", "too many steps"))
    assert partial == "partial\n\nIncomplete: too many steps"


# -- approvals --------------------------------------------------------------------------
def press(approvals, adapter, client, approve, user=USER):
    _, _, markup = client.sent[-1]
    data = markup["inline_keyboard"][0][0 if approve else 1]["callback_data"]
    return adapter.handle_update(
        {"callback_query": {"id": "cb", "from": {"id": user}, "data": data}}
    )


def test_an_approval_reaches_the_chat_and_the_button_decides():
    adapter, client, _, approvals = build()

    async def go():
        with chat_scope(CHAT, USER):
            pending = asyncio.create_task(approvals.request(approval()))
            await asyncio.sleep(0)
            chat_id, text, markup = client.sent[0]
            assert chat_id == CHAT and "email.send" in text and "a@b.c" in text
            assert len(markup["inline_keyboard"][0]) == 2
            await press(approvals, adapter, client, True)
            return await pending

    result = asyncio.run(go())
    assert result.status is ApprovalStatus.APPROVED
    assert client.callbacks == [("cb", "Approved.")]
    assert client.edits[0][2].endswith("-> Approved")


def test_a_rejection_is_a_rejection():
    adapter, client, _, approvals = build()

    async def go():
        with chat_scope(CHAT, USER):
            pending = asyncio.create_task(approvals.request(approval()))
            await asyncio.sleep(0)
            await press(approvals, adapter, client, False)
            return await pending

    result = asyncio.run(go())
    assert result.status is ApprovalStatus.REJECTED and result.comment


def test_no_answer_in_time_is_a_rejection():
    client = FakeClient()
    approvals = TelegramApprovalPort(client, timeout=0.05)

    async def go():
        with chat_scope(CHAT, USER):
            return await approvals.request(approval())

    result = asyncio.run(go())
    assert result.status is ApprovalStatus.REJECTED
    assert "Expired" in client.edits[0][2]


def test_another_user_cannot_answer_and_a_late_press_is_refused():
    adapter, client, _, approvals = build()

    async def go():
        with chat_scope(CHAT, USER):
            pending = asyncio.create_task(approvals.request(approval()))
            await asyncio.sleep(0)
            await press(approvals, adapter, client, True, user=OTHER)
            assert client.callbacks[-1][1] == "This request is not yours."
            assert not pending.done()
            await press(approvals, adapter, client, True)
            result = await pending
            await press(approvals, adapter, client, True)  # the request is over
            return result

    result = asyncio.run(go())
    assert result.status is ApprovalStatus.APPROVED
    assert client.callbacks[-1][1] == "This request is no longer pending."


def test_two_requests_of_one_execution_have_their_own_ids():
    adapter, client, _, approvals = build()

    async def go():
        with chat_scope(CHAT, USER):
            first = asyncio.create_task(approvals.request(approval()))
            second = asyncio.create_task(approvals.request(approval("email.reply")))
            await asyncio.sleep(0)
            ids = [m[2]["inline_keyboard"][0][0]["callback_data"] for m in client.sent]
            assert ids[0] != ids[1]
            await press(approvals, adapter, client, True)  # answers the second one only
            assert not first.done() and (await second).status is ApprovalStatus.APPROVED
            first.cancel()

    asyncio.run(go())


def test_without_a_chat_or_when_the_message_cannot_be_sent_the_request_fails():
    client = FakeClient()
    approvals = TelegramApprovalPort(client, timeout=1)
    with pytest.raises(ApprovalError, match="no Telegram chat"):
        asyncio.run(approvals.request(approval()))
    client.fail_send = True

    async def go():
        with chat_scope(CHAT, USER):
            await approvals.request(approval())

    with pytest.raises(ApprovalError, match="could not ask"):
        asyncio.run(go())
    assert approvals._pending == {}


def test_the_real_runtime_asks_the_chat_that_started_the_task():
    """Through the ReAct runtime: the approval of a tool call reaches the right chat."""
    client = FakeClient()
    approvals = TelegramApprovalPort(client, timeout=5)
    h = Harness([CallTool(ToolCall("send", {})), Finish("sent")], approvals=approvals)
    from tests.unit.test_runtime import call  # noqa: F401  (same fake tool setup)

    h.tools.add("send", RiskLevel.WRITE)
    agent = make_agent(approval_required=["send"])
    adapter = TelegramAdapter(
        client=client, backend=FakeBackend(), approvals=approvals, config=CONFIG, sleep=_no_sleep
    )

    async def go():
        with chat_scope(CHAT, USER):
            run = asyncio.create_task(
                h.runtime.run(agent, AgentMessage("user", agent.id, "task-1", "go"))
            )
            await asyncio.sleep(0.01)
            assert client.sent and client.sent[0][0] == CHAT
            await press(approvals, adapter, client, True)
            return await run

    execution = asyncio.run(go())
    assert execution.answer == "sent" and len(h.tools.calls) == 1


def test_cancelling_a_waiting_run_leaves_no_pending_request():
    client = FakeClient()
    approvals = TelegramApprovalPort(client, timeout=5)

    async def go():
        with chat_scope(CHAT, USER):
            pending = asyncio.create_task(approvals.request(approval()))
            await asyncio.sleep(0)
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(go())
    assert approvals._pending == {}


# -- polling ----------------------------------------------------------------------------
def test_the_polling_loop_dispatches_updates_and_moves_the_offset():
    offsets = []
    client = FakeClient([[message("a", update_id=7), message("b", update_id=8)], []])
    original = client.get_updates

    async def spy(offset, timeout):
        offsets.append(offset)
        return await original(offset, timeout)

    client.get_updates = spy
    adapter, _, backend, _ = build(client=client)

    async def go():
        with pytest.raises(asyncio.CancelledError):
            await adapter.run()
        await asyncio.gather(*list(adapter._running.values()), return_exceptions=True)

    asyncio.run(go())
    assert offsets == [None, 9, 9]
    assert [c[1] for c in backend.calls] == ["a"]  # the chat was busy for "b"


def test_a_polling_failure_is_retried_with_a_growing_delay():
    delays = []

    async def record(seconds):
        delays.append(seconds)

    client = FakeClient(
        [TelegramError("net"), TelegramError("net"), TelegramError("net", retry_after=9)]
    )
    adapter = TelegramAdapter(
        client=client,
        backend=FakeBackend(),
        approvals=TelegramApprovalPort(client, timeout=1),
        config=CONFIG,
        sleep=record,
    )

    async def go():
        with pytest.raises(asyncio.CancelledError):
            await adapter.run()

    asyncio.run(go())
    assert delays == [2.0, 4.0, 9]


def test_a_rejected_token_stops_the_bot():
    client = FakeClient([AuthenticationError("bad token")])
    adapter, _, _, _ = build(client=client)
    with pytest.raises(AuthenticationError):
        asyncio.run(adapter.run())


def test_a_failing_update_does_not_stop_the_loop():
    client = FakeClient(
        [[{"update_id": 1, "callback_query": {"from": {"id": USER}, "data": "ap:x:y"}}]]
    )

    async def boom(*_a, **_k):
        raise RuntimeError("x")

    client.answer_callback_query = boom
    adapter, _, _, _ = build(client=client)

    async def go():
        with pytest.raises(asyncio.CancelledError):
            await adapter.run()

    asyncio.run(go())


def test_closing_cancels_the_running_tasks():
    async def go():
        adapter, client, _, _ = build(FakeBackend(gate=asyncio.Event()))
        await adapter.handle_update(message("x"))
        await asyncio.sleep(0)
        await adapter.aclose()
        return adapter._running, client.texts()

    running, texts = asyncio.run(go())
    assert running == {} and texts == []
