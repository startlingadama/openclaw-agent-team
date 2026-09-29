"""Plumbing shared by the tool providers (`github.*`, `google.*`, `linkedin.*`, `web.*`).

- `ToolSet`: the `ToolProvider` contract (declared names, spec, execute) as a name -> handler table.
- argument helpers: the runtime only checks that required keys exist
  (`ToolSpec.validate_arguments`), so every handler validates types and bounds itself.
  Arguments come from an LLM: never trusted.
- `request` / `check_status`: HTTP failures mapped to the explicit categories of REQUIREMENTS
  section 23. Credentials only ever travel in request headers and never reach an error message.

Retries belong to the runtime (`ActionExecutor`, READ tools only): a provider never retries. It only
says whether a failure is `retryable` (timeouts, network errors, 5xx). Rate limits are not marked
retryable because the executor retries immediately, without backoff.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import httpx

from openclaw.domain.agents.model import AgentId
from openclaw.domain.shared.errors import (
    AuthenticationError,
    AuthorizationError,
    ToolError,
    ValidationError,
)
from openclaw.domain.tools.model import ToolCall, ToolSpec

Handler = Callable[[Mapping[str, Any]], Awaitable[Any]]

TRUNCATED = " [...truncated]"


class ToolSet:
    """Base class for `ToolProvider` implementations."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, Handler]] = {}
        self.tool_names: frozenset[str] = frozenset()

    def _add(self, spec: ToolSpec, handler: Handler) -> None:
        self._tools[spec.name] = (spec, handler)
        self.tool_names = frozenset(self._tools)

    def get_spec(self, name: str) -> ToolSpec | None:
        entry = self._tools.get(name)
        return entry[0] if entry else None

    async def execute(self, call: ToolCall, caller: AgentId) -> Any:
        entry = self._tools.get(call.name)
        if entry is None:
            raise ToolError(f"unknown tool: {call.name}")
        return await entry[1](call.arguments)


# -- argument validation ---------------------------------------------------------------------
def str_arg(
    args: Mapping[str, Any],
    name: str,
    *,
    required: bool = True,
    max_len: int = 10_000,
) -> str | None:
    value = args.get(name)
    if value is None:
        if required:
            raise ValidationError(f"'{name}' is required")
        return None
    if not isinstance(value, str):
        raise ValidationError(f"'{name}' must be a string")
    value = value.strip()
    if required and not value:
        raise ValidationError(f"'{name}' must not be empty")
    if len(value) > max_len:
        raise ValidationError(f"'{name}' is too long (max {max_len} characters)")
    return value or None


def req_str(args: Mapping[str, Any], name: str, *, max_len: int = 10_000) -> str:
    value = str_arg(args, name, required=True, max_len=max_len)
    assert value is not None  # required=True never returns None
    return value


def int_arg(
    args: Mapping[str, Any],
    name: str,
    *,
    default: int | None = None,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    value = args.get(name, default)
    if value is None:
        raise ValidationError(f"'{name}' is required")
    if isinstance(value, str) and value.strip().isdigit():  # LLMs sometimes send "12"
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"'{name}' must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        bound = f">= {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        raise ValidationError(f"'{name}' must be {bound}")
    return value


def bool_arg(args: Mapping[str, Any], name: str, default: bool = False) -> bool:
    value = args.get(name, default)
    if not isinstance(value, bool):
        raise ValidationError(f"'{name}' must be a boolean")
    return value


def str_list_arg(
    args: Mapping[str, Any], name: str, *, max_items: int = 20, max_len: int = 200
) -> list[str]:
    value = args.get(name)
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, str) or len(value) > max_items:
        raise ValidationError(f"'{name}' must be a list of at most {max_items} strings")
    items = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > max_len:
            raise ValidationError(f"'{name}' must contain only short, non-empty strings")
        items.append(item.strip())
    return items


def clip(text: str, limit: int) -> str:
    """Bounds one field so a single long value cannot starve the rest of an observation."""
    return text if len(text) <= limit else text[:limit].rstrip() + TRUNCATED


# -- HTTP ------------------------------------------------------------------------------------
async def request(
    client: httpx.AsyncClient,
    service: str,
    method: str,
    url: str,
    *,
    status_messages: Mapping[int, str] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Sends one request and returns it only when the status is < 400."""
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise ToolError(f"{service} request timed out", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise ToolError(f"{service} request failed: {type(exc).__name__}", retryable=True) from exc
    check_status(service, response, status_messages)
    return response


def check_status(
    service: str, response: httpx.Response, status_messages: Mapping[int, str] | None = None
) -> None:
    status = response.status_code
    if status < 400:
        return
    if status_messages and status in status_messages:
        raise ToolError(f"{service}: {status_messages[status]} (HTTP {status})")
    detail = error_detail(response)
    if status == 401:
        raise AuthenticationError(f"{service} rejected the credentials (HTTP 401){detail}")
    if status == 429 or (status == 403 and _rate_limited(response)):
        wait = response.headers.get("retry-after")
        hint = f", retry after {wait}s" if wait else ""
        raise ToolError(f"{service} rate limit reached (HTTP {status}{hint}){detail}")
    if status == 403:
        raise AuthorizationError(
            f"{service} refused the request: missing permission (HTTP 403){detail}"
        )
    if status >= 500:
        raise ToolError(f"{service} unavailable (HTTP {status}){detail}", retryable=True)
    if status == 404:
        raise ToolError(f"{service}: not found (HTTP 404){detail}")
    raise ToolError(f"{service} refused the request (HTTP {status}){detail}")


def _rate_limited(response: httpx.Response) -> bool:
    return response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers


def error_detail(response: httpx.Response) -> str:
    """The provider's own error message, bounded. Never includes request headers."""
    try:
        body = response.json()
    except ValueError:
        return ""
    message: Any = None
    if isinstance(body, dict):
        error = body.get("error")
        message = body.get("message") or (
            error.get("message") if isinstance(error, dict) else error_description(body)
        )
        if isinstance(message, str) and message and (reason := _first_reason(body)):
            message = f"{message} - {reason}"  # e.g. GitHub 422 "Validation Failed"
    return f": {message[:300]}" if isinstance(message, str) and message else ""


def _first_reason(body: Mapping[str, Any]) -> str:
    errors = body.get("errors")
    if isinstance(errors, list):
        for item in errors:
            text = item.get("message") if isinstance(item, dict) else item
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def error_description(body: Mapping[str, Any]) -> Any:
    return body.get("error_description") or body.get("error")


def json_body(service: str, response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise ToolError(f"{service} returned an unreadable response", retryable=True) from exc
