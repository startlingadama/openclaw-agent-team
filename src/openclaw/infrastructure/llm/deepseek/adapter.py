"""DeepSeekAdapter: implements LLMPort on DeepSeek's OpenAI-compatible chat completions API.

Errors are mapped to the explicit categories of REQUIREMENTS section 23:

- 401 / 403                  -> AuthenticationError (never retried)
- 429, 5xx, timeouts, network -> LLMError(retryable=True), retried with backoff
- other 4xx (incl. 402)       -> LLMError, not retried
- malformed answer            -> LLMError(retryable=True), retried

The API key only ever travels in the Authorization header: it is never put in an error message.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from openclaw.domain.agents.context import DecisionContext
from openclaw.domain.agents.decision import Decision
from openclaw.domain.shared.errors import (
    AuthenticationError,
    LLMError,
    OpenClawError,
    ValidationError,
)
from openclaw.infrastructure.llm.deepseek.prompt import (
    build_system_prompt,
    build_tools,
    build_user_prompt,
    parse_decision,
)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


@dataclass(frozen=True, slots=True)
class DeepSeekConfig:
    api_key: str
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 60.0
    temperature: float = 0.2
    max_tokens: int = 4096
    max_retries: int = 2
    backoff: float = 1.0  # seconds, doubled at each retry
    max_observation_chars: int = 8000

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise AuthenticationError("DEEPSEEK_API_KEY is not set")
        if self.model.startswith("deepseek-reasoner"):
            raise ValidationError(
                "deepseek-reasoner does not support tool calling: use a chat model "
                f"(DEEPSEEK_MODEL, default '{DEFAULT_MODEL}')"
            )
        if self.max_retries < 0 or self.max_tokens < 1 or self.max_observation_chars < 1:
            raise ValidationError("max_retries, max_tokens and max_observation_chars are invalid")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DeepSeekConfig:
        """Reads DEEPSEEK_API_KEY, DEEPSEEK_MODEL and DEEPSEEK_BASE_URL (.env.example)."""
        env = os.environ if env is None else env
        return cls(
            api_key=env.get("DEEPSEEK_API_KEY", ""),
            model=env.get("DEEPSEEK_MODEL") or DEFAULT_MODEL,
            base_url=env.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL,
        )


class DeepSeekAdapter:
    def __init__(
        self,
        config: DeepSeekConfig,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._config = config
        self._client = client or httpx.AsyncClient(timeout=config.timeout)
        self._owns_client = client is None
        self._sleep = sleep

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- LLMPort --------------------------------------------------------------------------
    async def decide(self, context: DecisionContext) -> Decision:
        tools, wire_to_tool = build_tools(context)
        payload = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": build_system_prompt(context)},
                {
                    "role": "user",
                    "content": build_user_prompt(context, self._config.max_observation_chars),
                },
            ],
            "tools": tools,
            "tool_choice": "required",
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
            "stream": False,
        }
        attempt = 0
        while True:
            try:
                message = await self._complete(payload)
                return parse_decision(message, wire_to_tool)
            except OpenClawError as exc:
                if not exc.retryable or attempt >= self._config.max_retries:
                    raise
                await self._sleep(self._config.backoff * (2**attempt))
                attempt += 1

    # -- HTTP -----------------------------------------------------------------------------
    async def _complete(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        url = self._config.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self._config.api_key}"}
        try:
            response = await self._client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMError("DeepSeek request timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise LLMError(
                f"DeepSeek request failed: {type(exc).__name__}", retryable=True
            ) from exc

        self._check_status(response)
        try:
            choice = response.json()["choices"][0]
            message, finish_reason = choice["message"], choice.get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("DeepSeek returned an unreadable response", retryable=True) from exc
        if finish_reason == "length":
            raise LLMError(
                f"DeepSeek answer was cut at max_tokens={self._config.max_tokens}; raise it"
            )
        if not isinstance(message, dict):
            raise LLMError("DeepSeek returned an unreadable response", retryable=True)
        return message

    @staticmethod
    def _check_status(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        detail = _error_detail(response)
        if status in (401, 403):
            raise AuthenticationError(f"DeepSeek rejected the API key (HTTP {status}){detail}")
        if status == 429 or status >= 500:
            raise LLMError(f"DeepSeek unavailable (HTTP {status}){detail}", retryable=True)
        raise LLMError(f"DeepSeek refused the request (HTTP {status}){detail}")


def _error_detail(response: httpx.Response) -> str:
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return ""
    return f": {message[:200]}" if isinstance(message, str) and message else ""
