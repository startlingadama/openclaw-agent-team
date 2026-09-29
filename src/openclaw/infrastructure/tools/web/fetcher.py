"""Safe page fetching for `web.open` / `web.extract`.

The URL is chosen by an LLM that reads untrusted web content, so a page can try to make the agent
fetch `http://169.254.169.254/...` or an internal service (SSRF). The guard therefore:

- allows only http(s), without credentials in the URL;
- resolves the host and refuses any address that is not globally routable (private, loopback,
  link-local, CGNAT, multicast, reserved, IPv4-mapped IPv6 of those);
- follows redirects by hand and applies the same check to every hop;
- streams the body and stops at `max_bytes`, so a huge or compressed page cannot exhaust memory.

Known limit: the check resolves the name before the request, and httpx resolves it again to connect.
A DNS-rebinding attacker could answer differently the second time. Closing that gap needs pinning
the connection to the checked address (a custom transport); do it before exposing this tool to
hostile input beyond a single-user setup.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from openclaw.domain.shared.errors import AuthorizationError, ToolError, ValidationError

Resolver = Callable[[str, int], Awaitable[Sequence[str]]]

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_REFUSED = frozenset({401, 403, 429})
_TEXT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml+xml")
USER_AGENT = "openclaw-agent-team/0.1 (research agent)"


async def system_resolver(host: str, port: int) -> Sequence[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def check_public_url(url: str, resolver: Resolver = system_resolver) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValidationError("only http and https URLs can be opened")
    if not parts.hostname:
        raise ValidationError("the URL has no host")
    if parts.username or parts.password:
        raise ValidationError("URLs with embedded credentials are not allowed")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise ValidationError("the URL has an invalid port") from exc
    try:
        addresses = await resolver(parts.hostname, port)
    except OSError as exc:
        raise ToolError(f"cannot resolve host '{parts.hostname}'") from exc
    if not addresses or not all(is_public_address(a) for a in addresses):
        raise AuthorizationError(f"refusing to open '{parts.hostname}': not a public address")


@dataclass(frozen=True, slots=True)
class FetchedPage:
    url: str  # final URL, after redirects
    status: int
    content_type: str
    text: str
    truncated: bool  # the body was cut at max_bytes


async def fetch_page(
    client: httpx.AsyncClient,
    url: str,
    *,
    resolver: Resolver = system_resolver,
    max_bytes: int = 2_000_000,
    max_redirects: int = 5,
    user_agent: str = USER_AGENT,
) -> FetchedPage:
    current = url
    for _ in range(max_redirects + 1):
        await check_public_url(current, resolver)
        try:
            async with client.stream(
                "GET",
                current,
                headers={"User-Agent": user_agent, "Accept": "text/html,text/plain,*/*;q=0.5"},
                follow_redirects=False,
            ) as response:
                if response.status_code in _REDIRECTS:
                    location = response.headers.get("location")
                    if not location:
                        raise ToolError("the page redirects without a destination")
                    current = urljoin(current, location)
                    continue
                return await _read(response, current, max_bytes)
        except httpx.TimeoutException as exc:
            raise ToolError("the page timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ToolError(
                f"the page could not be fetched: {type(exc).__name__}", retryable=True
            ) from exc
    raise ToolError(f"too many redirects (more than {max_redirects})")


async def _read(response: httpx.Response, url: str, max_bytes: int) -> FetchedPage:
    status = response.status_code
    if status in _REFUSED:
        raise ToolError(
            f"the site refuses automated access (HTTP {status}). Retrying the same URL will "
            "fail again: use another source"
        )
    if status >= 400:
        raise ToolError(
            f"the page returned HTTP {status}", retryable=status >= 500 and status != 501
        )
    content_type = response.headers.get("content-type", "text/html").split(";")[0].strip().lower()
    if not content_type.startswith(_TEXT_TYPES):
        raise ToolError(f"unsupported content type '{content_type}': only text pages can be read")
    chunks: list[bytes] = []
    size = 0
    truncated = False
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            chunks.append(chunk[: len(chunk) - (size - max_bytes)])
            truncated = True
            break
        chunks.append(chunk)
    encoding = response.charset_encoding or "utf-8"
    try:
        text = b"".join(chunks).decode(encoding, errors="replace")
    except LookupError:
        text = b"".join(chunks).decode("utf-8", errors="replace")
    return FetchedPage(url, status, content_type, text, truncated)
