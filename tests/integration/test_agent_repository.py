import asyncio
from pathlib import Path

import pytest

from openclaw.domain.shared.errors import AgentError
from openclaw.infrastructure.agents import YamlAgentRepository

PROJECT_AGENTS = Path(__file__).resolve().parents[2] / "agents"


def make(root: Path, agent_id="alpha", yaml_text=None, soul="# Soul\n") -> Path:
    directory = root / agent_id
    directory.mkdir(parents=True)
    (directory / "agent.yaml").write_text(
        yaml_text
        or f"agent:\n  id: {agent_id}\nskills:\n  - a/b\ntools:\n  allowed: [x.one]\n"
        "  approval_required: [x.two]\n",
        encoding="utf-8",
    )
    if soul is not None:
        (directory / "SOUL.md").write_text(soul, encoding="utf-8")
    return directory


def get(root, agent_id):
    return asyncio.run(YamlAgentRepository(root).get(agent_id))


def test_builds_an_agent_from_yaml_and_markdown(tmp_path):
    directory = make(tmp_path)
    (directory / "AGENTS.md").write_text("# Instructions\n", encoding="utf-8")
    agent = get(tmp_path, "alpha")
    assert agent.id == "alpha"
    assert agent.skills == ("a/b",)
    assert agent.tool_permissions.allowed == {"x.one"}
    assert agent.tool_permissions.approval_required == {"x.two"}
    assert agent.profile.soul == "# Soul\n"
    assert agent.profile.instructions == "# Instructions\n"
    assert agent.profile.heartbeat == ""  # optional file


def test_project_agents_all_load():
    repo = YamlAgentRepository(PROJECT_AGENTS)
    ids = asyncio.run(repo.list_ids())
    assert {"ceo", "github", "google-email", "google-research", "linkedin"} <= set(ids)
    research = get(PROJECT_AGENTS, "google-research")
    assert research.skills == ("research/web-research", "research/source-evaluation")
    assert research.tool_permissions.allowed == {
        "memory.update",
        "web.search",
        "web.open",
        "web.extract",
    }


def test_unknown_agent_lists_the_available_ones(tmp_path):
    make(tmp_path)
    with pytest.raises(AgentError, match=r"unknown agent 'nope'.*alpha"):
        get(tmp_path, "nope")


@pytest.mark.parametrize("bad", ["../etc", "a/b", "", ".hidden"])
def test_invalid_ids_are_refused(tmp_path, bad):
    with pytest.raises(AgentError, match="invalid agent id"):
        get(tmp_path, bad)


def test_directory_name_and_declared_id_must_agree(tmp_path):
    make(tmp_path, "alpha", yaml_text="agent:\n  id: beta\n")
    with pytest.raises(AgentError, match="must match the directory name"):
        get(tmp_path, "alpha")


def test_soul_is_required(tmp_path):
    make(tmp_path, soul=None)
    with pytest.raises(AgentError, match="SOUL.md"):
        get(tmp_path, "alpha")


@pytest.mark.parametrize(
    "yaml_text",
    [
        "agent:\n  id: alpha\ntools:\n  allowed: web.search\n",
        "agent:\n  id: alpha\nskills: [1, 2]\n",
        "agent: [not, a, mapping]\n",
        "agent: {id: alpha\n",  # invalid YAML
    ],
)
def test_malformed_definitions_are_explicit_errors(tmp_path, yaml_text):
    make(tmp_path, yaml_text=yaml_text)
    with pytest.raises(AgentError):
        get(tmp_path, "alpha")


def test_no_agents_directory_means_no_agents(tmp_path):
    assert asyncio.run(YamlAgentRepository(tmp_path / "missing").list_ids()) == []


# -- role: the optional display label (WebChat) ------------------------------------------------
def role_yaml(value: str) -> str:
    return f"agent:\n  id: alpha\n  role: {value}\n"


def test_role_is_optional_and_empty_by_default(tmp_path):
    make(tmp_path)
    assert get(tmp_path, "alpha").role == ""


def test_role_is_read_and_trimmed(tmp_path):
    make(tmp_path, yaml_text=role_yaml("'  Code execution specialist  '"))
    assert get(tmp_path, "alpha").role == "Code execution specialist"


@pytest.mark.parametrize(
    "value", ["''", "'   '", "42", "[a, b]", "'x" + "y" * 40 + "'", '"two\\nlines"']
)
def test_an_invalid_role_is_refused(tmp_path, value):
    make(tmp_path, yaml_text=role_yaml(value))
    with pytest.raises(AgentError, match="agent.role"):
        get(tmp_path, "alpha")


def test_every_project_agent_declares_its_role():
    roles = {
        agent_id: get(PROJECT_AGENTS, agent_id).role
        for agent_id in asyncio.run(YamlAgentRepository(PROJECT_AGENTS).list_ids())
    }
    assert roles == {
        "ceo": "Supervisor",
        "code-executor": "Code execution specialist",
        "github": "GitHub specialist",
        "google-email": "Email specialist",
        "google-research": "Research specialist",
        "linkedin": "LinkedIn specialist",
        "writer": "Documentation specialist",
    }


# -- tools.optional: permitted tools whose absence is tolerated -------------------------------
def optional_yaml(optional: str) -> str:
    return (
        "agent:\n  id: alpha\ntools:\n  allowed: [x.one]\n  approval_required: [x.two]\n"
        f"  optional: {optional}\n"
    )


def test_optional_is_empty_by_default(tmp_path):
    make(tmp_path)
    assert get(tmp_path, "alpha").tool_permissions.optional == frozenset()


def test_optional_is_read_for_allowed_and_approval_required_tools(tmp_path):
    make(tmp_path, yaml_text=optional_yaml("[x.one, x.two]"))
    permissions = get(tmp_path, "alpha").tool_permissions
    assert permissions.optional == {"x.one", "x.two"}
    assert permissions.allowed == {"x.one"}  # optional never adds a permission
    assert permissions.approval_required == {"x.two"}


def test_optional_does_not_change_the_approval_of_a_tool(tmp_path):
    make(tmp_path, yaml_text=optional_yaml("[x.two]"))
    permissions = get(tmp_path, "alpha").tool_permissions
    assert permissions.lists_approval("x.two") and permissions.is_optional("x.two")
    assert not permissions.is_optional("x.one")


def test_an_optional_tool_that_is_not_permitted_is_refused(tmp_path):
    make(tmp_path, yaml_text=optional_yaml("[x.other]"))
    with pytest.raises(AgentError, match=r"tools.optional.*x\.other"):
        get(tmp_path, "alpha")


@pytest.mark.parametrize("value", ["x.one", "[1, 2]", "[' ']"])
def test_a_malformed_optional_is_an_explicit_error(tmp_path, value):
    make(tmp_path, yaml_text=optional_yaml(value))
    with pytest.raises(AgentError, match="tools.optional"):
        get(tmp_path, "alpha")


def test_only_the_compile_tool_of_the_writer_is_optional_in_the_project():
    optional = {
        agent_id: get(PROJECT_AGENTS, agent_id).tool_permissions.optional
        for agent_id in asyncio.run(YamlAgentRepository(PROJECT_AGENTS).list_ids())
    }
    assert {a: o for a, o in optional.items() if o} == {"writer": {"docs.compile_pdf"}}
