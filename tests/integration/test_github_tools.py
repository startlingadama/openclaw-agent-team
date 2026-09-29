"""GitHub provider on a mocked HTTP transport (no network)."""

import asyncio
from pathlib import Path

import httpx
import pytest

from openclaw.domain.shared.errors import ToolError, ValidationError
from openclaw.domain.tools.model import ToolCall
from openclaw.infrastructure.agents import YamlAgentRepository
from openclaw.infrastructure.tools.github import GitHubConfig, GitHubToolProvider

AGENTS = Path(__file__).resolve().parents[2] / "agents"

USER = {
    "login": "octo-dev",
    "name": "Octo Dev",
    "html_url": "https://github.com/octo-dev",
    "public_repos": 12,
    "owned_private_repos": 3,
    "total_private_repos": 5,
    "followers": 7,
}


def run(coro):
    return asyncio.run(coro)


class Server:
    def __init__(self, user=None, search=None):
        self.user = user if user is not None else httpx.Response(200, json=USER)
        self.search = search or httpx.Response(200, json={"total_count": 0, "items": []})
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        return self.user if request.url.path == "/user" else self.search

    def paths(self):
        return [r.url.path for r in self.requests]

    def queries(self):
        return [r.url.params.get("q") for r in self.requests if r.url.path.startswith("/search")]


def provider(server, **config):
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return GitHubToolProvider(GitHubConfig(token="ghp_test", **config), client)


def call(prov, name, **arguments):
    return run(prov.execute(ToolCall(name, arguments), "github"))


def test_get_authenticated_user_reports_login_and_repository_counts():
    server = Server()
    out = call(provider(server), "github.get_authenticated_user")
    assert out == {
        "login": "octo-dev",
        "name": "Octo Dev",
        "default_username": "octo-dev",
        "url": "https://github.com/octo-dev",
        "public_repos": 12,
        "followers": 7,
        "owned_private_repos": 3,
        "total_private_repos": 5,
    }
    assert server.paths() == ["/user"]
    assert server.requests[0].headers["authorization"] == "Bearer ghp_test"


def test_private_counts_are_omitted_when_github_does_not_report_them():
    user = {k: v for k, v in USER.items() if "private" not in k}
    out = call(
        provider(Server(user=httpx.Response(200, json=user))), "github.get_authenticated_user"
    )
    assert "owned_private_repos" not in out and "total_private_repos" not in out
    assert out["public_repos"] == 12


def test_user_at_me_is_replaced_by_the_login_in_repository_search():
    server = Server()
    call(provider(server), "github.search_repository", query="user:@me language:python")
    assert server.paths() == ["/user", "/search/repositories"]
    assert server.queries() == ["user:octo-dev language:python"]


def test_user_at_me_is_replaced_in_code_search_too():
    server = Server()
    call(provider(server), "github.search_code", query="TODO user:@me")
    assert server.queries() == ["TODO user:octo-dev"]


def test_the_login_is_fetched_once():
    server = Server()
    prov = provider(server)
    call(prov, "github.search_repository", query="user:@me")
    call(prov, "github.search_repository", query="user:@me stars:>5")
    assert server.paths().count("/user") == 1
    assert server.queries() == ["user:octo-dev", "user:octo-dev stars:>5"]


@pytest.mark.parametrize("query", ["user:someone", "language:python", "email me@me.com"])
def test_other_queries_are_sent_unchanged_without_asking_who_you_are(query):
    server = Server()
    call(provider(server), "github.search_repository", query=query)
    assert server.paths() == ["/search/repositories"]
    assert server.queries() == [query]


def test_a_login_that_is_not_a_valid_name_is_refused():
    server = Server(user=httpx.Response(200, json={"login": "a/b?x=1"}))
    with pytest.raises(Exception, match="not a valid GitHub name"):
        call(provider(server), "github.search_repository", query="user:@me")
    assert server.queries() == []


def test_a_missing_login_is_an_error():
    server = Server(user=httpx.Response(200, json={}))
    with pytest.raises(ToolError, match="did not report the login"):
        call(provider(server), "github.search_repository", query="user:@me")


def test_a_422_tells_the_model_why():
    body = {
        "message": "Validation Failed",
        "errors": [{"message": "The listed users cannot be searched.", "code": "invalid"}],
    }
    server = Server(search=httpx.Response(422, json=body))
    with pytest.raises(ToolError) as exc:
        call(provider(server), "github.search_repository", query="user:nobody-here")
    text = str(exc.value)
    assert "HTTP 422" in text and "Validation Failed" in text
    assert "The listed users cannot be searched" in text


def test_the_token_never_appears_in_an_error():
    server = Server(search=httpx.Response(422, json={"message": "Validation Failed"}))
    with pytest.raises(ToolError) as exc:
        call(provider(server), "github.search_repository", query="x")
    assert "ghp_test" not in str(exc.value)


def test_every_github_tool_the_github_agent_may_use_is_served():
    """A permitted tool without a provider would make `openclaw run github` refuse to start."""
    agent = run(YamlAgentRepository(AGENTS).get("github"))
    permitted = agent.tool_permissions.allowed | agent.tool_permissions.approval_required
    served = GitHubToolProvider(GitHubConfig(token="t")).tool_names | {"memory.update"}
    assert permitted <= served
    assert "github.get_authenticated_user" in agent.tool_permissions.allowed


# -- GITHUB_USERNAME -------------------------------------------------------------------------
def test_config_reads_the_username_from_the_environment():
    config = GitHubConfig.from_env({"GITHUB_TOKEN": "t", "GITHUB_USERNAME": " @octo-dev "})
    assert config.username == "octo-dev"
    assert GitHubConfig.from_env({"GITHUB_TOKEN": "t"}).username == ""


@pytest.mark.parametrize("bad", ["a/b", "octo dev", "x?y=1", "../etc"])
def test_an_invalid_username_is_refused(bad):
    with pytest.raises(ValidationError, match="GITHUB_USERNAME is not a valid"):
        GitHubConfig(token="t", username=bad)


def test_a_configured_username_replaces_at_me_without_asking_github_who_you_are():
    server = Server()
    call(provider(server, username="someone-else"), "github.search_repository", query="user:@me")
    assert server.paths() == ["/search/repositories"]
    assert server.queries() == ["user:someone-else"]


def test_the_configured_username_wins_over_the_token_owner():
    server = Server()
    prov = provider(server, username="someone-else")
    out = call(prov, "github.get_authenticated_user")
    assert (out["login"], out["default_username"]) == ("octo-dev", "someone-else")
    call(prov, "github.search_code", query="TODO user:@me")
    assert server.queries() == ["TODO user:someone-else"]


def test_the_login_learned_from_the_user_call_is_reused_by_searches():
    server = Server()
    prov = provider(server)
    call(prov, "github.get_authenticated_user")
    call(prov, "github.search_repository", query="user:@me")
    assert server.paths() == ["/user", "/search/repositories"]
    assert server.queries() == ["user:octo-dev"]


def test_an_invalid_username_disables_github_tools_with_a_reason(tmp_path):
    from openclaw.entrypoints.bootstrap import build_app

    for name in ("agents", "skills"):
        (tmp_path / name).mkdir()
    env = {
        "OPENCLAW_HOME": str(tmp_path),
        "DEEPSEEK_API_KEY": "sk",
        "GITHUB_TOKEN": "t",
        "GITHUB_USERNAME": "a/b",
    }
    app = build_app(env)
    try:
        assert "github.search_repository" not in app.tools.names
        assert "GITHUB_USERNAME" in app.skipped["github"]
    finally:
        run(app.aclose())
