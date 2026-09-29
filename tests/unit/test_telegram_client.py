"""TelegramClient against a fake Bot API (httpx.MockTransport): no network."""

import asyncio
import json

import httpx
import pytest

from openclaw.domain.shared.errors import AuthenticationError
from openclaw.infrastructure.channels.telegram import TelegramClient, TelegramConfig, TelegramError

TOKEN = "123:very-secret"
CONFIG = TelegramConfig(TOKEN, frozenset({1}))


def client_for(handler):
    return TelegramClient(CONFIG, transport=httpx.MockTransport(handler))


def ok(result):
    return httpx.Response(200, json={"ok": True, "result": result})


def test_the_method_and_the_payload_reach_the_api():
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        return ok({"message_id": 9})

    async def go():
        client = client_for(handler)
        try:
            return await client.send_message(5, "hi", reply_markup={"inline_keyboard": []})
        finally:
            await client.aclose()

    assert asyncio.run(go()) == {"message_id": 9}
    path, payload = seen[0]
    assert path == f"/bot{TOKEN}/sendMessage"
    assert payload == {"chat_id": 5, "text": "hi", "reply_markup": {"inline_keyboard": []}}


def test_get_updates_asks_for_messages_and_button_presses_only():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return ok([])

    async def go():
        client = client_for(handler)
        try:
            await client.get_updates(7, 30)
            await client.get_updates(None, 30)
        finally:
            await client.aclose()

    asyncio.run(go())
    assert seen[0]["offset"] == 7 and "offset" not in seen[1]
    assert seen[0]["allowed_updates"] == ["message", "callback_query"]


def test_an_invalid_token_is_an_authentication_error_without_the_token():
    def handler(request):
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    async def go():
        client = client_for(handler)
        try:
            await client.get_me()
        finally:
            await client.aclose()

    with pytest.raises(AuthenticationError) as info:
        asyncio.run(go())
    assert TOKEN not in str(info.value)


def test_an_api_error_carries_its_description():
    def handler(request):
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})

    async def go():
        client = client_for(handler)
        try:
            await client.send_message(1, "x")
        finally:
            await client.aclose()

    with pytest.raises(TelegramError, match="chat not found"):
        asyncio.run(go())


def test_a_network_failure_is_a_telegram_error_that_never_shows_the_token():
    def handler(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    async def go():
        client = client_for(handler)
        try:
            await client.get_me()
        finally:
            await client.aclose()

    with pytest.raises(TelegramError) as info:
        asyncio.run(go())
    assert TOKEN not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_a_short_rate_limit_is_waited_out_once(monkeypatch):
    waits, answers = (
        [],
        [
            httpx.Response(
                429, json={"ok": False, "description": "slow", "parameters": {"retry_after": 2}}
            ),
            ok({"message_id": 1}),
        ],
    )

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(
        "openclaw.infrastructure.channels.telegram.client.asyncio.sleep", fake_sleep
    )

    async def go():
        client = client_for(lambda request: answers.pop(0))
        try:
            return await client.send_message(1, "x")
        finally:
            await client.aclose()

    assert asyncio.run(go()) == {"message_id": 1}
    assert waits == [2.0]


def test_a_long_rate_limit_is_reported_with_its_delay():
    def handler(request):
        return httpx.Response(
            429, json={"ok": False, "description": "slow", "parameters": {"retry_after": 500}}
        )

    async def go():
        client = client_for(handler)
        try:
            await client.get_updates(None, 1)
        finally:
            await client.aclose()

    with pytest.raises(TelegramError) as info:
        asyncio.run(go())
    assert info.value.retry_after == 500


def test_a_document_is_uploaded_as_a_multipart_form():
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers["content-type"], request.content))
        return ok({"message_id": 3})

    async def go():
        client = client_for(handler)
        try:
            return await client.send_document(5, "note.md", b"# Title", "text/markdown")
        finally:
            await client.aclose()

    assert asyncio.run(go()) == {"message_id": 3}
    path, content_type, body = seen[0]
    assert path == f"/bot{TOKEN}/sendDocument"
    assert content_type.startswith("multipart/form-data")
    assert b'name="chat_id"' in body and b"\r\n5\r\n" in body
    assert b'name="document"; filename="note.md"' in body and b"# Title" in body
    assert b"text/markdown" in body


def test_an_upload_error_never_shows_the_token():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    async def go():
        client = client_for(handler)
        try:
            await client.send_document(5, "a.pdf", b"x")
        finally:
            await client.aclose()

    with pytest.raises(TelegramError) as caught:
        asyncio.run(go())
    assert TOKEN not in str(caught.value)
