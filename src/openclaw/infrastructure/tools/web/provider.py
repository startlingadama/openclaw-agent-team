"""Web tool provider: `web.search`, `web.open`, `web.extract` of the research agent.

`web.open` and `web.extract` need no credentials. `web.search` needs a search backend and is only
registered when one is configured, so an agent never sees a tool that cannot work.

Two backends ship: the Brave Search API (`BRAVE_SEARCH_API_KEY`) and Tavily (`TAVILY_API_KEY`).
Google's Custom Search JSON API is closed to new customers and retires on 2027-01-01, so it is not
a base to build on. `WEB_SEARCH_PROVIDERS` (comma-separated, e.g. `tavily,brave`) picks and orders
the providers, default `brave,tavily`. The first is tried first and the next takes over when it
fails or finds nothing (`FallbackSearchBackend`); a listed provider without a key is skipped.
Backends implement `SearchBackend`; adding one is a new class, not a change to the provider
(ADR-002).
Page content is untrusted data: it is never interpreted here, only cut and returned.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from openclaw.domain.shared.errors import (
    AuthenticationError,
    AuthorizationError,
    ToolError,
    ValidationError,
)
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
from openclaw.infrastructure.tools.htmltext import HtmlPage, parse_html
from openclaw.infrastructure.tools.web.fetcher import (
    USER_AGENT,
    FetchedPage,
    Resolver,
    fetch_page,
    system_resolver,
)

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
TAVILY_URL = "https://api.tavily.com/search"
SEARCH_PROVIDERS = ("brave", "tavily")  # also the default order
_TAG = re.compile(r"<[^>]+>")
_WORD = re.compile(r"\w{3,}", re.UNICODE)


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchBackend(Protocol):
    async def search(self, query: str, limit: int) -> Sequence[SearchResult]:
        """Raises ToolError / AuthenticationError on failure."""
        ...


@dataclass(frozen=True, slots=True)
class WebConfig:
    brave_api_key: str = ""  # both keys empty: web.search is not offered
    tavily_api_key: str = ""
    search_providers: tuple[str, ...] = SEARCH_PROVIDERS  # order = priority
    timeout: float = 20.0
    max_bytes: int = 2_000_000
    max_redirects: int = 5
    # Some sites (Wikipedia, per its User-Agent policy) refuse clients without a way to contact
    # their operator: set WEB_USER_AGENT to e.g. "my-bot/1.0 (https://example.org; me@example.org)".
    user_agent: str = USER_AGENT

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> WebConfig:
        env = os.environ if env is None else env
        return cls(
            brave_api_key=env.get("BRAVE_SEARCH_API_KEY", "").strip(),
            tavily_api_key=env.get("TAVILY_API_KEY", "").strip(),
            search_providers=parse_providers(env.get("WEB_SEARCH_PROVIDERS", "")),
            user_agent=env.get("WEB_USER_AGENT", "").strip() or USER_AGENT,
        )


def parse_providers(raw: str) -> tuple[str, ...]:
    """`"tavily, Brave"` -> `("tavily", "brave")`. Empty: the default order. Unknown name: error."""
    names: list[str] = []
    for part in raw.split(","):
        name = part.strip().lower()
        if not name or name in names:
            continue
        if name not in SEARCH_PROVIDERS:
            known = ", ".join(SEARCH_PROVIDERS)
            raise ValidationError(
                f"WEB_SEARCH_PROVIDERS: unknown provider '{name}' (known: {known})"
            )
        names.append(name)
    return tuple(names) or SEARCH_PROVIDERS


class BraveSearchBackend:
    def __init__(self, api_key: str, client: httpx.AsyncClient) -> None:
        if not api_key.strip():
            raise AuthenticationError("BRAVE_SEARCH_API_KEY is not set")
        self._api_key = api_key
        self._client = client

    async def search(self, query: str, limit: int) -> Sequence[SearchResult]:
        response = await request(
            self._client,
            "Brave Search",
            "GET",
            BRAVE_URL,
            headers={"Accept": "application/json", "X-Subscription-Token": self._api_key},
            params={"q": query, "count": limit},
        )
        data = json_body("Brave Search", response)
        results = ((data.get("web") or {}).get("results") or []) if isinstance(data, dict) else []
        return [
            SearchResult(
                title=_TAG.sub("", r.get("title") or "").strip(),
                url=r.get("url") or "",
                snippet=_TAG.sub("", r.get("description") or "").strip(),
            )
            for r in results
            if r.get("url")
        ][:limit]


class TavilySearchBackend:
    def __init__(self, api_key: str, client: httpx.AsyncClient) -> None:
        if not api_key.strip():
            raise AuthenticationError("TAVILY_API_KEY is not set")
        self._api_key = api_key
        self._client = client

    async def search(self, query: str, limit: int) -> Sequence[SearchResult]:
        response = await request(
            self._client,
            "Tavily",
            "POST",
            TAVILY_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "query": query,
                "max_results": limit,
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
            },
        )
        data = json_body("Tavily", response)
        results = data.get("results") if isinstance(data, dict) else None
        return [
            SearchResult(
                title=str(r.get("title") or "").strip(),
                url=r["url"],
                snippet=str(r.get("content") or "").strip(),
            )
            for r in (results if isinstance(results, list) else [])
            if isinstance(r, dict) and isinstance(r.get("url"), str) and r["url"]
        ][:limit]


class FallbackSearchBackend:
    """Tries backends in order; the next one takes over on a failure or an empty answer.

    Only provider-side failures fall through (`ToolError`, `AuthenticationError`,
    `AuthorizationError`): a bad key or a rate limit on one backend must not take search down while
    another can answer. If every backend fails, the last failure is raised; if at least one
    answered cleanly with no result, the answer is empty.
    """

    def __init__(self, backends: Sequence[SearchBackend]) -> None:
        if not backends:
            raise ValueError("FallbackSearchBackend needs at least one backend")
        self._backends = tuple(backends)

    async def search(self, query: str, limit: int) -> Sequence[SearchResult]:
        failure: Exception | None = None
        answered = False
        for backend in self._backends:
            try:
                results = await backend.search(query, limit)
            except (ToolError, AuthenticationError, AuthorizationError) as exc:
                failure = exc
                continue
            if results:
                return results
            answered = True
        if failure is not None and not answered:
            raise failure
        return []


def _default_backend(config: WebConfig, client: httpx.AsyncClient) -> SearchBackend | None:
    """The configured providers, in `config.search_providers` order, that have a key."""
    factories: dict[str, tuple[str, type[BraveSearchBackend] | type[TavilySearchBackend]]] = {
        "brave": (config.brave_api_key, BraveSearchBackend),
        "tavily": (config.tavily_api_key, TavilySearchBackend),
    }
    backends: list[SearchBackend] = []
    for name in config.search_providers:
        key, backend_class = factories[name]
        if key:
            backends.append(backend_class(key, client))
    if not backends:
        return None
    return backends[0] if len(backends) == 1 else FallbackSearchBackend(backends)


def _spec(name: str, description: str, properties: dict[str, Any], required: list[str]) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": properties, "required": required},
        output_schema={"type": "object"},
        risk_level=RiskLevel.READ,
    )


_URL = {"type": "string", "description": "Absolute http(s) URL of a public page."}

WEB_SEARCH = _spec(
    "web.search",
    "Search the web. Returns titles, URLs and snippets; open a result to read the page.",
    {
        "query": {"type": "string"},
        "limit": {"type": "integer", "description": "Results to return (1-10, default 5)."},
    },
    ["query"],
)
WEB_OPEN = _spec(
    "web.open",
    "Fetch a public web page and return its readable text (text pages only, no PDF).",
    {
        "url": _URL,
        "max_chars": {
            "type": "integer",
            "description": "Text to return (500-20000, default 6000).",
        },
    },
    ["url"],
)
WEB_EXTRACT = _spec(
    "web.extract",
    "Fetch a public web page and return its structure: title, description, headings and links. "
    "With 'query', also returns the passages that best match its words.",
    {
        "url": _URL,
        "query": {"type": "string", "description": "Words to look for in the page text."},
        "max_links": {"type": "integer", "description": "Links to return (0-100, default 20)."},
    },
    ["url"],
)


def best_passages(lines: Sequence[str], query: str, limit: int = 8) -> list[str]:
    """Lines matching the most distinct query words, best first; ties keep document order."""
    terms = {w.lower() for w in _WORD.findall(query)}
    if not terms:
        return []
    scored = [
        (sum(t in line.lower() for t in terms), -index, line) for index, line in enumerate(lines)
    ]
    ranked = sorted((s for s in scored if s[0] > 0), reverse=True)
    return [clip(line, 500) for _, _, line in ranked[:limit]]


class WebToolProvider(ToolSet):
    def __init__(
        self,
        config: WebConfig | None = None,
        client: httpx.AsyncClient | None = None,
        *,
        search: SearchBackend | None = None,
        resolver: Resolver = system_resolver,
    ) -> None:
        super().__init__()
        self._config = config or WebConfig()
        # follow_redirects stays off: the fetcher checks every hop itself.
        self._client = client or httpx.AsyncClient(timeout=self._config.timeout)
        self._owns_client = client is None
        self._resolver = resolver
        backend = search or _default_backend(self._config, self._client)
        self._search_backend = backend
        if backend is not None:
            self._add(WEB_SEARCH, self._search)
        self._add(WEB_OPEN, self._open)
        self._add(WEB_EXTRACT, self._extract)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _search(self, args: Mapping[str, Any]) -> Any:
        assert self._search_backend is not None  # registered only with a backend
        query = req_str(args, "query", max_len=400)
        limit = int_arg(args, "limit", default=5, maximum=10)
        results = await self._search_backend.search(query, limit)
        return {
            "query": query,
            "results": [
                {"title": r.title, "url": r.url, "snippet": clip(r.snippet, 400)} for r in results
            ],
        }

    async def _fetch(self, args: Mapping[str, Any]) -> tuple[FetchedPage, HtmlPage | None]:
        url = req_str(args, "url", max_len=2000)
        page = await fetch_page(
            self._client,
            url,
            resolver=self._resolver,
            max_bytes=self._config.max_bytes,
            max_redirects=self._config.max_redirects,
            user_agent=self._config.user_agent,
        )
        is_html = page.content_type in ("text/html", "application/xhtml+xml")
        return page, parse_html(page.text, page.url) if is_html else None

    async def _open(self, args: Mapping[str, Any]) -> Any:
        limit = int_arg(args, "max_chars", default=6000, minimum=500, maximum=20_000)
        page, parsed = await self._fetch(args)
        text = parsed.text if parsed else page.text.strip()
        return {
            "url": page.url,
            "status": page.status,
            "content_type": page.content_type,
            "title": parsed.title if parsed else "",
            "text": clip(text, limit),
            "truncated": len(text) > limit or page.truncated,
        }

    async def _extract(self, args: Mapping[str, Any]) -> Any:
        query = str_arg(args, "query", required=False, max_len=400)
        max_links = int_arg(args, "max_links", default=20, minimum=0, maximum=100)
        page, parsed = await self._fetch(args)
        lines = parsed.lines if parsed else tuple(page.text.splitlines())
        result: dict[str, Any] = {
            "url": page.url,
            "title": parsed.title if parsed else "",
            "description": clip(parsed.description, 500) if parsed else "",
            "headings": [
                {"level": level, "text": clip(text, 200)}
                for level, text in (parsed.headings[:30] if parsed else ())
            ],
            "links": [
                {"text": clip(text, 120), "url": url}
                for text, url in (parsed.links[:max_links] if parsed else ())
            ],
        }
        if query:
            result["passages"] = best_passages(lines, query)
        return result
