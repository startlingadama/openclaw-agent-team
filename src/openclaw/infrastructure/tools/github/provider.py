"""GitHub tool provider: the `github.*` tools of `agents/github/agent.yaml` over the REST API.

Risk levels (ARCHITECTURE section 8): reads are READ; creating an issue, a branch or a pull request
is WRITE; commenting is EXTERNAL_COMMUNICATION (it notifies people). The agent configuration lists
every write tool under `approval_required`, and the PolicyEngine also forces approval for
EXTERNAL_COMMUNICATION even if a configuration forgets it (ADR-015, defense in depth).

`repo` and branch names end up in URL paths: they are validated so an LLM-supplied value can never
walk out of `/repos/{owner}/{name}/...`. Issue and PR text is untrusted data written by third
parties; the LLM adapter presents tool output as data, not instructions (ADR-022).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from openclaw.domain.shared.errors import AuthenticationError, ToolError, ValidationError
from openclaw.domain.tools.model import RiskLevel, ToolSpec
from openclaw.infrastructure.tools.base import (
    ToolSet,
    bool_arg,
    clip,
    int_arg,
    json_body,
    req_str,
    request,
    str_arg,
    str_list_arg,
)

DEFAULT_BASE_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
SERVICE = "GitHub"

_SEGMENT = re.compile(r"[A-Za-z0-9_.-]{1,100}")
# `user:@me` is a `gh` CLI shorthand: the REST search API refuses it (HTTP 422).
_ME_QUALIFIER = re.compile(r"(?<!\w)user:@me(?![\w-])")
_BAD_REF = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]|\.\.|@\{|//|\.lock(/|$)")


@dataclass(frozen=True, slots=True)
class GitHubConfig:
    token: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 30.0
    max_body_chars: int = 4000
    max_patch_chars: int = 3000
    # The account "my repositories" and `user:@me` mean. Empty = the owner of the token.
    username: str = ""

    def __post_init__(self) -> None:
        if not self.token.strip():
            raise AuthenticationError("GITHUB_TOKEN is not set")
        username = self.username.strip().removeprefix("@")
        if username:
            try:
                _segment(username, "GITHUB_USERNAME")
            except ValidationError as exc:
                raise ValidationError("GITHUB_USERNAME is not a valid GitHub username") from exc
        object.__setattr__(self, "username", username)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> GitHubConfig:
        """Reads GITHUB_TOKEN, and the optional GITHUB_USERNAME and GITHUB_API_URL
        (GitHub Enterprise)."""
        env = os.environ if env is None else env
        return cls(
            token=env.get("GITHUB_TOKEN", ""),
            base_url=env.get("GITHUB_API_URL") or DEFAULT_BASE_URL,
            username=env.get("GITHUB_USERNAME", ""),
        )


# -- validation ------------------------------------------------------------------------------
def _segment(value: str, label: str) -> str:
    if not _SEGMENT.fullmatch(value) or not value.strip("."):
        raise ValidationError(f"'{label}' is not a valid GitHub name")
    return value


def _repo(args: Mapping[str, Any]) -> str:
    owner, sep, name = req_str(args, "repo", max_len=201).partition("/")
    if not sep:
        raise ValidationError("'repo' must look like 'owner/name'")
    return f"{_segment(owner, 'repo')}/{_segment(name, 'repo')}"


def _branch(value: str, label: str) -> str:
    parts = value.split("/")
    if (
        len(value) > 200
        or _BAD_REF.search(value)
        or value == "@"
        or value.startswith("-")
        or value.endswith((".", "/"))
        or any(part.startswith(".") for part in parts)
    ):
        raise ValidationError(f"'{label}' is not a valid branch name")
    return value


def _head(value: str) -> str:
    """`branch`, or `owner:branch` for a pull request from a fork."""
    owner, sep, ref = value.partition(":")
    if not sep:
        return _branch(value, "head")
    return f"{_segment(owner, 'head')}:{_branch(ref, 'head')}"


# -- specs -----------------------------------------------------------------------------------
def _spec(name: str, description: str, properties: dict[str, Any], required: list[str], risk):
    return ToolSpec(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": properties, "required": required},
        output_schema={"type": "object"},
        risk_level=risk,
    )


_REPO = {"type": "string", "description": "Repository as 'owner/name'."}
_NUMBER = {"type": "integer", "description": "Issue or pull request number."}

GET_AUTHENTICATED_USER = _spec(
    "github.get_authenticated_user",
    "Who the configured GitHub token belongs to: login, name and repository counts (public, and "
    "private when the token may see them), and default_username, the account 'my repositories' "
    "and 'user:@me' mean. Use it for 'my repositories'.",
    {},
    [],
    RiskLevel.READ,
)
SEARCH_REPOSITORY = _spec(
    "github.search_repository",
    "Search GitHub repositories. Supports qualifiers (language:python, org:name, stars:>100). "
    "'user:@me' means the configured GitHub username, else the owner of the token. Only "
    "repositories the token can see are listed.",
    {
        "query": {"type": "string"},
        "limit": {"type": "integer", "description": "Results to return (1-30, default 10)."},
    },
    ["query"],
    RiskLevel.READ,
)
SEARCH_CODE = _spec(
    "github.search_code",
    "Search code on GitHub. The query must include a scope qualifier such as "
    "'repo:owner/name', 'org:name' or 'user:name'. Returns matching files with text fragments.",
    {
        "query": {"type": "string"},
        "limit": {"type": "integer", "description": "Results to return (1-30, default 10)."},
    },
    ["query"],
    RiskLevel.READ,
)
GET_ISSUE = _spec(
    "github.get_issue",
    "Read one issue (title, state, labels, body), optionally with its latest comments.",
    {
        "repo": _REPO,
        "number": _NUMBER,
        "include_comments": {"type": "boolean", "description": "Also return up to 20 comments."},
    },
    ["repo", "number"],
    RiskLevel.READ,
)
GET_PULL_REQUEST = _spec(
    "github.get_pull_request",
    "Read one pull request (state, branches, stats, body) with the changed files and their "
    "diffs (each diff is cut; at most 30 files).",
    {
        "repo": _REPO,
        "number": _NUMBER,
        "include_files": {
            "type": "boolean",
            "description": "Include changed files (default true).",
        },
    },
    ["repo", "number"],
    RiskLevel.READ,
)
CREATE_ISSUE = _spec(
    "github.create_issue",
    "Create an issue. Requires human approval.",
    {
        "repo": _REPO,
        "title": {"type": "string"},
        "body": {"type": "string"},
        "labels": {"type": "array", "items": {"type": "string"}},
    },
    ["repo", "title"],
    RiskLevel.WRITE,
)
COMMENT_ISSUE = _spec(
    "github.comment_issue",
    "Comment on an issue or pull request. Requires human approval.",
    {"repo": _REPO, "number": _NUMBER, "body": {"type": "string"}},
    ["repo", "number", "body"],
    RiskLevel.EXTERNAL_COMMUNICATION,
)
CREATE_BRANCH = _spec(
    "github.create_branch",
    "Create a branch from another branch (default: the repository's default branch). "
    "Requires human approval.",
    {
        "repo": _REPO,
        "branch": {"type": "string", "description": "Name of the new branch."},
        "from_branch": {
            "type": "string",
            "description": "Source branch (default branch if omitted).",
        },
    },
    ["repo", "branch"],
    RiskLevel.WRITE,
)
CREATE_PULL_REQUEST = _spec(
    "github.create_pull_request",
    "Open a pull request from an existing branch. Requires human approval.",
    {
        "repo": _REPO,
        "title": {"type": "string"},
        "head": {
            "type": "string",
            "description": "Branch with the changes ('owner:branch' for a fork).",
        },
        "base": {"type": "string", "description": "Branch to merge into."},
        "body": {"type": "string"},
        "draft": {"type": "boolean"},
    },
    ["repo", "title", "head", "base"],
    RiskLevel.WRITE,
)


class GitHubToolProvider(ToolSet):
    def __init__(self, config: GitHubConfig, client: httpx.AsyncClient | None = None) -> None:
        super().__init__()
        self._config = config
        self._client = client or httpx.AsyncClient(timeout=config.timeout)
        self._owns_client = client is None
        self._login: str | None = None  # the token's owner, fetched once
        for spec, handler in (
            (GET_AUTHENTICATED_USER, self._get_authenticated_user),
            (SEARCH_REPOSITORY, self._search_repository),
            (SEARCH_CODE, self._search_code),
            (GET_ISSUE, self._get_issue),
            (GET_PULL_REQUEST, self._get_pull_request),
            (CREATE_ISSUE, self._create_issue),
            (COMMENT_ISSUE, self._comment_issue),
            (CREATE_BRANCH, self._create_branch),
            (CREATE_PULL_REQUEST, self._create_pull_request),
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
        accept: str = "application/vnd.github+json",
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self._config.token}",
            "Accept": accept,
            "X-GitHub-Api-Version": API_VERSION,
        }
        response = await request(
            self._client,
            SERVICE,
            method,
            self._config.base_url.rstrip("/") + path,
            headers=headers,
            params=params,
            json=payload,
        )
        return json_body(SERVICE, response)

    def _body(self, text: Any) -> str:
        return clip(text, self._config.max_body_chars) if isinstance(text, str) else ""

    async def _me(self) -> str:
        """The account `user:@me` stands for: GITHUB_USERNAME, else the token's owner."""
        if self._config.username:
            return self._config.username
        if self._login is None:
            login = (await self._call("GET", "/user")).get("login")
            if not isinstance(login, str):
                raise ToolError("GitHub did not report the login of the token's owner")
            self._login = _segment(login, "login")
        return self._login

    async def _resolve_me(self, query: str) -> str:
        """Replaces `user:@me` by the login it stands for (the owner is asked only once)."""
        if not _ME_QUALIFIER.search(query):
            return query
        return _ME_QUALIFIER.sub(f"user:{await self._me()}", query)

    # -- reads ------------------------------------------------------------------------------
    async def _get_authenticated_user(self, args: Mapping[str, Any]) -> Any:
        user = await self._call("GET", "/user")
        if self._login is None and isinstance(user.get("login"), str):
            self._login = user["login"]
        result = {
            "login": user.get("login"),
            "default_username": self._config.username or user.get("login"),
            "name": user.get("name"),
            "url": user.get("html_url"),
            "public_repos": user.get("public_repos"),
            "followers": user.get("followers"),
        }
        # Only reported when the token may see private repositories.
        for key in ("owned_private_repos", "total_private_repos"):
            if key in user:
                result[key] = user[key]
        return result

    async def _search_repository(self, args: Mapping[str, Any]) -> Any:
        query = await self._resolve_me(req_str(args, "query", max_len=256))
        limit = int_arg(args, "limit", default=10, maximum=30)
        data = await self._call(
            "GET", "/search/repositories", params={"q": query, "per_page": limit}
        )
        return {
            "total_count": data.get("total_count", 0),
            "items": [
                {
                    "full_name": r.get("full_name"),
                    "description": clip(r.get("description") or "", 300),
                    "stars": r.get("stargazers_count"),
                    "language": r.get("language"),
                    "topics": r.get("topics", [])[:10],
                    "updated_at": r.get("updated_at"),
                    "url": r.get("html_url"),
                }
                for r in data.get("items", [])
            ],
        }

    async def _search_code(self, args: Mapping[str, Any]) -> Any:
        query = await self._resolve_me(req_str(args, "query", max_len=256))
        limit = int_arg(args, "limit", default=10, maximum=30)
        data = await self._call(
            "GET",
            "/search/code",
            params={"q": query, "per_page": limit},
            accept="application/vnd.github.text-match+json",
        )
        return {
            "total_count": data.get("total_count", 0),
            "items": [
                {
                    "repository": (item.get("repository") or {}).get("full_name"),
                    "path": item.get("path"),
                    "url": item.get("html_url"),
                    "fragments": [
                        clip(m.get("fragment", ""), 500) for m in item.get("text_matches", [])[:3]
                    ],
                }
                for item in data.get("items", [])
            ],
        }

    async def _get_issue(self, args: Mapping[str, Any]) -> Any:
        repo, number = _repo(args), int_arg(args, "number")
        with_comments = bool_arg(args, "include_comments")
        issue = await self._call("GET", f"/repos/{repo}/issues/{number}")
        result = {
            "number": issue.get("number"),
            "title": issue.get("title"),
            "state": issue.get("state"),
            "is_pull_request": "pull_request" in issue,
            "author": (issue.get("user") or {}).get("login"),
            "labels": [label.get("name") for label in issue.get("labels", [])],
            "assignees": [a.get("login") for a in issue.get("assignees", [])],
            "comments_count": issue.get("comments"),
            "created_at": issue.get("created_at"),
            "updated_at": issue.get("updated_at"),
            "url": issue.get("html_url"),
            "body": self._body(issue.get("body")),
        }
        if with_comments:
            comments = await self._call(
                "GET", f"/repos/{repo}/issues/{number}/comments", params={"per_page": 20}
            )
            result["comments"] = [
                {
                    "author": (c.get("user") or {}).get("login"),
                    "created_at": c.get("created_at"),
                    "body": clip(c.get("body") or "", 1500),
                }
                for c in comments
            ]
        return result

    async def _get_pull_request(self, args: Mapping[str, Any]) -> Any:
        repo, number = _repo(args), int_arg(args, "number")
        with_files = bool_arg(args, "include_files", True)
        pr = await self._call("GET", f"/repos/{repo}/pulls/{number}")
        result = {
            "number": pr.get("number"),
            "title": pr.get("title"),
            "state": pr.get("state"),
            "draft": pr.get("draft"),
            "merged": pr.get("merged"),
            "author": (pr.get("user") or {}).get("login"),
            "base": (pr.get("base") or {}).get("ref"),
            "head": (pr.get("head") or {}).get("ref"),
            "commits": pr.get("commits"),
            "additions": pr.get("additions"),
            "deletions": pr.get("deletions"),
            "changed_files": pr.get("changed_files"),
            "url": pr.get("html_url"),
            "body": self._body(pr.get("body")),
        }
        if with_files:
            files = await self._call(
                "GET", f"/repos/{repo}/pulls/{number}/files", params={"per_page": 30}
            )
            result["files"] = [
                {
                    "filename": f.get("filename"),
                    "status": f.get("status"),
                    "additions": f.get("additions"),
                    "deletions": f.get("deletions"),
                    "patch": clip(f.get("patch") or "", self._config.max_patch_chars),
                }
                for f in files
            ]
            result["files_truncated"] = (pr.get("changed_files") or 0) > len(files)
        return result

    # -- writes (approval is decided by the PolicyEngine, not here) ---------------------------
    async def _create_issue(self, args: Mapping[str, Any]) -> Any:
        repo = _repo(args)
        payload: dict[str, Any] = {"title": req_str(args, "title", max_len=256)}
        if body := str_arg(args, "body", required=False, max_len=65_000):
            payload["body"] = body
        if labels := str_list_arg(args, "labels"):
            payload["labels"] = labels
        issue = await self._call("POST", f"/repos/{repo}/issues", payload=payload)
        return {"number": issue.get("number"), "url": issue.get("html_url")}

    async def _comment_issue(self, args: Mapping[str, Any]) -> Any:
        repo, number = _repo(args), int_arg(args, "number")
        payload = {"body": req_str(args, "body", max_len=65_000)}
        comment = await self._call(
            "POST", f"/repos/{repo}/issues/{number}/comments", payload=payload
        )
        return {"id": comment.get("id"), "url": comment.get("html_url")}

    async def _create_branch(self, args: Mapping[str, Any]) -> Any:
        repo = _repo(args)
        branch = _branch(req_str(args, "branch", max_len=200), "branch")
        source = str_arg(args, "from_branch", required=False, max_len=200)
        if source is None:
            source = (await self._call("GET", f"/repos/{repo}")).get("default_branch")
            if not isinstance(source, str):
                raise ToolError("GitHub did not report the default branch of the repository")
        source = _branch(source, "from_branch")
        ref = await self._call("GET", f"/repos/{repo}/git/ref/heads/{quote(source, safe='/')}")
        sha = (ref.get("object") or {}).get("sha")
        if not isinstance(sha, str):
            raise ToolError(f"GitHub returned no commit for branch '{source}'")
        await self._call(
            "POST", f"/repos/{repo}/git/refs", payload={"ref": f"refs/heads/{branch}", "sha": sha}
        )
        return {"branch": branch, "from_branch": source, "sha": sha}

    async def _create_pull_request(self, args: Mapping[str, Any]) -> Any:
        repo = _repo(args)
        payload: dict[str, Any] = {
            "title": req_str(args, "title", max_len=256),
            "head": _head(req_str(args, "head", max_len=300)),
            "base": _branch(req_str(args, "base", max_len=200), "base"),
            "draft": bool_arg(args, "draft"),
        }
        if body := str_arg(args, "body", required=False, max_len=65_000):
            payload["body"] = body
        pr = await self._call("POST", f"/repos/{repo}/pulls", payload=payload)
        return {
            "number": pr.get("number"),
            "url": pr.get("html_url"),
            "state": pr.get("state"),
            "draft": pr.get("draft"),
        }
