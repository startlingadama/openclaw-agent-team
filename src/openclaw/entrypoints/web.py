"""CLI entrypoint factory for the HTTP/WebChat channel.

HTTP transport, task state, event translation and channel approvals live in
``infrastructure.channels.webchat``. Concrete adapters are composed by ``bootstrap``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from openclaw.entrypoints.bootstrap import build_webchat_api, build_webchat_server

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def build_http_api(
    env: Mapping[str, str] | None = None,
    *,
    llm: Any = None,
    approvals: Any = None,
) -> Any:
    """Compatibility factory used by API callers and integration tests."""
    return build_webchat_api(env, llm=llm, approvals=approvals)


def run_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    env: Mapping[str, str] | None = None,
    *,
    llm: Any = None,
    approvals: Any = None,
) -> OpenClawHTTPServer:
    """Create the HTTP server; the CLI owns the serve-forever lifecycle."""
    return OpenClawHTTPServer(host, port, env, llm=llm, approvals=approvals)


class OpenClawHTTPServer:
    """Thin entrypoint wrapper around the infrastructure HTTP transport."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        env: Mapping[str, str] | None = None,
        *,
        llm: Any = None,
        approvals: Any = None,
    ) -> None:
        self._server_adapter = build_webchat_server(
            host,
            port,
            env,
            llm=llm,
            approvals=approvals,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._server_adapter, name)


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "OpenClawHTTPServer",
    "build_http_api",
    "run_server",
]
