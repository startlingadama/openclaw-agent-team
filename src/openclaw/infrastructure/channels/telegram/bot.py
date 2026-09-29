"""The running bot: the adapter plus what must be closed with it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from openclaw.infrastructure.channels.telegram.adapter import TelegramAdapter


class TelegramBot:
    def __init__(self, adapter: TelegramAdapter, *closers: Callable[[], Awaitable[None]]) -> None:
        self._adapter = adapter
        self._closers = closers

    @property
    def adapter(self) -> TelegramAdapter:
        return self._adapter

    async def run(self) -> None:
        await self._adapter.run()

    async def aclose(self) -> None:
        await self._adapter.aclose()
        for close in self._closers:
            await close()
