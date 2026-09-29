"""Command line entrypoint: `openclaw run <agent> "<task>"`, `openclaw team run|list`,
`openclaw runs` and `openclaw show <execution>`.

Wiring lives in `bootstrap`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence
from typing import Any, Protocol

from dotenv import find_dotenv, load_dotenv

from openclaw import __version__
from openclaw.application.agents.run_agent import MissingToolsError
from openclaw.domain.shared.errors import OpenClawError
from openclaw.entrypoints.bootstrap import (
    App,
    build_app,
    build_telegram_bot,
    load_history,
    load_teams,
)

EXIT_OK, EXIT_FAILED, EXIT_ERROR = 0, 1, 2


def _load_env_file() -> None:
    """Load the project-local .env file explicitly when present."""
    env_path = find_dotenv(filename=".env", usecwd=True)
    if env_path:
        load_dotenv(env_path, override=False)


class _Outcome(Protocol):
    """What the CLI reads of an execution (entrypoints do not import the domain)."""

    status: object
    answer: str | None
    error: str | None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openclaw",
        description="OpenClaw Agent Team - multi-agent runtime.",
    )
    parser.add_argument("--version", action="version", version=f"openclaw {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    run = commands.add_parser("run", help="run one agent on a task, from start to finish")
    run.add_argument("agent", help="agent id, e.g. google-research")
    run.add_argument("task", nargs="*", help="what the agent must do")
    run.add_argument(
        "-v", "--verbose", action="store_true", help="trace the agent's steps on stderr"
    )
    run.add_argument(
        "--host", default=None, help="bind host when starting the web server with `run web`"
    )
    run.add_argument(
        "--port",
        type=int,
        default=None,
        help="bind port when starting the web server with `run web`",
    )
    runs = commands.add_parser("runs", help="list recent executions, newest first")
    runs.add_argument("-n", "--limit", type=_positive, default=20, help="how many (default 20)")
    runs.add_argument(
        "--all", action="store_true", help="also list executions started by a delegation"
    )
    show = commands.add_parser("show", help="show one execution and what it delegated")
    show.add_argument("execution", help="execution id, task id or trace name (a prefix is enough)")
    show.add_argument("--events", action="store_true", help="also list each step of every run")
    team = commands.add_parser("team", help="work with agent teams")
    team_commands = team.add_subparsers(dest="team_command", metavar="<team-command>")
    team_run = team_commands.add_parser("run", help="run a team: its supervisor gets the task")
    team_run.add_argument("team", help="team id, e.g. executive")
    team_run.add_argument("task", nargs="+", help="what the team must do")
    team_run.add_argument(
        "-v", "--verbose", action="store_true", help="trace every agent's steps on stderr"
    )
    team_commands.add_parser("list", help="list the defined teams")
    web = commands.add_parser("web", help="start the HTTP/WebChat server")
    web.add_argument("--host", default=None, help="bind host")
    web.add_argument("--port", type=int, default=None, help="bind port")
    commands.add_parser("telegram", help="start the Telegram bot (long polling)")
    return parser


def parse_args(parser: argparse.ArgumentParser, argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the command line; `run <agent> -v "task"` works like `run <agent> "task" -v`.

    argparse gives an optional positional (`task`, `nargs="*"`) an empty match before a flag that
    follows the agent, then rejects the text after the flag. That text is the task: it is
    appended to it. Anything that looks like an unknown flag is still an error.
    """
    args, extra = parser.parse_known_args(argv)
    if extra:
        if args.command == "run" and not any(item.startswith("-") for item in extra):
            args.task = [*args.task, *extra]
        else:
            parser.error(f"unrecognized arguments: {' '.join(extra)}")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    # Real environment variables win; the project-local .env is loaded explicitly.
    _load_env_file()
    _configure_logging()
    parser = build_parser()
    args = parse_args(parser, argv)
    try:
        if args.command == "run":
            if args.agent == "web":
                host = args.host or _web_host()
                port = args.port if args.port is not None else _web_port()
                return _start_web_server(host, port)
            if not args.task:
                parser.error("the following arguments are required: task")
            return asyncio.run(_run(args.agent, " ".join(args.task), args.verbose))
        if args.command == "runs":
            return asyncio.run(_runs(args.limit, args.all))
        if args.command == "show":
            return asyncio.run(_show(args.execution, args.events))
        if args.command == "team" and args.team_command == "run":
            return asyncio.run(_run_team(args.team, " ".join(args.task), args.verbose))
        if args.command == "team" and args.team_command == "list":
            return asyncio.run(_list_teams())
        if args.command == "web":
            host = args.host or _web_host()
            port = args.port if args.port is not None else _web_port()
            return _start_web_server(host, port)
        if args.command == "telegram":
            return asyncio.run(_telegram())
        parser.print_help()
        return EXIT_OK
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


async def _run(agent: str, task: str, verbose: bool) -> int:
    try:
        app = build_app(verbose=verbose)
    except OpenClawError as exc:
        return _fail(f"error: {exc}", EXIT_ERROR)
    try:
        execution = await app.run_agent(agent, task)
    except MissingToolsError as exc:
        return _fail(f"error: {exc}", EXIT_ERROR, _hints(app, exc.missing))
    except OpenClawError as exc:
        return _fail(f"error: {exc}", EXIT_ERROR)
    finally:
        await app.aclose()
    return _report(execution)


async def _run_team(team: str, task: str, verbose: bool) -> int:
    try:
        app = build_app(verbose=verbose)
    except OpenClawError as exc:
        return _fail(f"error: {exc}", EXIT_ERROR)
    try:
        assert app.run_team is not None
        execution = await app.run_team(team, task)
    except MissingToolsError as exc:
        return _fail(f"error: {exc}", EXIT_ERROR, _hints(app, exc.missing))
    except OpenClawError as exc:
        return _fail(f"error: {exc}", EXIT_ERROR)
    finally:
        await app.aclose()
    return _report(execution)


async def _telegram() -> int:
    try:
        bot = build_telegram_bot()
    except OpenClawError as exc:
        return _fail(f"error: {exc}", EXIT_ERROR)
    print("OpenClaw Telegram bot running (Ctrl+C to stop)", file=sys.stderr)
    try:
        await bot.run()
    except OpenClawError as exc:
        return _fail(f"error: {exc}", EXIT_ERROR)
    finally:
        await bot.aclose()
    return EXIT_OK


async def _list_teams() -> int:
    try:
        teams = load_teams()
        ids = await teams.list_ids()
    except OpenClawError as exc:
        return _fail(f"error: {exc}", EXIT_ERROR)
    if not ids:
        print("no team defined (teams/<id>/team.yaml)")
        return EXIT_OK
    for team_id in ids:
        try:
            team = await teams.get(team_id)
        except OpenClawError as exc:  # one broken file must not hide the others
            print(f"{team_id}  invalid: {exc}")
            continue
        lead = f"  supervisor={team.supervisor}" if team.supervisor else ""
        print(f"{team.id}  {team.pattern}{lead}  members: {', '.join(team.members) or 'none'}")
    return EXIT_OK


def _report(execution: _Outcome) -> int:
    if execution.answer:
        print(execution.answer)
    if str(execution.status) == "completed":
        return EXIT_OK
    label = "incomplete" if execution.answer else "error"  # a partial answer was still given
    return _fail(f"{label}: {execution.error} ({execution.status})", EXIT_FAILED)


async def _runs(limit: int, delegations: bool) -> int:
    try:
        summaries = await load_history().recent(limit, delegations=delegations)
    except OpenClawError as exc:
        return _fail(f"error: {exc}", EXIT_ERROR)
    if not summaries:
        print("no execution recorded (executions/)")
        return EXIT_OK
    for s in summaries:
        kind = "(delegation) " if s.parent_execution_id else ""
        print(
            f"{s.execution_id[:12]}  {_when(s.started_at)}  {s.agent_id:<16} "
            f"{s.status:<18} {_steps(s.steps):>9}  {_duration(s.duration_ms):>7}  "
            f"{kind}{_clip(s.request or '', 60)}".rstrip()
        )
    return EXIT_OK


async def _show(ref: str, with_events: bool) -> int:
    try:
        tree = await load_history().tree(ref)
    except OpenClawError as exc:
        return _fail(f"error: {exc}", EXIT_ERROR)
    _print_node(tree, 0, with_events)
    return EXIT_OK


def _print_node(node: Any, depth: int, with_events: bool) -> None:
    """One execution, then the ones it delegated, each indented one level deeper."""
    record, pad = node.record, "    " * depth
    s = record.summary
    arrow = f"{s.sender} -> " if s.sender else ""
    print(f"{pad}{arrow}{s.agent_id}: {s.status}")
    print(
        f"{pad}  execution {s.execution_id[:12]}  task {s.task_id[:12]}  {_when(s.started_at)}  "
        f"{_steps(s.steps)}  {_duration(s.duration_ms)}"
    )
    if s.parent_execution_id and depth == 0:
        print(f"{pad}  part of execution {s.parent_execution_id[:12]}")
    if s.error:
        print(f"{pad}  error: {s.error}")
    _print_block(pad, "request", record.request)
    _print_block(pad, "answer", record.answer)
    if with_events:
        print(f"{pad}  events:")
        for event in record.events:
            print(f"{pad}    {_event_line(event)}")
    for child in node.children:
        print()
        _print_node(child, depth + 1, with_events)


def _print_block(pad: str, label: str, text: str | None) -> None:
    print(f"{pad}  {label}:")
    for line in (text or "(none)").splitlines() or [""]:
        print(f"{pad}    {line}")


_EVENT_KEYS = ("decision", "tool", "skill", "status", "approval", "reason", "error", "input")


def _event_line(event: Any) -> str:
    details = " ".join(
        f"{k}={_clip(event.data[k])}" for k in _EVENT_KEYS if event.data.get(k) not in (None, "")
    )
    return f"{event.timestamp:%H:%M:%S} {event.type.value} {details}".rstrip()


def _clip(value: object, limit: int = 120) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit] + "..."


def _when(moment: Any) -> str:
    return f"{moment:%Y-%m-%d %H:%M:%S}Z"  # UTC, like the trace names and events.jsonl


def _steps(steps: int | None) -> str:
    return "-" if steps is None else f"{steps} step" + ("" if steps == 1 else "s")


def _duration(ms: int | None) -> str:
    if ms is None:
        return "-"
    return f"{ms} ms" if ms < 1000 else f"{ms / 1000:.1f} s"


def _positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError("must be an integer >= 1")
    return number


def _hints(app: App, missing: Sequence[str]) -> dict[str, str]:
    """Why the missing tools are missing: the skipped providers that would have served them."""
    return {
        name: reason
        for name, reason in app.skipped.items()
        if any(tool == name or tool.startswith(f"{name}.") for tool in missing)
    }


def _fail(message: str, code: int, hints: dict[str, str] | None = None) -> int:
    print(message, file=sys.stderr)
    for name, reason in (hints or {}).items():
        print(f"  {name}: {reason}", file=sys.stderr)
    return code


def _start_web_server(host: str, port: int) -> int:
    from openclaw.entrypoints.web import run_server

    server = run_server(host=host, port=port)
    print(f"OpenClaw web server running on http://{host}:{port}")
    server.serve_forever()
    return EXIT_OK


def _web_host() -> str:
    return os.environ.get("OPENCLAW_WEB_HOST", "127.0.0.1").strip() or "127.0.0.1"


def _web_port() -> int:
    raw = os.environ.get("OPENCLAW_WEB_PORT", "8000").strip()
    try:
        port = int(raw)
    except ValueError:
        port = 8000
    return port


def _configure_logging() -> None:
    name = os.environ.get("OPENCLAW_LOG_LEVEL", "INFO").strip().upper()
    level = logging.getLevelNamesMapping().get(name, logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request otherwise


if __name__ == "__main__":
    raise SystemExit(main())
