"""Channel messages (ARCHITECTURE section 16): what enters and leaves the Telegram adapter."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """A text message from an authorized user, in a private chat."""

    chat_id: int
    user_id: int
    text: str
    message_id: int = 0


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """A text for a chat. The adapter splits it to fit Telegram's message size."""

    chat_id: int
    text: str
