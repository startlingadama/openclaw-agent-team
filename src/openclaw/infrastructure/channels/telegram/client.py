"""Telegram Bot API client: HTTPS calls and configuration (long polling, no webhook).

The bot token is part of every URL: it is never logged and never put in an error message.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from openclaw.domain.shared.errors import AuthenticationError, OpenClawError, ValidationError

log = logging.getLogger(__name__)

DEFAULT_API_URL = "https://api.telegram.org"
DEFAULT_APPROVAL_TIMEOUT = 300.0
DEFAULT_POLL_TIMEOUT = 30
MAX_RETRY_AFTER = 30.0
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024  # what the Bot API accepts for a file sent by a bot
UPLOAD_TIMEOUT = 120.0


class TelegramError(OpenClawError):
    """The Bot API or the network failed. The polling loop retries; a send is only logged."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message, retryable=True)
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    token: str = field(repr=False)
    allowed_user_ids: frozenset[int]
    team: str = "default"
    approval_timeout: float = DEFAULT_APPROVAL_TIMEOUT
    poll_timeout: int = DEFAULT_POLL_TIMEOUT
    api_url: str = DEFAULT_API_URL

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, default_team: str = "default") -> TelegramConfig:
        token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise AuthenticationError("TELEGRAM_BOT_TOKEN is not set")
        allowed = _user_ids(env.get("TELEGRAM_ALLOWED_USER_IDS", ""))
        team = env.get("TELEGRAM_TEAM", "").strip() or default_team
        raw = env.get("TELEGRAM_APPROVAL_TIMEOUT", "").strip()
        try:
            timeout = float(raw) if raw else DEFAULT_APPROVAL_TIMEOUT
        except ValueError:
            timeout = 0.0
        if timeout <= 0:
            raise ValidationError("TELEGRAM_APPROVAL_TIMEOUT must be a number of seconds > 0")
        api_url = env.get("TELEGRAM_API_URL", "").strip() or DEFAULT_API_URL
        return cls(token, allowed, team, timeout, api_url=api_url)


def _user_ids(raw: str) -> frozenset[int]:
    items = [item for item in re.split(r"[\s,;]+", raw.strip()) if item]
    if not items:
        raise ValidationError(
            "TELEGRAM_ALLOWED_USER_IDS is required: the numeric Telegram ids allowed to use "
            "the bot, separated by commas (the bot can send e-mails and run code)"
        )
    ids: set[int] = set()
    for item in items:
        if not item.isdecimal() or int(item) < 1:
            raise ValidationError(
                f"TELEGRAM_ALLOWED_USER_IDS: {item!r} is not a Telegram user id "
                "(a positive integer)"
            )
        ids.add(int(item))
    return frozenset(ids)


class TelegramClient:
    def __init__(
        self, config: TelegramConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._base = f"{config.api_url.rstrip('/')}/bot{config.token}"
        self._http = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30.0))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_me(self) -> dict[str, Any]:
        return await self._call("getMe")

    async def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        return await self._call("getUpdates", payload, timeout=timeout + 15)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: Mapping[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self._call("sendMessage", payload)

    async def send_document(
        self, chat_id: int, name: str, content: bytes, mime_type: str = "application/octet-stream"
    ) -> dict[str, Any]:
        """Send a file to the chat (at most `MAX_DOCUMENT_BYTES`)."""
        return await self._call(
            "sendDocument",
            {"chat_id": chat_id},
            timeout=UPLOAD_TIMEOUT,
            files={"document": (name, content, mime_type)},
        )

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        """Replace a message's text and remove its buttons."""
        await self._call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "reply_markup": {"inline_keyboard": []},
            },
        )

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        await self._call("sendChatAction", {"chat_id": chat_id, "action": action})

    async def answer_callback_query(self, callback_id: str, text: str = "") -> None:
        await self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    async def _call(
        self,
        method: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
    ) -> Any:
        for attempt in (1, 2):
            try:
                if files is None:
                    response = await self._http.post(
                        f"{self._base}/{method}", json=dict(payload or {}), timeout=timeout
                    )
                else:  # an upload is a multipart form, not JSON
                    form = {key: str(value) for key, value in (payload or {}).items()}
                    response = await self._http.post(
                        f"{self._base}/{method}", data=form, files=dict(files), timeout=timeout
                    )
            except httpx.HTTPError as exc:
                # No chaining: the message of an httpx error can carry the URL, hence the token.
                raise TelegramError(f"{method}: {type(exc).__name__}") from None
            try:
                body = response.json()
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                body = {}
            if response.status_code == 200 and body.get("ok"):
                return body.get("result")
            if response.status_code == 401:
                raise AuthenticationError("Telegram rejected the bot token (TELEGRAM_BOT_TOKEN)")
            description = str(body.get("description") or f"HTTP {response.status_code}")
            parameters = body.get("parameters")
            retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
            wait = float(retry_after) if isinstance(retry_after, int | float) else None
            if response.status_code == 429 and attempt == 1 and wait and wait <= MAX_RETRY_AFTER:
                log.warning("Telegram rate limit on %s: waiting %.0f s", method, wait)
                await asyncio.sleep(wait)
                continue
            raise TelegramError(f"{method}: {description}", retry_after=wait)
        raise TelegramError(f"{method}: rate limited")  # unreachable: the loop returns or raises
