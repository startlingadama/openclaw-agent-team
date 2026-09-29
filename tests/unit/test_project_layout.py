"""Validates the Markdown-first content: agent profiles, skills, teams."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILE_FILES = {"SOUL.md", "USER.md", "MEMORY.md", "AGENTS.md", "HEARTBEAT.md"}


def test_every_agent_has_the_five_profile_files():
    agents = [d for d in (ROOT / "agents").iterdir() if d.is_dir()]
    assert {d.name for d in agents} >= {"github", "linkedin", "google-email", "google-research"}
    for d in agents:
        assert PROFILE_FILES <= {f.name for f in d.iterdir()}, d.name


def test_every_skill_has_valid_frontmatter():
    skills = list((ROOT / "skills").rglob("SKILL.md"))
    assert skills
    for skill in skills:
        lines = skill.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "---", skill
        end = lines.index("---", 1)
        meta = dict(line.split(": ", 1) for line in lines[1:end])
        assert meta["name"] == skill.parent.name, skill
        assert meta["description"], skill


def test_every_team_has_a_config():
    for d in (ROOT / "teams").iterdir():
        assert (d / "team.yaml").is_file(), d.name


def test_agent_skill_references_exist():
    for cfg in (ROOT / "agents").glob("*/agent.yaml"):
        for line in cfg.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("- ") and "/" in line and "." not in line:
                assert (ROOT / "skills" / line[2:] / "SKILL.md").is_file(), (cfg, line)
