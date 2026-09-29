"""LinkedIn tool provider: the tools of `agents/linkedin/agent.yaml` that the official API supports.

Served:
- `linkedin.get_profile`  (READ)  the authenticated member's own profile (OpenID `/v2/userinfo`).
- `linkedin.publish_post` (EXTERNAL_COMMUNICATION, always human-approved) a text post on the
  member's own feed (`POST /rest/posts`, scope `w_member_social`).

NOT served: `linkedin.search_profile` and `linkedin.send_message`. With a self-serve developer app
the official API offers no people search, no reading of other members' profiles and no messaging;
those need a LinkedIn partner program (per LinkedIn's API terms and third-party integrations).
Scraping is not an alternative: it violates LinkedIn's terms. A tool that is not registered is
invisible to the agent (`ToolResolver.visible_specs`), so it never wastes steps on a call that can
only fail; a partner-API or third-party adapter can register these two names later without any
change to the runtime (ADR-020).

Posts use LinkedIn's versioned API: the `LinkedIn-Version` header (YYYYMM) is configuration
(`LINKEDIN_API_VERSION`) because versions are retired about a year after release.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from openclaw.domain.shared.errors import AuthenticationError, ToolError, ValidationError
from openclaw.domain.tools.model import RiskLevel, ToolSpec
from openclaw.infrastructure.tools.base import ToolSet, json_body, req_str, request

DEFAULT_BASE_URL = "https://api.linkedin.com"
DEFAULT_API_VERSION = "202607"
SERVICE = "LinkedIn"
MAX_POST_CHARS = 3000

# Characters with a meaning in LinkedIn's post markup: unescaped, they can cut a post short.
_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")


@dataclass(frozen=True, slots=True)
class LinkedInConfig:
    access_token: str = field(repr=False)
    api_version: str = DEFAULT_API_VERSION
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 30.0

    def __post_init__(self) -> None:
        if not self.access_token.strip():
            raise AuthenticationError("LINKEDIN_ACCESS_TOKEN is not set")
        if not re.fullmatch(r"\d{6}", self.api_version):
            raise ValidationError("LINKEDIN_API_VERSION must look like YYYYMM, e.g. 202607")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LinkedInConfig:
        env = os.environ if env is None else env
        return cls(
            access_token=env.get("LINKEDIN_ACCESS_TOKEN", ""),
            api_version=env.get("LINKEDIN_API_VERSION") or DEFAULT_API_VERSION,
        )


def escape_post_text(text: str) -> str:
    """Escapes LinkedIn's reserved characters. Hashtags and @mentions are therefore published as
    plain text, not as links: the safe choice, since an unescaped one can truncate the post."""
    return _RESERVED.sub(r"\\\1", text)


GET_PROFILE = ToolSpec(
    name="linkedin.get_profile",
    description=(
        "Read the profile of the LinkedIn member who authorized this agent (name, picture, "
        "locale, email). Other members' profiles are not available through the official API."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
    output_schema={"type": "object"},
    risk_level=RiskLevel.READ,
)
PUBLISH_POST = ToolSpec(
    name="linkedin.publish_post",
    description=(
        f"Publish a text post (max {MAX_POST_CHARS} characters) on the authorized member's public "
        "LinkedIn feed. Requires human approval; hashtags and mentions are posted as plain text."
    ),
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The post text."}},
        "required": ["text"],
    },
    output_schema={"type": "object"},
    risk_level=RiskLevel.EXTERNAL_COMMUNICATION,
)


class LinkedInToolProvider(ToolSet):
    def __init__(self, config: LinkedInConfig, client: httpx.AsyncClient | None = None) -> None:
        super().__init__()
        self._config = config
        self._client = client or httpx.AsyncClient(timeout=config.timeout)
        self._owns_client = client is None
        self._member_id: str | None = None
        self._add(GET_PROFILE, self._get_profile)
        self._add(PUBLISH_POST, self._publish_post)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _url(self, path: str) -> str:
        return self._config.base_url.rstrip("/") + path

    async def _userinfo(self) -> Mapping[str, Any]:
        response = await request(
            self._client,
            SERVICE,
            "GET",
            self._url("/v2/userinfo"),
            headers={"Authorization": f"Bearer {self._config.access_token}"},
        )
        data = json_body(SERVICE, response)
        if not isinstance(data, dict):
            raise ToolError("LinkedIn returned an unreadable profile")
        return data

    async def _get_profile(self, args: Mapping[str, Any]) -> Any:
        data = await self._userinfo()
        fields = ("name", "given_name", "family_name", "picture", "locale", "email")
        return {"id": data.get("sub"), **{k: data[k] for k in fields if data.get(k)}}

    async def _publish_post(self, args: Mapping[str, Any]) -> Any:
        text = req_str(args, "text", max_len=MAX_POST_CHARS)
        if self._member_id is None:
            member_id = (await self._userinfo()).get("sub")
            if not isinstance(member_id, str) or not member_id:
                raise ToolError("LinkedIn did not return the member id needed to publish")
            self._member_id = member_id
        response = await request(
            self._client,
            SERVICE,
            "POST",
            self._url("/rest/posts"),
            status_messages={
                426: f"LinkedIn-Version {self._config.api_version} is retired or not released "
                "yet: set LINKEDIN_API_VERSION",
            },
            headers={
                "Authorization": f"Bearer {self._config.access_token}",
                "LinkedIn-Version": self._config.api_version,
                "X-Restli-Protocol-Version": "2.0.0",
            },
            json={
                "author": f"urn:li:person:{self._member_id}",
                "commentary": escape_post_text(text),
                "visibility": "PUBLIC",
                "distribution": {
                    "feedDistribution": "MAIN_FEED",
                    "targetEntities": [],
                    "thirdPartyDistributionChannels": [],
                },
                "lifecycleState": "PUBLISHED",
                "isReshareDisabledByAuthor": False,
            },
        )
        urn = response.headers.get("x-restli-id")
        if not urn:
            raise ToolError("LinkedIn accepted the post but returned no post id")
        return {"post_urn": urn, "url": f"https://www.linkedin.com/feed/update/{urn}/"}
