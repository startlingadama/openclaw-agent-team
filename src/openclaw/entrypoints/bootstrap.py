"""Composition root: the only place that knows every concrete adapter.

It reads the environment, builds the adapters, and injects them into the application layer
(ARCHITECTURE section 21, ADR-011). Nothing else in the code base constructs an adapter.

What gets built:

- LLM: DeepSeek (`LLM_PROVIDER`, `DEEPSEEK_API_KEY`, ...). Mandatory: no key, no app.
- Memory: Markdown files under `agents/<id>/` and `workspace/shared/` (ADR-005).
- Skills: Agent Skills directories under `skills/` (ADR-006, ADR-007).
- Trace: every execution is written under `executions/` (`execution.json`, `events.jsonl`,
  `result.md`; ARCHITECTURE section 19), unless `OPENCLAW_TRACE=off`. `-v` also prints the events.
- Teams: `teams/<id>/team.yaml`. `team.members` and `team.delegate` are always registered; only
  the supervisor of the team (`OPENCLAW_TEAM`, default `default`) may use them successfully.
- Tools: `memory.update` and `web.*` always; `github.*`, `google.*` and `linkedin.*` only when
  their credentials are present; `code.*` only when `OPENCLAW_CODE_SANDBOX` is set and the
  sandbox is usable on this machine. A provider that is absent, or configured but invalid, is
  reported in `App.skipped` instead of stopping the app: an agent only fails, before its first
  LLM call, if it needs a tool that no provider serves (see `RunAgent`).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from openclaw.application.agents.action_executor import RetryPolicy
from openclaw.application.agents.run_agent import RunAgent
from openclaw.application.agents.runtime import AgentRuntime, RuntimeConfig
from openclaw.application.agents.script_tools import ScriptToolBroker
from openclaw.application.memory.tools import MemoryToolProvider
from openclaw.application.tasks.channel import ChannelBackend
from openclaw.application.tasks.history import ReadHistory
from openclaw.application.tasks.run_task import RunTask
from openclaw.application.teams.delegate import DelegateTask
from openclaw.application.teams.run_team import RunTeam
from openclaw.application.teams.tools import TeamToolProvider
from openclaw.domain.agents.ports import LLMPort
from openclaw.domain.shared.errors import OpenClawError, ValidationError
from openclaw.domain.tasks.ports import ApprovalPort, EventSink
from openclaw.domain.tools.registry import ToolProvider, ToolRegistry
from openclaw.infrastructure.agents import YamlAgentRepository
from openclaw.infrastructure.channels.cli.approval import CliApprovalPort
from openclaw.infrastructure.channels.telegram import (
    TaskDocuments,
    TelegramAdapter,
    TelegramApprovalPort,
    TelegramBot,
    TelegramClient,
    TelegramConfig,
)
from openclaw.infrastructure.channels.webchat import (
    TaskStore,
    WebChatAPI,
    WebChatApprovalController,
    WebChatEventSink,
)
from openclaw.infrastructure.channels.webchat.server import OpenClawHTTPServer as WebChatHTTPServer
from openclaw.infrastructure.llm.deepseek import DeepSeekAdapter, DeepSeekConfig
from openclaw.infrastructure.memory.markdown.repository import MarkdownMemoryRepository
from openclaw.infrastructure.observability.composite import CompositeEventSink
from openclaw.infrastructure.observability.console import ConsoleEventSink
from openclaw.infrastructure.observability.history import FileExecutionHistory
from openclaw.infrastructure.observability.trace import FileTraceSink
from openclaw.infrastructure.skills.loader import SkillLoader
from openclaw.infrastructure.teams import YamlTeamRepository
from openclaw.infrastructure.tools.code import CodeConfig, CodeToolProvider
from openclaw.infrastructure.tools.docs import DocsConfig, DocsToolProvider
from openclaw.infrastructure.tools.docs.files import DocumentFiles
from openclaw.infrastructure.tools.github import GitHubConfig, GitHubToolProvider
from openclaw.infrastructure.tools.google import GmailToolProvider, GoogleCredentials
from openclaw.infrastructure.tools.linkedin import LinkedInConfig, LinkedInToolProvider
from openclaw.infrastructure.tools.web import WebConfig, WebToolProvider

log = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 25
DEFAULT_TEAM = "default"
_OFF = frozenset({"0", "off", "false", "no"})
TOOL_RETRY_ATTEMPTS = 2  # READ tools only: ActionExecutor never repeats anything else


@dataclass(frozen=True, slots=True)
class Settings:
    """Where things live. `OPENCLAW_HOME` (default: the current directory) holds `agents/` and
    `skills/`; `OPENCLAW_WORKSPACE` is relative to it."""

    home: Path
    workspace: Path
    max_steps: int = DEFAULT_MAX_STEPS
    team: str = DEFAULT_TEAM
    trace: bool = True

    @property
    def agents_dir(self) -> Path:
        return self.home / "agents"

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    @property
    def teams_dir(self) -> Path:
        return self.home / "teams"

    @property
    def executions_dir(self) -> Path:
        return self.home / "executions"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        home = Path(env.get("OPENCLAW_HOME", "").strip() or ".").resolve()
        workspace = home / (env.get("OPENCLAW_WORKSPACE", "").strip() or "workspace")
        raw = env.get("OPENCLAW_MAX_STEPS", "").strip()
        try:
            max_steps = int(raw) if raw else DEFAULT_MAX_STEPS
        except ValueError:
            max_steps = 0
        if max_steps < 1:
            raise ValidationError("OPENCLAW_MAX_STEPS must be an integer >= 1")
        team = env.get("OPENCLAW_TEAM", "").strip() or DEFAULT_TEAM
        trace = env.get("OPENCLAW_TRACE", "").strip().lower() not in _OFF
        return cls(home, workspace, max_steps, team, trace)


@dataclass(frozen=True, slots=True)
class App:
    run_agent: RunAgent
    tools: ToolRegistry
    skipped: Mapping[str, str] = field(default_factory=dict)  # provider/tool -> why not available
    _closers: tuple[Callable[[], Awaitable[None]], ...] = ()
    run_team: RunTeam | None = None  # always set by `build_app`
    agents: YamlAgentRepository | None = None
    teams: YamlTeamRepository | None = None
    skills: SkillLoader | None = None
    history: ReadHistory | None = None
    memory: MarkdownMemoryRepository | None = None
    run_task: RunTask = field(default_factory=RunTask)

    async def aclose(self) -> None:
        for close in self._closers:
            await close()


def build_app(
    env: Mapping[str, str] | None = None,
    *,
    verbose: bool = False,
    llm: LLMPort | None = None,
    approvals: ApprovalPort | None = None,
    sink: EventSink | None = None,
) -> App:
    """Wire the application. Raises AuthenticationError if the LLM key is missing and
    ValidationError for an unusable setting. `llm`, `approvals` and `sink` replace the default
    adapters (tests, other channels)."""
    env = os.environ if env is None else env
    settings = Settings.from_env(env)
    closers: list[Callable[[], Awaitable[None]]] = []

    # Validated first, before any client exists: a missing key stops the build cleanly.
    deepseek_config = None if llm is not None else _deepseek_config(env)

    memory = MarkdownMemoryRepository(settings.agents_dir, settings.workspace)
    skipped: dict[str, str] = {}
    providers: list[ToolProvider] = [MemoryToolProvider(memory)]
    providers += _tool_providers(env, skipped, closers, settings)
    registry = ToolRegistry(providers)

    if llm is None:
        assert deepseek_config is not None
        adapter = DeepSeekAdapter(deepseek_config)
        closers.append(adapter.aclose)
        llm = adapter

    approval_port = approvals or CliApprovalPort()
    event_sink = sink or _default_sink(settings, verbose)
    runtime = AgentRuntime(
        llm=llm,
        tools=registry,
        skills=SkillLoader(settings.skills_dir),
        memory=memory,
        approvals=approval_port,
        sink=event_sink,
        retry=RetryPolicy(max_attempts=TOOL_RETRY_ATTEMPTS),
        config=RuntimeConfig(max_steps=settings.max_steps),
    )
    agents = YamlAgentRepository(settings.agents_dir)
    run_agent = RunAgent(agents=agents, runtime=runtime, tools=registry)
    # What a script of the sandbox calls goes through the same policy and approvals (ADR-025).
    broker = ScriptToolBroker(
        agents=agents,
        tools=registry,
        approvals=approval_port,
        sink=event_sink,
        retry=RetryPolicy(max_attempts=TOOL_RETRY_ATTEMPTS),
    )
    for provider in providers:
        if isinstance(provider, CodeToolProvider):
            provider.attach(broker)
    # Registered last: delegation runs agents through `run_agent`, which needs the registry.
    teams = YamlTeamRepository(settings.teams_dir)
    delegate = DelegateTask(
        teams=teams,
        agents=agents,
        run_agent=run_agent,
        default_team=settings.team,
    )
    registry.register(TeamToolProvider(delegate))
    run_team = RunTeam(teams=teams, run_agent=run_agent)
    history = load_history(env)
    memory = MarkdownMemoryRepository(settings.agents_dir, settings.workspace)
    return App(
        run_agent=run_agent,
        tools=registry,
        skipped=skipped,
        _closers=tuple(closers),
        run_team=run_team,
        agents=agents,
        teams=teams,
        skills=SkillLoader(settings.skills_dir),
        history=history,
        memory=memory,
    )


def build_webchat_app(
    env: Mapping[str, str] | None = None,
    *,
    llm: LLMPort | None = None,
    event_handler: Callable[[Any], Awaitable[None]],
    approvals: ApprovalPort | None = None,
) -> tuple[App, ApprovalPort]:
    """Build the shared runtime with WebChat-specific infrastructure ports."""
    env = os.environ if env is None else env
    settings = Settings.from_env(env)
    approval_port = approvals or WebChatApprovalController()

    async def record_event(event: Any) -> None:
        if isinstance(approval_port, WebChatApprovalController):
            approval_port.register_execution(event.execution_id, event.task_id)
        await event_handler(event)

    webchat_sink = WebChatEventSink(record_event)
    trace_sink = _default_sink(settings, verbose=False)
    sink = CompositeEventSink(trace_sink, webchat_sink) if trace_sink is not None else webchat_sink
    return build_app(env, llm=llm, approvals=approval_port, sink=sink), approval_port


def build_webchat_api(
    env: Mapping[str, str] | None = None,
    *,
    llm: LLMPort | None = None,
    approvals: ApprovalPort | None = None,
) -> WebChatAPI:
    """Compose the WebChat API adapter with the shared application and channel ports."""
    env = os.environ if env is None else env
    tasks = TaskStore()
    app, approval_port = build_webchat_app(
        env,
        llm=llm,
        event_handler=tasks.record_event,
        approvals=approvals,
    )
    # PDF and LaTeX sources of the writer agent: served from the documents directory only.
    default_root = Settings.from_env(env).workspace / "shared" / "reports"
    documents = DocumentFiles(DocsConfig.root_from_env(env, default_root))
    return WebChatAPI(app=app, tasks=tasks, approvals=approval_port, documents=documents)


def build_webchat_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    env: Mapping[str, str] | None = None,
    *,
    llm: LLMPort | None = None,
    approvals: ApprovalPort | None = None,
) -> WebChatHTTPServer:
    """Compose the WebChat HTTP transport and its API facade."""
    return WebChatHTTPServer(build_webchat_api(env, llm=llm, approvals=approvals), host, port)


def build_telegram_bot(
    env: Mapping[str, str] | None = None,
    *,
    llm: LLMPort | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TelegramBot:
    """Compose the Telegram channel: long-polling adapter, approvals in the chat, shared runtime.

    Raises AuthenticationError without `TELEGRAM_BOT_TOKEN` and ValidationError without
    `TELEGRAM_ALLOWED_USER_IDS`. `transport` replaces the HTTP transport (tests).
    """
    env = os.environ if env is None else env
    settings = Settings.from_env(env)
    config = TelegramConfig.from_env(env, default_team=settings.team)
    client = TelegramClient(config, transport=transport)
    approvals = TelegramApprovalPort(client, timeout=config.approval_timeout)
    # The files of the writer are sent after the answer (ADR-030): the same directory and the same
    # checks as the WebChat download route, the events of the task tell which files it produced.
    default_root = settings.workspace / "shared" / "reports"
    documents = TaskDocuments(DocumentFiles(DocsConfig.root_from_env(env, default_root)))
    trace_sink = _default_sink(settings, verbose=False)
    sink = CompositeEventSink(trace_sink, documents) if trace_sink is not None else documents
    app = build_app(env, llm=llm, approvals=approvals, sink=sink)  # the client made no request yet
    assert app.run_team is not None and app.agents is not None
    backend = ChannelBackend(
        run_task=app.run_task,
        run_agent=app.run_agent,
        run_team=app.run_team,
        agents=app.agents,
        team_id=config.team,
    )
    adapter = TelegramAdapter(
        client=client,
        backend=backend,
        approvals=approvals,
        config=config,
        documents=documents,
    )
    return TelegramBot(adapter, client.aclose, app.aclose)


def _default_sink(settings: Settings, verbose: bool) -> EventSink | None:
    """Trace to files (unless switched off) and, with `-v`, one line per event on stderr."""
    sinks: list[EventSink] = []
    if verbose:
        sinks.append(ConsoleEventSink())
    if settings.trace:
        sinks.append(FileTraceSink(settings.executions_dir))
    if len(sinks) > 1:
        return CompositeEventSink(*sinks)
    return sinks[0] if sinks else None


def load_teams(env: Mapping[str, str] | None = None) -> YamlTeamRepository:
    """The team definitions alone: listing teams needs no LLM key and no tool provider."""
    env = os.environ if env is None else env
    return YamlTeamRepository(Settings.from_env(env).teams_dir)


def load_history(env: Mapping[str, str] | None = None) -> ReadHistory:
    """Past executions alone: reading them needs no LLM key and no tool provider."""
    env = os.environ if env is None else env
    return ReadHistory(FileExecutionHistory(Settings.from_env(env).executions_dir))


def _deepseek_config(env: Mapping[str, str]) -> DeepSeekConfig:
    provider = env.get("LLM_PROVIDER", "").strip().lower() or "deepseek"
    if provider != "deepseek":
        raise ValidationError(f"LLM_PROVIDER '{provider}' is not supported (available: deepseek)")
    return DeepSeekConfig.from_env(env)


def _tool_providers(
    env: Mapping[str, str],
    skipped: dict[str, str],
    closers: list[Callable[[], Awaitable[None]]],
    settings: Settings,
) -> list[ToolProvider]:
    providers: list[ToolProvider] = []

    def add(name: str, needs: str, factory: Callable[[], ToolProvider]) -> None:
        if not env.get(needs, "").strip():
            skipped[name] = f"not configured ({needs} is not set)"
            log.debug("%s tools disabled: %s is not set", name, needs)
            return
        try:
            provider = factory()
        except OpenClawError as exc:  # present but unusable: reported, never fatal
            skipped[name] = f"invalid configuration: {exc}"
            log.warning("%s tools disabled: %s", name, exc)
            return
        providers.append(provider)
        closers.append(provider.aclose)  # type: ignore[attr-defined]

    # web.open / web.extract need no key; web.search is only offered when a search key is set.
    try:
        web = WebToolProvider(WebConfig.from_env(env))
    except OpenClawError as exc:
        skipped["web"] = f"invalid configuration: {exc}"
        log.warning("web tools disabled: %s", exc)
    else:
        providers.append(web)
        closers.append(web.aclose)
        if "web.search" not in web.tool_names:
            skipped["web.search"] = (
                "no key for the configured search providers "
                "(BRAVE_SEARCH_API_KEY, TAVILY_API_KEY; order in WEB_SEARCH_PROVIDERS)"
            )

    add("github", "GITHUB_TOKEN", lambda: GitHubToolProvider(GitHubConfig.from_env(env)))
    add(
        "google",
        "GOOGLE_TOKEN_FILE",
        lambda: GmailToolProvider(GoogleCredentials.from_env(env)),
    )
    add(
        "linkedin",
        "LINKEDIN_ACCESS_TOKEN",
        lambda: LinkedInToolProvider(LinkedInConfig.from_env(env)),
    )
    # code.* run in the sandbox (ADR-025): only offered when a mechanism is chosen and usable here.
    add(
        "code",
        "OPENCLAW_CODE_SANDBOX",
        lambda: CodeToolProvider(CodeConfig.from_env(env, settings.workspace / "sandbox")),
    )
    # docs.* (writer agent, ADR-026) need no credential. docs.compile_pdf is only offered where a
    # LaTeX engine is installed and the compilation can be isolated: its absence is reported.
    try:
        docs = DocsToolProvider(DocsConfig.from_env(env, settings.workspace / "shared" / "reports"))
    except OpenClawError as exc:
        skipped["docs"] = f"invalid configuration: {exc}"
        log.warning("docs tools disabled: %s", exc)
    else:
        providers.append(docs)
        closers.append(docs.aclose)
        if docs.compile_unavailable:
            skipped["docs.compile_pdf"] = docs.compile_unavailable
    return providers
