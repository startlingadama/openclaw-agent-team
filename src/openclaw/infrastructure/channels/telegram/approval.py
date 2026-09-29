"""Human approval in the Telegram chat (ADR-015): inline buttons, one request id each.

Which chat to ask comes from a context variable set around each run (`chat_scope`): it follows
the asynchronous call chain of that run, so an approval asked by a delegated agent or by a
script of the sandbox reaches the chat that started the task, and never another one.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from openclaw.domain.shared.errors import ApprovalError
from openclaw.domain.tasks.approval import Approval
from openclaw.infrastructure.channels.telegram.client import TelegramClient, TelegramError
from openclaw.infrastructure.channels.telegram.formatting import approval_keyboard, approval_text

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChatRef:
    chat_id: int
    user_id: int


_current_chat: ContextVar[ChatRef | None] = ContextVar("openclaw_telegram_chat", default=None)


@contextmanager
def chat_scope(chat_id: int, user_id: int) -> Iterator[None]:
    """Approvals asked by runs started inside this block go to that chat and that user."""
    token = _current_chat.set(ChatRef(chat_id, user_id))
    try:
        yield
    finally:
        _current_chat.reset(token)


@dataclass(slots=True)
class _Pending:
    user_id: int
    future: asyncio.Future[bool]


class TelegramApprovalPort:
    """Implements ApprovalPort. Not answered in time, or not askable: rejected (never approved)."""

    def __init__(self, client: TelegramClient, *, timeout: float) -> None:
        self._client = client
        self._timeout = timeout
        self._pending: dict[str, _Pending] = {}

    async def request(self, approval: Approval) -> Approval:
        chat = _current_chat.get()
        if chat is None:
            raise ApprovalError("no Telegram chat to ask for approval")
        approval_id = uuid.uuid4().hex  # one per request, never the execution's id
        text = approval_text(approval)
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[approval_id] = _Pending(chat.user_id, future)
        try:
            try:
                sent = await self._client.send_message(
                    chat.chat_id, text, reply_markup=approval_keyboard(approval_id)
                )
            except TelegramError as exc:
                raise ApprovalError(f"could not ask for approval on Telegram: {exc}") from exc
            message_id = sent.get("message_id")
            try:
                approved = await asyncio.wait_for(future, self._timeout)
            except TimeoutError:
                approved, verdict, comment = False, "Expired, treated as rejected", "no answer"
            else:
                verdict = "Approved" if approved else "Rejected"
                comment = "" if approved else "rejected on Telegram"
        finally:
            self._pending.pop(approval_id, None)
        if isinstance(message_id, int):
            try:
                await self._client.edit_message_text(
                    chat.chat_id, message_id, f"{text}\n\n-> {verdict}"
                )
            except TelegramError as exc:  # cosmetic: the decision is already taken
                log.warning("could not update the approval message: %s", exc)
        return approval.resolve(approved, comment)

    def answer(self, approval_id: str, approved: bool, *, user_id: int) -> str:
        """A button was pressed. Returns the short text shown to the user."""
        pending = self._pending.get(approval_id)
        if pending is None or pending.future.done():
            return "This request is no longer pending."
        if pending.user_id != user_id:
            return "This request is not yours."
        pending.future.set_result(approved)
        return "Approved." if approved else "Rejected."
