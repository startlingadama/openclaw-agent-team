"""Gmail tool provider: the `google.email_*` tools of `agents/google-email/agent.yaml`.

Authentication: OAuth 2.0 user credentials. `GOOGLE_TOKEN_FILE` is the standard "authorized user"
JSON (as written by `google-auth-oauthlib`: `refresh_token`, `client_id`, `client_secret`);
`GOOGLE_CREDENTIALS_FILE` (the OAuth client file, `installed` / `web`) is only read for the
client id and secret when the token file lacks them. Access tokens are short-lived: they live in
memory and are refreshed from the refresh token (once more after a 401). Neither file is written.

Required scopes when authorizing: `gmail.readonly` (search, read) and `gmail.compose` (create drafts
and send). Sending is EXTERNAL_COMMUNICATION: always human-approved (ADR-015). Emails are plain text
only: no HTML, no attachments. Email content is untrusted data written by third parties.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from openclaw.domain.shared.errors import AuthenticationError, ToolError, ValidationError
from openclaw.domain.tools.model import RiskLevel, ToolSpec
from openclaw.infrastructure.tools.base import (
    ToolSet,
    clip,
    int_arg,
    json_body,
    req_str,
    request,
    str_arg,
)
from openclaw.infrastructure.tools.htmltext import html_to_text

API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SERVICE = "Gmail"
_EXPIRY_SKEW = 60.0
_ADDRESS = re.compile(r"[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+")
_LIST_HEADERS = ("From", "To", "Cc", "Subject", "Date", "Message-ID")


# -- credentials -----------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class GoogleCredentials:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)
    token_uri: str = TOKEN_URI

    @classmethod
    def from_files(
        cls, token_file: str | Path, credentials_file: str | Path | None = None
    ) -> GoogleCredentials:
        token = _read_json(token_file, "GOOGLE_TOKEN_FILE")
        client: Mapping[str, Any] = {}
        if not (token.get("client_id") and token.get("client_secret")) and credentials_file:
            raw = _read_json(credentials_file, "GOOGLE_CREDENTIALS_FILE")
            client = raw.get("installed") or raw.get("web") or raw
        pick = lambda key: token.get(key) or client.get(key) or ""  # noqa: E731
        refresh_token, client_id, client_secret = (
            pick("refresh_token"),
            pick("client_id"),
            pick("client_secret"),
        )
        if not (refresh_token and client_id and client_secret):
            raise AuthenticationError(
                "Google credentials are incomplete: need refresh_token, client_id and "
                "client_secret (GOOGLE_TOKEN_FILE, optionally GOOGLE_CREDENTIALS_FILE)"
            )
        return cls(client_id, client_secret, refresh_token, pick("token_uri") or TOKEN_URI)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> GoogleCredentials:
        env = os.environ if env is None else env
        token_file = env.get("GOOGLE_TOKEN_FILE", "").strip()
        if not token_file:
            raise AuthenticationError("GOOGLE_TOKEN_FILE is not set")
        return cls.from_files(token_file, env.get("GOOGLE_CREDENTIALS_FILE", "").strip() or None)


def _read_json(path: str | Path, label: str) -> Mapping[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # the message never includes file contents
        raise AuthenticationError(f"{label} cannot be read as JSON ({path})") from exc
    if not isinstance(data, dict):
        raise AuthenticationError(f"{label} must contain a JSON object ({path})")
    return data


class GoogleTokenSource:
    """Keeps a valid access token in memory, refreshing it when needed."""

    def __init__(
        self,
        credentials: GoogleCredentials,
        client: httpx.AsyncClient,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._credentials = credentials
        self._client = client
        self._clock = clock
        self._token = ""
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def token(self, *, force_refresh: bool = False) -> str:
        async with self._lock:
            if force_refresh or not self._token or self._clock() >= self._expires_at:
                await self._refresh()
            return self._token

    async def _refresh(self) -> None:
        c = self._credentials
        try:
            response = await self._client.post(
                c.token_uri,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": c.refresh_token,
                    "client_id": c.client_id,
                    "client_secret": c.client_secret,
                },
            )
        except httpx.TimeoutException as exc:
            raise ToolError("Google token refresh timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ToolError(
                f"Google token refresh failed: {type(exc).__name__}", retryable=True
            ) from exc
        if response.status_code in (400, 401):
            raise AuthenticationError(
                "Google refused the refresh token (revoked, expired or wrong client): "
                "authorize again and update GOOGLE_TOKEN_FILE"
            )
        if response.status_code >= 500:
            raise ToolError(
                f"Google token endpoint unavailable (HTTP {response.status_code})", retryable=True
            )
        if response.status_code >= 400:
            raise ToolError(f"Google token refresh refused (HTTP {response.status_code})")
        data = json_body("Google", response)
        token = data.get("access_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise ToolError("Google token endpoint returned no access token")
        self._token = token
        self._expires_at = self._clock() + float(data.get("expires_in", 3600)) - _EXPIRY_SKEW


# -- message helpers -------------------------------------------------------------------------
def _decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _headers(payload: Mapping[str, Any]) -> dict[str, str]:
    return {h.get("name", ""): h.get("value", "") for h in payload.get("headers", [])}


def _walk(payload: Mapping[str, Any], plain: list[str], html: list[str], files: list[dict]) -> None:
    body = payload.get("body") or {}
    mime = payload.get("mimeType", "")
    if payload.get("filename"):
        files.append(
            {
                "filename": payload["filename"],
                "mime_type": mime,
                "size": body.get("size"),
            }
        )
    elif body.get("data"):
        if mime == "text/plain":
            plain.append(_decode(body["data"]))
        elif mime == "text/html":
            html.append(_decode(body["data"]))
    for part in payload.get("parts", []):
        _walk(part, plain, html, files)


def _address(value: str, label: str) -> str:
    value = value.strip()
    match = re.fullmatch(r"(?:.*<)?([^<>]+)>?", value)  # "Name <a@b.c>" or "a@b.c"
    address = match.group(1).strip() if match else value
    if not _ADDRESS.fullmatch(address):
        raise ValidationError(f"'{label}' contains an invalid email address")
    return value


def _address_list(args: Mapping[str, Any], name: str, *, required: bool) -> str | None:
    raw = str_arg(args, name, required=required, max_len=1000)
    if raw is None:
        return None
    parts = [p for p in raw.split(",") if p.strip()]
    if not parts or len(parts) > 20:
        raise ValidationError(f"'{name}' must list between 1 and 20 addresses")
    return ", ".join(_address(p, name) for p in parts)


def _spec(name: str, description: str, properties: dict[str, Any], required: list[str], risk):
    return ToolSpec(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": properties, "required": required},
        output_schema={"type": "object"},
        risk_level=risk,
    )


_COMPOSE = {
    "to": {"type": "string", "description": "Recipient(s), comma-separated."},
    "subject": {"type": "string"},
    "body": {"type": "string", "description": "Plain-text body."},
    "cc": {"type": "string", "description": "Cc recipient(s), comma-separated."},
    "reply_to_message_id": {
        "type": "string",
        "description": "Id of the message being answered: keeps the conversation thread.",
    },
}

EMAIL_SEARCH = _spec(
    "google.email_search",
    "Search the mailbox with Gmail search syntax (from:, subject:, is:unread, newer_than:7d...). "
    "Returns sender, subject, date and a snippet for each match; use google.email_read for a body.",
    {
        "query": {"type": "string"},
        "limit": {"type": "integer", "description": "Messages to return (1-20, default 10)."},
    },
    ["query"],
    RiskLevel.READ,
)
EMAIL_READ = _spec(
    "google.email_read",
    "Read one email by id: headers, plain-text body and attachment names.",
    {"message_id": {"type": "string", "description": "Id from google.email_search."}},
    ["message_id"],
    RiskLevel.READ,
)
EMAIL_DRAFT = _spec(
    "google.email_draft",
    "Save a draft in the mailbox. Nothing is sent.",
    _COMPOSE,
    ["to", "subject", "body"],
    RiskLevel.WRITE,
)
EMAIL_SEND = _spec(
    "google.email_send",
    "Send an email. Requires human approval.",
    _COMPOSE,
    ["to", "subject", "body"],
    RiskLevel.EXTERNAL_COMMUNICATION,
)


class GmailToolProvider(ToolSet):
    def __init__(
        self,
        credentials: GoogleCredentials,
        client: httpx.AsyncClient | None = None,
        *,
        timeout: float = 30.0,
        max_body_chars: int = 8000,
        tokens: GoogleTokenSource | None = None,
    ) -> None:
        super().__init__()
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self._tokens = tokens or GoogleTokenSource(credentials, self._client)
        self._max_body_chars = max_body_chars
        for spec, handler in (
            (EMAIL_SEARCH, self._search),
            (EMAIL_READ, self._read),
            (EMAIL_DRAFT, self._draft),
            (EMAIL_SEND, self._send),
        ):
            self._add(spec, handler)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- HTTP -------------------------------------------------------------------------------
    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        for attempt in (0, 1):
            token = await self._tokens.token(force_refresh=attempt == 1)
            try:
                response = await request(
                    self._client,
                    SERVICE,
                    method,
                    API_BASE + path,
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                    json=payload,
                )
            except AuthenticationError:
                if attempt == 1:
                    raise
                continue  # a 401 means nothing was processed: refresh the token, try once more
            return json_body(SERVICE, response)
        raise AssertionError("unreachable")  # pragma: no cover

    # -- reads ------------------------------------------------------------------------------
    async def _search(self, args: Mapping[str, Any]) -> Any:
        query = req_str(args, "query", max_len=500)
        limit = int_arg(args, "limit", default=10, maximum=20)
        listing = await self._call("GET", "/messages", params={"q": query, "maxResults": limit})
        ids = [m["id"] for m in listing.get("messages", []) if "id" in m]
        messages = await asyncio.gather(*(self._metadata(i) for i in ids))
        return {
            "query": query,
            "estimated_total": listing.get("resultSizeEstimate", len(ids)),
            "messages": list(messages),
        }

    async def _metadata(self, message_id: str) -> dict[str, Any]:
        message = await self._call(
            "GET",
            f"/messages/{quote(message_id, safe='')}",
            params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
        )
        headers = _headers(message.get("payload") or {})
        return {
            "id": message.get("id"),
            "thread_id": message.get("threadId"),
            "from": headers.get("From"),
            "subject": headers.get("Subject"),
            "date": headers.get("Date"),
            "snippet": clip(message.get("snippet") or "", 300),
            "unread": "UNREAD" in message.get("labelIds", []),
        }

    async def _read(self, args: Mapping[str, Any]) -> Any:
        message_id = req_str(args, "message_id", max_len=100)
        message = await self._call(
            "GET", f"/messages/{quote(message_id, safe='')}", params={"format": "full"}
        )
        payload = message.get("payload") or {}
        headers = _headers(payload)
        plain: list[str] = []
        html: list[str] = []
        attachments: list[dict[str, Any]] = []
        _walk(payload, plain, html, attachments)
        text = "\n".join(plain).strip() or "\n".join(html_to_text(h) for h in html).strip()
        return {
            "id": message.get("id"),
            "thread_id": message.get("threadId"),
            **{h.lower().replace("-", "_"): headers.get(h) for h in _LIST_HEADERS},
            "labels": message.get("labelIds", []),
            "attachments": attachments,
            "body": clip(text, self._max_body_chars),
        }

    # -- writes (approval is decided by the PolicyEngine, not here) ---------------------------
    async def _build(self, args: Mapping[str, Any]) -> dict[str, Any]:
        message = EmailMessage()
        try:
            message["To"] = _address_list(args, "to", required=True)
            if cc := _address_list(args, "cc", required=False):
                message["Cc"] = cc
            message["Subject"] = req_str(args, "subject", max_len=300)
            message.set_content(req_str(args, "body", max_len=50_000))
            body: dict[str, Any] = {}
            if reply_to := str_arg(args, "reply_to_message_id", required=False, max_len=100):
                original = await self._call(
                    "GET",
                    f"/messages/{quote(reply_to, safe='')}",
                    params={"format": "metadata", "metadataHeaders": ["Message-ID", "References"]},
                )
                headers = _headers(original.get("payload") or {})
                if rfc_id := headers.get("Message-ID"):
                    message["In-Reply-To"] = rfc_id
                    message["References"] = f"{headers.get('References', '')} {rfc_id}".strip()
                if thread_id := original.get("threadId"):
                    body["threadId"] = thread_id
        except ValueError as exc:  # e.g. a line break smuggled into a header value
            raise ValidationError(f"invalid email header: {exc}") from exc
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        return {"raw": raw, **body}

    async def _draft(self, args: Mapping[str, Any]) -> Any:
        draft = await self._call("POST", "/drafts", payload={"message": await self._build(args)})
        return {"draft_id": draft.get("id"), "message_id": (draft.get("message") or {}).get("id")}

    async def _send(self, args: Mapping[str, Any]) -> Any:
        sent = await self._call("POST", "/messages/send", payload=await self._build(args))
        return {"message_id": sent.get("id"), "thread_id": sent.get("threadId")}
