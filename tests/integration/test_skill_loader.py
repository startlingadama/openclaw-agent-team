import asyncio
from pathlib import Path

import pytest

from openclaw.application.agents.runtime import AgentRuntime
from openclaw.domain.agents.decision import Finish, UseSkill
from openclaw.domain.messages.model import AgentMessage
from openclaw.domain.shared.errors import SkillError
from openclaw.infrastructure.skills.loader import REQUIRED_SECTIONS, SkillLoader
from tests.unit.fakes import FakeApprovals, FakeMemory, FakeTools, ScriptedLLM, make_agent

PROJECT_SKILLS = Path(__file__).resolve().parents[2] / "skills"

VALID = """---
name: {name}
description: Does {name} a thing.
---

# Title

## Purpose

p

## When to use

w

## Procedure

1. step

## Constraints

c

## Expected Output

e
"""


def run(coro):
    return asyncio.run(coro)


def make_skill(root, skill_id, text=None, **files):
    directory = root / skill_id
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(text or VALID.format(name=skill_id.split("/")[-1]))
    for rel, content in files.items():
        f = directory / rel.replace("__", "/")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    return directory


@pytest.fixture
def loader(tmp_path):
    return SkillLoader(tmp_path), tmp_path


def test_metadata_is_name_and_description_only(loader):
    ld, root = loader
    make_skill(root, "github/repo-analysis")
    [meta] = run(ld.list_metadata(["github/repo-analysis"]))
    assert (meta.id, meta.name) == ("github/repo-analysis", "repo-analysis")
    assert meta.description == "Does repo-analysis a thing."
    assert not hasattr(meta, "instructions")


def test_full_skill_loaded_on_demand_with_optional_directories(loader):
    ld, root = loader
    make_skill(
        root,
        "github/repo-analysis",
        scripts__run_py="x",
        references__guide_md="y",
        assets__logo_txt="z",
    )
    (root / "github/repo-analysis/scripts/.gitkeep").write_text("")
    skill = run(ld.load("github/repo-analysis"))
    assert "## Procedure" in skill.instructions and "name:" not in skill.instructions
    assert skill.scripts == ("scripts/run_py",)
    assert skill.references == ("references/guide_md",) and skill.assets == ("assets/logo_txt",)


def test_flat_layout_is_supported(loader):
    ld, root = loader
    make_skill(root, "new-skill")  # REQUIREMENTS section 24: skills/new-skill/SKILL.md
    assert run(ld.load("new-skill")).metadata.id == "new-skill"


def test_crlf_files_are_supported(loader):
    ld, root = loader
    make_skill(root, "crlf", text=VALID.format(name="crlf").replace("\n", "\r\n"))
    assert run(ld.load("crlf")).metadata.name == "crlf"


@pytest.mark.parametrize("bad", ["", "../x", "a//b", "a/../b", "/abs", "a b", ".hidden"])
def test_invalid_ids_are_refused(loader, bad):
    with pytest.raises(SkillError):
        run(loader[0].load(bad))


def test_unknown_skill(loader):
    with pytest.raises(SkillError):
        run(loader[0].list_metadata(["nope/nothing"]))


@pytest.mark.parametrize(
    "text",
    [
        "# no frontmatter\n",
        "---\nname: a\n",
        "---\nname: a\n---\n",
        "---\ndescription: d\n---\n",
        "---\nname: ''\ndescription: d\n---\n",
        "---\nname: [unclosed\n---\n",
        "---\n- a list\n---\n",
    ],
)
def test_invalid_frontmatter_is_an_explicit_error(loader, text):
    ld, root = loader
    make_skill(root, "bad", text=text)
    with pytest.raises(SkillError):
        run(ld.list_metadata(["bad"]))


def test_missing_sections_fail_at_load_time_only(loader):
    ld, root = loader
    make_skill(root, "thin", text="---\nname: thin\ndescription: d\n---\n\n## Purpose\n\np\n")
    assert run(ld.list_metadata(["thin"]))[0].name == "thin"  # listing still works
    with pytest.raises(SkillError, match="When to use.*Procedure.*Constraints.*Expected Output"):
        run(ld.load("thin"))


def test_headings_in_code_fences_do_not_count(loader):
    ld, root = loader
    text = "---\nname: f\ndescription: d\n---\n\n```\n## Purpose\n```\n"
    make_skill(root, "f", text=text)
    with pytest.raises(SkillError):
        run(ld.load("f"))


def test_every_project_skill_respects_the_contract():
    loader = SkillLoader(PROJECT_SKILLS)
    ids = sorted(
        p.parent.relative_to(PROJECT_SKILLS).as_posix() for p in PROJECT_SKILLS.rglob("SKILL.md")
    )
    assert len(ids) >= 13
    for skill_id in ids:
        skill = run(loader.load(skill_id))
        assert all(s.lower() in skill.instructions.lower() for s in REQUIRED_SECTIONS)


def test_runtime_discovers_then_loads_a_real_skill():
    llm = ScriptedLLM([UseSkill("github/repository-analysis"), Finish("done")])
    runtime = AgentRuntime(
        llm=llm,
        tools=FakeTools(),
        skills=SkillLoader(PROJECT_SKILLS),
        memory=FakeMemory(),
        approvals=FakeApprovals(),
    )
    agent = make_agent(skills=["github/repository-analysis"])
    run(runtime.run(agent, AgentMessage("user", agent.id, "t1", "analyze")))
    first, second = llm.contexts
    assert [s.name for s in first.available_skills] == ["repository-analysis"]
    assert first.loaded_skills == ()
    assert "Inspect repository structure." in second.loaded_skills[0].instructions


def test_registry_discovers_every_skill_sorted():
    found = run(SkillLoader(PROJECT_SKILLS).discover())
    ids = [m.id for m in found]
    assert ids == sorted(ids) and len(ids) >= 13
    assert "github/repository-analysis" in ids and all(m.description for m in found)


def test_discovery_of_flat_and_grouped_layouts_ignoring_hidden_directories(loader):
    ld, root = loader
    make_skill(root, "new-skill")
    make_skill(root, "github/code-search")
    make_skill(root, ".cache/ignored")
    assert [m.id for m in run(ld.discover())] == ["github/code-search", "new-skill"]


def test_discovery_reports_a_broken_skill_explicitly(loader):
    ld, root = loader
    make_skill(root, "good")
    make_skill(root, "broken", text="no frontmatter")
    with pytest.raises(SkillError, match="broken"):
        run(ld.discover())


def test_discovery_of_a_missing_root_is_empty(tmp_path):
    assert run(SkillLoader(tmp_path / "nothing").discover()) == []
