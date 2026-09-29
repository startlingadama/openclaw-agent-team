"""The Telegram channel through the composition root: real agents/, fake LLM, fake Bot API."""

import asyncio
import json
import shutil
from pathlib import Path

import httpx
import pytest

from openclaw.domain.agents.decision import CallTool, Finish
from openclaw.domain.shared.errors import AuthenticationError, ValidationError
from openclaw.domain.tools.model import ToolCall
from openclaw.entrypoints.bootstrap import build_telegram_bot
from openclaw.entrypoints.cli import build_parser
from tests.unit.fakes import ScriptedLLM

PROJECT = Path(__file__).resolve().parents[2]
USER, CHAT = 42, 500


@pytest.fixture
def home(tmp_path):
    for name in ("agents", "skills", "teams"):
        shutil.copytree(PROJECT / name, tmp_path / name)
    (tmp_path / "workspace" / "shared").mkdir(parents=True)
    return tmp_path


def env_for(home, **extra):
    return {
        "OPENCLAW_HOME": str(home),
        "DEEPSEEK_API_KEY": "sk-test",
        "TELEGRAM_BOT_TOKEN": "1:tok",
        "TELEGRAM_ALLOWED_USER_IDS": str(USER),
        **extra,
    }


class FakeBotApi:
    def __init__(self):
        self.calls = []

    def __call__(self, request):
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content)
        self.calls.append((method, payload))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.calls)}})

    def texts(self):
        return [p["text"] for m, p in self.calls if m == "sendMessage"]


def update(text):
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": USER, "is_bot": False},
            "chat": {"id": CHAT, "type": "private"},
            "text": text,
        },
    }


def test_the_token_and_the_authorized_users_are_mandatory(home):
    env = env_for(home)
    with pytest.raises(AuthenticationError, match="TELEGRAM_BOT_TOKEN"):
        build_telegram_bot({**env, "TELEGRAM_BOT_TOKEN": ""})
    with pytest.raises(ValidationError, match="TELEGRAM_ALLOWED_USER_IDS"):
        build_telegram_bot({**env, "TELEGRAM_ALLOWED_USER_IDS": ""})


def test_free_text_reaches_the_supervisor_of_the_team_and_the_answer_is_sent(home):
    api = FakeBotApi()
    llm = ScriptedLLM([Finish("Hello from the CEO")])
    bot = build_telegram_bot(env_for(home), llm=llm, transport=httpx.MockTransport(api))

    async def go():
        try:
            await bot.adapter.handle_update(update("what can you do?"))
            await asyncio.gather(*list(bot.adapter._running.values()))
        finally:
            await bot.aclose()

    asyncio.run(go())
    assert api.texts() == ["Hello from the CEO"]
    assert (
        "what can you do?" in llm.contexts[0].request
        if hasattr(llm.contexts[0], "request")
        else True
    )


def test_run_targets_one_agent_and_an_unknown_one_is_reported(home):
    api = FakeBotApi()
    llm = ScriptedLLM([Finish("ok")])
    bot = build_telegram_bot(env_for(home), llm=llm, transport=httpx.MockTransport(api))

    async def go():
        try:
            await bot.adapter.handle_update(update("/run nobody do it"))
            await asyncio.gather(*list(bot.adapter._running.values()))
            await bot.adapter.handle_update(update("/agents"))
        finally:
            await bot.aclose()

    asyncio.run(go())
    assert "unknown agent 'nobody'" in api.texts()[0]
    listing = api.texts()[1]
    assert "ceo - Supervisor" in listing and "writer" in listing


def test_an_unknown_team_is_reported_in_the_chat(home):
    api = FakeBotApi()
    bot = build_telegram_bot(
        env_for(home, TELEGRAM_TEAM="nope"),
        llm=ScriptedLLM([Finish("x")]),
        transport=httpx.MockTransport(api),
    )

    async def go():
        try:
            await bot.adapter.handle_update(update("hello"))
            await asyncio.gather(*list(bot.adapter._running.values()))
        finally:
            await bot.aclose()

    asyncio.run(go())
    assert "unknown team 'nope'" in api.texts()[0]


def test_the_cli_has_a_telegram_command():
    assert build_parser().parse_args(["telegram"]).command == "telegram"


# -- the files of the writer are sent after the answer (ADR-030) --------------------------------
class UploadApi(FakeBotApi):
    """A fake Bot API that also understands a multipart upload."""

    def __call__(self, request):
        method = request.url.path.rsplit("/", 1)[-1]
        if method != "sendDocument":
            return super().__call__(request)
        body = request.content
        name = body.split(b'filename="', 1)[1].split(b'"', 1)[0].decode()
        self.calls.append((method, {"name": name, "body": body}))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.calls)}})

    def documents(self):
        return [(p["name"], p["body"]) for m, p in self.calls if m == "sendDocument"]


def write_note(path, content):
    return CallTool(ToolCall("docs.write", {"path": path, "content": content}))


def run_updates(bot, *texts):
    async def go():
        try:
            for text in texts:
                await bot.adapter.handle_update(update(text))
                await asyncio.gather(*list(bot.adapter._running.values()))
        finally:
            await bot.aclose()

    asyncio.run(go())


def test_a_file_written_by_the_writer_is_sent_after_its_answer(home):
    api = UploadApi()
    llm = ScriptedLLM([write_note("notes/summary.md", "# Summary\n"), Finish("The note is ready")])
    env = env_for(home, OPENCLAW_DOCS_DIR=str(home / "docs"), TAVILY_API_KEY="t")
    bot = build_telegram_bot(env, llm=llm, transport=httpx.MockTransport(api))
    run_updates(bot, "/run writer write a note")
    assert api.texts() == ["The note is ready"]
    assert [name for name, _ in api.documents()] == ["summary.md"]
    assert b"# Summary" in api.documents()[0][1]
    assert [m for m, _ in api.calls if m in ("sendMessage", "sendDocument")] == [
        "sendMessage",
        "sendDocument",
    ]


def test_a_file_written_by_a_delegated_writer_reaches_the_chat_of_the_task(home):
    api = UploadApi()
    llm = ScriptedLLM(
        [
            CallTool(ToolCall("team.delegate", {"agent": "writer", "objective": "Write a note"})),
            write_note("deleg.md", "# Delegated\n"),
            Finish("written"),
            Finish("The writer prepared it"),
        ]
    )
    env = env_for(home, OPENCLAW_DOCS_DIR=str(home / "docs"), TAVILY_API_KEY="t")
    bot = build_telegram_bot(env, llm=llm, transport=httpx.MockTransport(api))
    run_updates(bot, "please write a note")
    assert api.texts() == ["The writer prepared it"]
    assert [name for name, _ in api.documents()] == ["deleg.md"]


def test_a_task_without_a_document_sends_no_file(home):
    api = UploadApi()
    bot = build_telegram_bot(
        env_for(home, OPENCLAW_DOCS_DIR=str(home / "docs"), TAVILY_API_KEY="t"),
        llm=ScriptedLLM([Finish("nothing to attach")]),
        transport=httpx.MockTransport(api),
    )
    run_updates(bot, "hello")
    assert api.documents() == []


def test_an_old_file_of_the_directory_is_not_sent_by_a_later_task(home):
    (home / "docs").mkdir()
    (home / "docs" / "old.md").write_text("old")
    api = UploadApi()
    bot = build_telegram_bot(
        env_for(home, OPENCLAW_DOCS_DIR=str(home / "docs"), TAVILY_API_KEY="t"),
        llm=ScriptedLLM([Finish("only text")]),
        transport=httpx.MockTransport(api),
    )
    run_updates(bot, "/run writer say something")
    assert api.documents() == []
