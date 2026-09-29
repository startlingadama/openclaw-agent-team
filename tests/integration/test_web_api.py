import asyncio
import json
import shutil
from pathlib import Path
from urllib import request

import pytest

from openclaw.domain.agents.decision import Finish
from openclaw.entrypoints.web import OpenClawHTTPServer, build_http_api
from tests.unit.fakes import ScriptedLLM

PROJECT = Path(__file__).resolve().parents[2]


@pytest.fixture
def home(tmp_path):
    shutil.copytree(PROJECT / "agents", tmp_path / "agents")
    shutil.copytree(PROJECT / "skills", tmp_path / "skills")
    shutil.copytree(PROJECT / "teams", tmp_path / "teams")
    (tmp_path / "workspace" / "shared").mkdir(parents=True)
    return tmp_path


def env_for(home, **extra):
    return {"OPENCLAW_HOME": str(home), "DEEPSEEK_API_KEY": "sk-test", **extra}


@pytest.mark.asyncio
async def test_backend_contract_matches_frontend(home):
    llm = ScriptedLLM([Finish("done")])
    api = build_http_api(env_for(home, TAVILY_API_KEY="t"), llm=llm)

    agents = await api.list_agents()
    assert any(a["id"] == "github" for a in agents)

    skills = await api.list_skills()
    assert skills

    teams = await api.list_teams()
    assert any(t["id"] == "default" for t in teams)

    task = await api.create_task({"agent_id": "github", "title": "Analyze repo"})
    assert task["agentId"] == "github"
    assert task["title"] == "Analyze repo"
    assert await api.get_task(task["id"]) is not None

    memory = await api.get_memory("github")
    assert isinstance(memory, list)

    approvals = await api.list_approvals()
    assert isinstance(approvals, list)

    await api.aclose()


@pytest.mark.asyncio
async def test_a_runtime_error_does_not_leave_task_stuck_in_running(home, monkeypatch):
    llm = ScriptedLLM([Finish("done")])
    api = build_http_api(env_for(home, TAVILY_API_KEY="t"), llm=llm)

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    object.__setattr__(api.app, "run_agent", boom)

    await api.create_task({"agent_id": "github", "title": "Analyze repo"})
    await asyncio.sleep(0.1)
    tasks = await api.list_tasks()
    assert tasks[0]["status"] == "failed"

    await api.aclose()


@pytest.mark.asyncio
async def test_auth_login_and_cors_headers(home):
    llm = ScriptedLLM([Finish("done")])
    server = OpenClawHTTPServer("127.0.0.1", 0, env_for(home, TAVILY_API_KEY="t"), llm=llm)
    server.start()
    port = server._server.server_address[1]
    try:
        login_req = request.Request(
            f"http://127.0.0.1:{port}/api/auth/login",
            data=json.dumps({"email": "demo@example.com", "password": "secret"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(login_req, timeout=5) as resp:
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"
            login = json.loads(resp.read().decode("utf-8"))
            assert login["user"]["email"] == "demo@example.com"
            token = login["access_token"]

        me_req = request.Request(
            f"http://127.0.0.1:{port}/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with request.urlopen(me_req, timeout=5) as resp:
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"
            me = json.loads(resp.read().decode("utf-8"))
            assert me["email"] == "demo@example.com"

        preflight = request.Request(
            f"http://127.0.0.1:{port}/api/auth/login",
            headers={"Origin": "http://localhost:8080", "Access-Control-Request-Method": "POST"},
            method="OPTIONS",
        )
        with request.urlopen(preflight, timeout=5) as resp:
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"
            assert resp.headers.get("Access-Control-Allow-Methods")
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_skill_loading_and_memory_updates_are_effective(home):
    llm = ScriptedLLM([Finish("done")])
    server = OpenClawHTTPServer("127.0.0.1", 0, env_for(home, GITHUB_TOKEN="ghp_test"), llm=llm)
    server.start()
    port = server._server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        with request.urlopen(f"{base}/api/skills", timeout=5) as response:
            skills = json.loads(response.read().decode("utf-8"))
        skill = next(item for item in skills if item["id"] == "github/repository-analysis")
        assert skill["agents"] == ["github"]
        assert any(item["path"] == "SKILL.md" for item in skill["files"])

        with request.urlopen(
            f"{base}/api/skills/github/repository-analysis/instructions", timeout=5
        ) as response:
            instructions = json.loads(response.read().decode("utf-8"))["instructions"]
        assert "Repository Analysis" in instructions

        updated_content = "# Agent memory\n\n## Test section\nPersist this change.\n"
        update = request.Request(
            f"{base}/api/memory/github/agent",
            data=json.dumps({"content": updated_content}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with request.urlopen(update, timeout=5) as response:
            memories = json.loads(response.read().decode("utf-8"))
        agent_memory = next(item for item in memories if item["name"] == "MEMORY.md")
        assert agent_memory["content"] == updated_content
        assert "github.search_code" in {tool["name"] for tool in await server.api.list_tools()}
        assert "github" in next(
            tool["agents"]
            for tool in await server.api.list_tools()
            if tool["name"] == "github.search_code"
        )
    finally:
        server.shutdown()
        await server.api.aclose()


@pytest.mark.asyncio
async def test_webchat_shows_the_role_of_each_agent_and_member(home):
    api = build_http_api(env_for(home, TAVILY_API_KEY="t"), llm=ScriptedLLM([]))
    roles = {a["id"]: a["role"] for a in await api.list_agents()}
    assert roles["ceo"] == "Supervisor"
    assert roles["code-executor"] == "Code execution specialist"
    assert roles["github"] == "GitHub specialist"

    default = next(t for t in await api.list_teams() if t["id"] == "default")
    responsibilities = {m["agentId"]: m["responsibility"] for m in default["members"]}
    assert responsibilities["google-email"] == "Email specialist"
    assert responsibilities["code-executor"] == "Code execution specialist"
    await api.aclose()


@pytest.mark.asyncio
async def test_webchat_falls_back_when_no_role_is_declared(home):
    (home / "agents" / "github" / "agent.yaml").write_text(
        "agent:\n  id: github\n  profile: agents/github\n", encoding="utf-8"
    )
    api = build_http_api(env_for(home, TAVILY_API_KEY="t"), llm=ScriptedLLM([]))
    agent = next(a for a in await api.list_agents() if a["id"] == "github")
    assert agent["role"] == "AI specialist"
    await api.aclose()


@pytest.mark.asyncio
async def test_a_team_member_without_an_agent_file_does_not_break_the_team_listing(home):
    (home / "teams" / "default" / "team.yaml").write_text(
        "team:\n  id: default\n  pattern: supervisor\n  supervisor: ceo\n"
        "  members:\n    - github\n    - ghost\n",
        encoding="utf-8",
    )
    api = build_http_api(env_for(home, TAVILY_API_KEY="t"), llm=ScriptedLLM([]))
    default = next(t for t in await api.list_teams() if t["id"] == "default")
    assert {m["agentId"]: m["responsibility"] for m in default["members"]} == {
        "github": "GitHub specialist",
        "ghost": "AI specialist",
    }
    await api.aclose()


# -- download of the documents of the writer agent (ADR-026) -----------------------------------
def _login(base):
    login = request.Request(
        f"{base}/api/auth/login",
        data=json.dumps({"email": "demo@example.com", "password": "secret"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(login, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))["access_token"]


def _get(url, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with request.urlopen(request.Request(url, headers=headers), timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except request.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


@pytest.mark.asyncio
async def test_documents_are_downloaded_with_authentication_and_only_from_their_directory(home):
    docs = home / "docs"
    (docs / "report").mkdir(parents=True)
    (docs / "report" / "report.pdf").write_bytes(b"%PDF-1.5 content")
    (docs / "report" / "report.tex").write_text("\\documentclass{article}", encoding="utf-8")
    (docs / "report" / "notes.md").write_text("# Notes", encoding="utf-8")
    (docs / "report" / "data.csv").write_text("a,b", encoding="utf-8")
    (home / "outside.pdf").write_bytes(b"%PDF outside")
    (docs / "link.pdf").symlink_to(home / "outside.pdf")
    env = env_for(home, OPENCLAW_DOCS_DIR=str(docs))
    server = OpenClawHTTPServer("127.0.0.1", 0, env, llm=ScriptedLLM([]))
    server.start()
    base = f"http://127.0.0.1:{server._server.server_address[1]}/api/documents"
    try:
        assert _get(f"{base}/report/report.pdf")[0] == 401  # no token
        assert _get(f"{base}/report/report.pdf", "not-a-session")[0] == 401
        token = _login(base.removesuffix("/api/documents"))

        status, headers, body = _get(f"{base}/report/report.pdf", token)
        assert (status, body) == (200, b"%PDF-1.5 content")
        assert headers["Content-Type"] == "application/pdf"
        assert "attachment" in headers["Content-Disposition"]
        status, headers, body = _get(f"{base}/report/report.tex", token)
        assert status == 200 and headers["Content-Type"].startswith("text/x-tex")
        status, headers, body = _get(f"{base}/report/notes.md", token)
        assert (status, body) == (200, b"# Notes")
        assert headers["Content-Type"] == "text/markdown; charset=utf-8"

        for refused in (
            "report/missing.pdf",  # not there
            "report/data.csv",  # not a Markdown, LaTeX or PDF document
            "%2e%2e/outside.pdf",  # ..
            "report/%2e%2e/%2e%2e/outside.pdf",
            "%2Fetc%2Fpasswd.pdf",  # absolute
            "link.pdf",  # symbolic link leaving the directory
            "report",  # a directory
        ):
            assert _get(f"{base}/{refused}", token)[0] == 404, refused
        post = request.Request(
            f"{base}/report/report.pdf",
            data=b"{}",
            method="POST",
            headers={"Authorization": f"Bearer {token}"},
        )
        with pytest.raises(request.HTTPError) as exc:
            request.urlopen(post, timeout=5)
        assert exc.value.code == 405
    finally:
        server.shutdown()
        await server.api.aclose()


# -- history of the documents and documents of a task (ADR-029) --------------------------------
def _emit_document_call(api, task_id, tool, path, *, status="ok"):
    api.tasks.emit(
        task_id,
        {
            "time": "10:00",
            "type": "tool.called",
            "label": tool,
            "status": "running",
            "input": {"path": path},
        },
    )
    api.tasks.emit(
        task_id,
        {"time": "10:00", "type": "tool.completed", "label": tool, "status": status},
    )


@pytest.mark.asyncio
async def test_documents_are_listed_with_authentication_and_the_task_that_produced_them(home):
    docs = home / "docs"
    (docs / "report").mkdir(parents=True)
    (docs / "report" / "report.tex").write_text("\\documentclass{article}", encoding="utf-8")
    (docs / "report" / "report.pdf").write_bytes(b"%PDF-1.5 content")
    (docs / "report" / "report.aux").write_text("auxiliary", encoding="utf-8")
    (docs / "notes.md").write_text("# Notes", encoding="utf-8")
    (docs / ".hidden.md").write_text("hidden", encoding="utf-8")
    (home / "outside.md").write_text("outside", encoding="utf-8")
    (docs / "link.md").symlink_to(home / "outside.md")
    env = env_for(home, OPENCLAW_DOCS_DIR=str(docs))
    server = OpenClawHTTPServer("127.0.0.1", 0, env, llm=ScriptedLLM([]))
    server.start()
    root = f"http://127.0.0.1:{server._server.server_address[1]}"
    try:
        task = server.api.tasks.create(agent_id="writer", title="Write the report")
        _emit_document_call(server.api, task.task_id, "docs.write", "report/report.tex")
        _emit_document_call(server.api, task.task_id, "docs.compile_pdf", "report/report.tex")
        _emit_document_call(
            server.api, task.task_id, "docs.write", "missing/never.md", status="error"
        )

        assert _get(f"{root}/api/documents")[0] == 401
        assert _get(f"{root}/api/documents", "not-a-session")[0] == 401
        token = _login(root)
        status, headers, body = _get(f"{root}/api/documents", token)
        assert status == 200 and headers["Content-Type"] == "application/json"
        listed = json.loads(body)

        by_path = {item["path"]: item for item in listed}
        assert set(by_path) == {"notes.md", "report/report.tex", "report/report.pdf"}
        assert by_path["report/report.pdf"]["kind"] == "pdf"
        assert by_path["report/report.pdf"]["size"] == len(b"%PDF-1.5 content")
        assert by_path["report/report.pdf"]["folder"] == "report"
        assert by_path["notes.md"]["folder"] == ""
        assert by_path["report/report.tex"]["taskId"] == task.task_id
        assert by_path["report/report.pdf"]["agentId"] == "writer"
        assert by_path["notes.md"]["taskId"] is None  # no task known for it
        modified = [item["modified"] for item in listed]
        assert modified == sorted(modified, reverse=True)

        # the documents of the task: only what was written without error and still exists
        status, _, body = _get(f"{root}/api/tasks/{task.task_id}", token)
        assert status == 200
        produced = json.loads(body)["documents"]
        assert [d["path"] for d in produced] == ["report/report.tex", "report/report.pdf"]

        post = request.Request(
            f"{root}/api/documents",
            data=b"{}",
            method="POST",
            headers={"Authorization": f"Bearer {token}"},
        )
        with pytest.raises(request.HTTPError) as exc:
            request.urlopen(post, timeout=5)
        assert exc.value.code == 405
    finally:
        server.shutdown()
        await server.api.aclose()
