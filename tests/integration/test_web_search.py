import asyncio
import json

import httpx
import pytest

from openclaw.domain.shared.errors import AuthenticationError, ToolError, ValidationError
from openclaw.infrastructure.tools.web import (
    BraveSearchBackend,
    FallbackSearchBackend,
    SearchResult,
    TavilySearchBackend,
    WebConfig,
    WebToolProvider,
)
from openclaw.infrastructure.tools.web.provider import parse_providers

BRAVE_HOST = "api.search.brave.com"
TAVILY_HOST = "api.tavily.com"


def run(coro):
    return asyncio.run(coro)


class Server:
    """Routes by host, records requests. Each host answers with a fixed response."""

    def __init__(self, brave=None, tavily=None):
        self.routes = {BRAVE_HOST: brave, TAVILY_HOST: tavily}
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        response = self.routes[request.url.host]
        assert response is not None, f"unexpected call to {request.url.host}"
        return response

    def hosts(self):
        return [r.url.host for r in self.requests]

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


def brave_ok(*urls):
    results = [{"title": f"<b>B</b> {u}", "url": u, "description": "d <i>x</i>"} for u in urls]
    return httpx.Response(200, json={"web": {"results": results}})


def tavily_ok(*urls):
    results = [{"title": f"T {u}", "url": u, "content": f"c {u}", "score": 0.9} for u in urls]
    return httpx.Response(200, json={"results": results})


# -- TavilySearchBackend ---------------------------------------------------------------------
def test_tavily_request_shape_and_mapping():
    server = Server(
        tavily=httpx.Response(
            200,
            json={
                "results": [
                    {"title": " A ", "url": "https://a.test", "content": " snippet ", "score": 0.9},
                    {"title": "no url", "content": "dropped"},
                    "not-a-dict",
                    {"title": "B", "url": "https://b.test", "content": None},
                ]
            },
        )
    )
    backend = TavilySearchBackend("tvly-secret", server.client())

    results = run(backend.search("openclaw agents", 5))

    assert results == [
        SearchResult("A", "https://a.test", "snippet"),
        SearchResult("B", "https://b.test", ""),
    ]
    (req,) = server.requests
    assert req.method == "POST"
    assert str(req.url) == "https://api.tavily.com/search"
    assert req.headers["authorization"] == "Bearer tvly-secret"
    body = json.loads(req.content)
    assert body["query"] == "openclaw agents"
    assert body["max_results"] == 5
    assert body["include_answer"] is False


def test_tavily_respects_limit():
    server = Server(tavily=tavily_ok("https://1.test", "https://2.test", "https://3.test"))
    results = run(TavilySearchBackend("k", server.client()).search("q", 2))
    assert [r.url for r in results] == ["https://1.test", "https://2.test"]


def test_tavily_requires_a_key():
    with pytest.raises(AuthenticationError):
        TavilySearchBackend("  ", httpx.AsyncClient())


def test_tavily_maps_http_errors_without_leaking_the_key():
    server = Server(tavily=httpx.Response(401, json={"detail": "bad key"}))
    with pytest.raises(AuthenticationError) as info:
        run(TavilySearchBackend("tvly-secret", server.client()).search("q", 5))
    assert "tvly-secret" not in str(info.value)

    server = Server(tavily=httpx.Response(429, headers={"retry-after": "3"}))
    with pytest.raises(ToolError, match="rate limit"):
        run(TavilySearchBackend("k", server.client()).search("q", 5))


def test_tavily_unreadable_body_is_a_retryable_tool_error():
    server = Server(tavily=httpx.Response(200, content=b"<html>oops"))
    with pytest.raises(ToolError) as info:
        run(TavilySearchBackend("k", server.client()).search("q", 5))
    assert info.value.retryable


# -- FallbackSearchBackend -------------------------------------------------------------------
def make_chain(server):
    client = server.client()
    return FallbackSearchBackend(
        [BraveSearchBackend("b", client), TavilySearchBackend("t", client)]
    )


def test_fallback_uses_only_brave_when_it_answers():
    server = Server(brave=brave_ok("https://brave.test"))
    results = run(make_chain(server).search("q", 5))
    assert [r.url for r in results] == ["https://brave.test"]
    assert server.hosts() == [BRAVE_HOST]


@pytest.mark.parametrize(
    "brave_failure",
    [
        httpx.Response(429, headers={"retry-after": "1"}),
        httpx.Response(401),
        httpx.Response(403),
        httpx.Response(503),
        brave_ok(),  # clean but empty answer
    ],
)
def test_fallback_switches_to_tavily(brave_failure):
    server = Server(brave=brave_failure, tavily=tavily_ok("https://tavily.test"))
    results = run(make_chain(server).search("q", 5))
    assert [r.url for r in results] == ["https://tavily.test"]
    assert server.hosts() == [BRAVE_HOST, TAVILY_HOST]


def test_fallback_raises_when_every_backend_fails():
    server = Server(brave=httpx.Response(429), tavily=httpx.Response(503))
    with pytest.raises(ToolError, match="Tavily"):
        run(make_chain(server).search("q", 5))


def test_fallback_returns_empty_when_a_backend_answered_with_nothing():
    server = Server(brave=httpx.Response(503), tavily=tavily_ok())
    assert run(make_chain(server).search("q", 5)) == []


def test_fallback_does_not_swallow_programming_errors():
    class Broken:
        async def search(self, query, limit):
            raise ValidationError("bad input")

    chain = FallbackSearchBackend([Broken(), Broken()])
    with pytest.raises(ValidationError):
        run(chain.search("q", 5))


def test_fallback_needs_a_backend():
    with pytest.raises(ValueError):
        FallbackSearchBackend([])


# -- WebConfig / WebToolProvider -------------------------------------------------------------
def test_config_reads_both_keys_from_env():
    config = WebConfig.from_env({"BRAVE_SEARCH_API_KEY": " b ", "TAVILY_API_KEY": " t "})
    assert (config.brave_api_key, config.tavily_api_key) == ("b", "t")
    assert WebConfig.from_env({}) == WebConfig()


@pytest.mark.parametrize(
    ("config", "offered"),
    [
        (WebConfig(), False),
        (WebConfig(brave_api_key="b"), True),
        (WebConfig(tavily_api_key="t"), True),
        (WebConfig(brave_api_key="b", tavily_api_key="t"), True),
    ],
)
def test_web_search_is_offered_when_any_backend_is_configured(config, offered):
    provider = WebToolProvider(config, httpx.AsyncClient())
    assert ("web.search" in provider.tool_names) is offered
    assert {"web.open", "web.extract"} <= provider.tool_names


def test_provider_with_both_keys_falls_back_to_tavily():
    server = Server(brave=httpx.Response(429), tavily=tavily_ok("https://tavily.test"))
    provider = WebToolProvider(WebConfig(brave_api_key="b", tavily_api_key="t"), server.client())
    out = run(provider._search({"query": "openclaw"}))
    assert out["query"] == "openclaw"
    assert [r["url"] for r in out["results"]] == ["https://tavily.test"]


def test_provider_with_only_tavily_never_calls_brave():
    server = Server(tavily=tavily_ok("https://tavily.test"))
    provider = WebToolProvider(WebConfig(tavily_api_key="t"), server.client())
    run(provider._search({"query": "openclaw"}))
    assert server.hosts() == [TAVILY_HOST]


# -- WEB_SEARCH_PROVIDERS --------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ("brave", "tavily")),
        ("  ", ("brave", "tavily")),
        ("tavily,brave", ("tavily", "brave")),
        (" Tavily , BRAVE ", ("tavily", "brave")),
        ("tavily", ("tavily",)),
        ("tavily,tavily,brave", ("tavily", "brave")),
        ("tavily,,", ("tavily",)),
    ],
)
def test_parse_providers(raw, expected):
    assert parse_providers(raw) == expected


def test_parse_providers_rejects_unknown_names():
    with pytest.raises(ValidationError, match="unknown provider 'google'"):
        parse_providers("tavily,google")


def test_config_reads_provider_order_from_env():
    assert WebConfig.from_env({}).search_providers == ("brave", "tavily")
    config = WebConfig.from_env({"WEB_SEARCH_PROVIDERS": "tavily,brave"})
    assert config.search_providers == ("tavily", "brave")


def test_configured_order_puts_tavily_first_and_brave_as_fallback():
    server = Server(brave=brave_ok("https://brave.test"), tavily=tavily_ok("https://tavily.test"))
    config = WebConfig(brave_api_key="b", tavily_api_key="t", search_providers=("tavily", "brave"))
    out = run(WebToolProvider(config, server.client())._search({"query": "q"}))
    assert [r["url"] for r in out["results"]] == ["https://tavily.test"]
    assert server.hosts() == [TAVILY_HOST]

    server = Server(brave=brave_ok("https://brave.test"), tavily=httpx.Response(503))
    out = run(WebToolProvider(config, server.client())._search({"query": "q"}))
    assert [r["url"] for r in out["results"]] == ["https://brave.test"]
    assert server.hosts() == [TAVILY_HOST, BRAVE_HOST]


def test_default_order_is_still_brave_then_tavily():
    server = Server(brave=brave_ok("https://brave.test"))
    config = WebConfig(brave_api_key="b", tavily_api_key="t")
    run(WebToolProvider(config, server.client())._search({"query": "q"}))
    assert server.hosts() == [BRAVE_HOST]


def test_listed_provider_without_key_is_skipped():
    server = Server(brave=brave_ok("https://brave.test"))
    config = WebConfig(brave_api_key="b", search_providers=("tavily", "brave"))
    run(WebToolProvider(config, server.client())._search({"query": "q"}))
    assert server.hosts() == [BRAVE_HOST]


def test_provider_not_listed_is_not_used_even_with_a_key():
    config = WebConfig(brave_api_key="b", tavily_api_key="t", search_providers=("tavily",))
    server = Server(tavily=httpx.Response(503))
    with pytest.raises(ToolError):
        run(WebToolProvider(config, server.client())._search({"query": "q"}))
    assert server.hosts() == [TAVILY_HOST]


def test_no_search_tool_when_listed_providers_have_no_key():
    config = WebConfig(brave_api_key="b", search_providers=("tavily",))
    assert "web.search" not in WebToolProvider(config, httpx.AsyncClient()).tool_names


# -- fetching: User-Agent and refusals ----------------------------------------------------------
def fetch_provider(handler, **config):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def public(host, port):
        return ["93.184.216.34"]

    return WebToolProvider(WebConfig(**config), client, resolver=public)


def open_call(url="https://example.test/page"):
    from openclaw.domain.tools.model import ToolCall

    return ToolCall("web.open", {"url": url})


def test_web_user_agent_comes_from_the_environment():
    from openclaw.infrastructure.tools.web.fetcher import USER_AGENT

    assert WebConfig.from_env({}).user_agent == USER_AGENT
    config = WebConfig.from_env({"WEB_USER_AGENT": " bot/1 (https://x.org; me@x.org) "})
    assert config.user_agent == "bot/1 (https://x.org; me@x.org)"


def test_the_configured_user_agent_is_sent():
    sent = []

    def handler(request):
        sent.append(request.headers["user-agent"])
        return httpx.Response(200, text="<html><title>t</title><body>hello</body></html>",
                              headers={"content-type": "text/html"})

    provider = fetch_provider(handler, user_agent="bot/1 (me@x.org)")
    asyncio.run(provider.execute(open_call(), "google-research"))
    assert sent == ["bot/1 (me@x.org)"]


@pytest.mark.parametrize("status", [401, 403, 429])
def test_a_refusing_site_says_not_to_retry(status):
    provider = fetch_provider(lambda request: httpx.Response(status))
    pattern = rf"refuses automated access \(HTTP {status}\).*another source"
    with pytest.raises(ToolError, match=pattern) as exc:
        asyncio.run(provider.execute(open_call(), "google-research"))
    assert not exc.value.retryable


def test_other_http_errors_keep_their_message():
    provider = fetch_provider(lambda request: httpx.Response(404))
    with pytest.raises(ToolError, match="HTTP 404"):
        asyncio.run(provider.execute(open_call(), "google-research"))
