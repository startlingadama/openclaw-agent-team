"""TelegramAdapter (ARCHITECTURE section 16): Telegram updates -> InboundMessage -> the runtime
-> OutboundMessage -> Telegram. The agent core knows nothing about Telegram.

The adapter never imports the application layer: it talks to a `TelegramBackend` that the
composition root gives it.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from openclaw.domain.shared.errors import AuthenticationError
from openclaw.infrastructure.channels.telegram.approval import TelegramApprovalPort, chat_scope
from openclaw.infrastructure.channels.telegram.client import (
    MAX_DOCUMENT_BYTES,
    TelegramClient,
    TelegramConfig,
    TelegramError,
)
from openclaw.infrastructure.channels.telegram.formatting import (
    HELP_TEXT,
    parse_callback,
    parse_command,
    split_message,
    to_telegram_html,
)
from openclaw.infrastructure.channels.telegram.messages import InboundMessage, OutboundMessage

log = logging.getLogger(__name__)

TYPING_INTERVAL = 4.0  # Telegram shows "typing" for about 5 seconds
MAX_BACKOFF = 30.0


class RunOutcome(Protocol):
    """What the adapter reads of a finished run."""

    @property
    def status(self) -> str: ...

    @property
    def answer(self) -> str | None: ...

    @property
    def error(self) -> str | None: ...


class TelegramBackend(Protocol):
    async def run_team(self, text: str, task_id: str) -> RunOutcome:
        """Give the task to the supervisor of the configured team."""
        ...

    async def run_agent(self, agent_id: str, text: str, task_id: str) -> RunOutcome:
        """Give the task to one agent."""
        ...

    async def agents(self) -> Sequence[tuple[str, str]]:
        """(agent id, role label) of every defined agent."""
        ...


class SentDocument(Protocol):
    """One file to send to the chat."""

    @property
    def path(self) -> Path: ...

    @property
    def name(self) -> str: ...

    @property
    def size(self) -> int: ...


class TaskFiles(Protocol):
    def take(self, task_id: str) -> Sequence[SentDocument]:
        """The files the task produced and that may be sent (each task is served once)."""
        ...


_MIME_TYPES = {
    ".md": "text/markdown",
    ".tex": "application/x-tex",
    ".pdf": "application/pdf",
}


class TelegramAdapter:
    def __init__(
        self,
        *,
        client: TelegramClient,
        backend: TelegramBackend,
        approvals: TelegramApprovalPort,
        config: TelegramConfig,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        documents: TaskFiles | None = None,
    ) -> None:
        self._client = client
        self._backend = backend
        self._approvals = approvals
        self._config = config
        self._sleep = sleep
        self._documents = documents
        self._running: dict[int, asyncio.Task[None]] = {}  # one task at a time per chat

    # -- polling ------------------------------------------------------------------------
    async def run(self) -> None:
        """Poll until cancelled. A bad token stops it; a network or API failure is retried."""
        me = await self._client.get_me()
        log.info(
            "Telegram bot @%s started (%d authorized user(s), team '%s')",
            me.get("username", "?"),
            len(self._config.allowed_user_ids),
            self._config.team,
        )
        offset: int | None = None
        failures = 0
        while True:
            try:
                updates = await self._client.get_updates(offset, self._config.poll_timeout)
            except AuthenticationError:
                raise
            except TelegramError as exc:
                failures += 1
                delay = exc.retry_after or min(2.0**failures, MAX_BACKOFF)
                log.warning("Telegram polling failed (%s); retrying in %.0f s", exc, delay)
                await self._sleep(delay)
                continue
            failures = 0
            for update in updates:
                offset = int(update["update_id"]) + 1
                try:
                    await self.handle_update(update)
                except Exception:  # one bad update must not stop the bot
                    log.exception("Telegram update %s failed", update.get("update_id"))

    async def aclose(self) -> None:
        tasks = list(self._running.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._running.clear()

    # -- one update ---------------------------------------------------------------------
    async def handle_update(self, update: dict[str, Any]) -> None:
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            await self._on_callback(callback)
            return
        inbound = self.to_inbound(update)
        if inbound is not None:
            await self._on_message(inbound)

    def to_inbound(self, update: dict[str, Any]) -> InboundMessage | None:
        """The text message of an authorized user in a private chat; anything else is ignored."""
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        user_id, chat_id, text = sender.get("id"), chat.get("id"), message.get("text")
        if not isinstance(user_id, int) or not isinstance(chat_id, int):
            return None
        if not isinstance(text, str) or not text.strip():
            return None
        if chat.get("type") != "private" or sender.get("is_bot"):
            return None
        if user_id not in self._config.allowed_user_ids:
            log.warning("ignored a message from unauthorized Telegram user %s", user_id)
            return None
        return InboundMessage(chat_id, user_id, text.strip(), int(message.get("message_id") or 0))

    async def _on_message(self, inbound: InboundMessage) -> None:
        command, argument = parse_command(inbound.text)
        if command is None:
            await self._start(inbound, None, inbound.text)
        elif command in ("start", "help"):
            await self._reply(inbound.chat_id, HELP_TEXT)
        elif command == "agents":
            await self._reply(inbound.chat_id, await self._agents_text())
        elif command == "run":
            parts = argument.split(None, 1)
            if len(parts) < 2:
                await self._reply(inbound.chat_id, "Usage: /run <agent> <task>")
            else:
                await self._start(inbound, parts[0], parts[1].strip())
        else:
            await self._reply(inbound.chat_id, "Unknown command. /help lists the commands.")

    async def _agents_text(self) -> str:
        agents = await self._backend.agents()
        if not agents:
            return "No agent defined."
        return "\n".join(f"{agent_id} - {role}" if role else agent_id for agent_id, role in agents)

    async def _on_callback(self, callback: dict[str, Any]) -> None:
        user_id = (callback.get("from") or {}).get("id")
        if user_id not in self._config.allowed_user_ids:
            log.warning("ignored a button pressed by unauthorized Telegram user %s", user_id)
            return
        parsed = parse_callback(callback.get("data"))
        if parsed is None:
            text = "Unknown action."
        else:
            approval_id, approved = parsed
            text = self._approvals.answer(approval_id, approved, user_id=user_id)
        try:
            await self._client.answer_callback_query(str(callback.get("id", "")), text)
        except TelegramError as exc:
            log.warning("could not answer a button press: %s", exc)

    # -- running a task -----------------------------------------------------------------
    async def _start(self, inbound: InboundMessage, agent_id: str | None, text: str) -> None:
        """Run in the background: the polling loop must stay free to receive the approvals."""
        current = self._running.get(inbound.chat_id)
        if current is not None and not current.done():
            await self._reply(
                inbound.chat_id, "A task is already running in this chat. Wait for its answer."
            )
            return
        task = asyncio.create_task(
            self._run(inbound, agent_id, text), name=f"telegram-chat-{inbound.chat_id}"
        )
        self._running[inbound.chat_id] = task
        task.add_done_callback(lambda done, chat_id=inbound.chat_id: self._forget(chat_id, done))

    def _forget(self, chat_id: int, task: asyncio.Task[None]) -> None:
        if self._running.get(chat_id) is task:
            del self._running[chat_id]

    async def _run(self, inbound: InboundMessage, agent_id: str | None, text: str) -> None:
        task_id = uuid.uuid4().hex
        typing = asyncio.create_task(self._keep_typing(inbound.chat_id))
        try:
            with chat_scope(inbound.chat_id, inbound.user_id):
                if agent_id is None:
                    outcome = await self._backend.run_team(text, task_id)
                else:
                    outcome = await self._backend.run_agent(agent_id, text, task_id)
            reply = render_outcome(outcome)
        except Exception:
            log.exception("Telegram task %s failed", task_id)
            reply = "Internal error: the task could not be run. See the server log."
        finally:
            typing.cancel()
        await self._reply(inbound.chat_id, reply)
        await self._send_documents(inbound.chat_id, task_id)

    async def _send_documents(self, chat_id: int, task_id: str) -> None:
        """The files the task produced, after its answer. A file that cannot be sent is named
        in the chat: it stays on disk, and the user must not think it was delivered."""
        if self._documents is None:
            return
        try:
            documents = list(self._documents.take(task_id))
        except Exception:
            log.exception("could not list the documents of Telegram task %s", task_id)
            return
        for document in documents:
            if document.size > MAX_DOCUMENT_BYTES:
                await self._reply(
                    chat_id, f"{document.name} is too large for Telegram (limit 50 MB)."
                )
                continue
            try:
                content = await asyncio.to_thread(document.path.read_bytes)
                mime = _MIME_TYPES.get(document.path.suffix.lower(), "application/octet-stream")
                await self._client.send_document(chat_id, document.name, content, mime)
            except (OSError, TelegramError) as exc:
                log.warning("could not send the document %s: %s", document.name, exc)
                await self._reply(chat_id, f"Could not send {document.name}.")

    async def _keep_typing(self, chat_id: int) -> None:
        while True:
            try:
                await self._client.send_chat_action(chat_id)
            except TelegramError:
                pass
            await asyncio.sleep(TYPING_INTERVAL)

    # -- output -------------------------------------------------------------------------
    async def _reply(self, chat_id: int, text: str) -> None:
        await self.deliver(OutboundMessage(chat_id, text))

    async def deliver(self, message: OutboundMessage) -> None:
        for chunk in split_message(message.text) or ["(empty)"]:
            try:
                await self._send(message.chat_id, chunk)
            except TelegramError as exc:
                log.warning("could not send a Telegram message: %s", exc)
                return

    async def _send(self, chat_id: int, chunk: str) -> None:
        """The Markdown of an agent as Telegram HTML; if Telegram still refuses the formatting
        (a badly nested tag), the same text goes out as plain text rather than being lost."""
        formatted = to_telegram_html(chunk)
        if formatted:
            try:
                await self._client.send_message(chat_id, formatted, parse_mode="HTML")
                return
            except TelegramError as exc:
                if "parse entities" not in str(exc):
                    raise
                log.warning("Telegram refused the HTML formatting (%s); sending plain text", exc)
        await self._client.send_message(chat_id, chunk)


def render_outcome(outcome: RunOutcome) -> str:
    answer = (outcome.answer or "").strip()
    if str(outcome.status) == "completed":
        return answer or "(the task finished without an answer)"
    detail = outcome.error or str(outcome.status)
    if answer:
        return f"{answer}\n\nIncomplete: {detail}"
    return f"The task failed: {detail}"
