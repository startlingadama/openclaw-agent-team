import asyncio
from pathlib import Path

import pytest

from openclaw.domain.shared.errors import TeamError
from openclaw.domain.teams.team import TeamPattern
from openclaw.infrastructure.teams import YamlTeamRepository

PROJECT_TEAMS = Path(__file__).resolve().parents[2] / "teams"


def make(root: Path, team_id="alpha", body=None) -> None:
    directory = root / team_id
    directory.mkdir(parents=True)
    (directory / "team.yaml").write_text(
        body
        if body is not None
        else f"team:\n  id: {team_id}\n  pattern: supervisor\n  supervisor: boss\n"
        "  members: [a, b]\n",
        encoding="utf-8",
    )


def get(root, team_id):
    return asyncio.run(YamlTeamRepository(root).get(team_id))


def test_builds_a_team_from_yaml(tmp_path):
    make(tmp_path)
    team = get(tmp_path, "alpha")
    assert (team.id, team.pattern, team.supervisor) == ("alpha", TeamPattern.SUPERVISOR, "boss")
    assert team.members == ("a", "b")


def test_project_teams_all_load():
    repo = YamlTeamRepository(PROJECT_TEAMS)
    ids = asyncio.run(repo.list_ids())
    assert {"default", "executive", "growth", "research"} <= set(ids)
    default = get(PROJECT_TEAMS, "default")
    assert default.supervisor == "ceo"
    assert default.members == (
            "github",
            "linkedin",
            "google-email",
            "google-research",
            "code-executor",
            "writer",
        )
    assert get(PROJECT_TEAMS, "executive").members == ()


def test_unknown_team_lists_the_available_ones(tmp_path):
    make(tmp_path)
    with pytest.raises(TeamError, match=r"unknown team 'nope'.*alpha"):
        get(tmp_path, "nope")


def test_missing_teams_directory_is_an_unknown_team(tmp_path):
    with pytest.raises(TeamError, match="unknown team 'default' .*none"):
        get(tmp_path / "absent", "default")


@pytest.mark.parametrize("bad", ["../etc", "a/b", "", ".hidden"])
def test_team_id_cannot_traverse_paths(tmp_path, bad):
    with pytest.raises(TeamError, match="invalid team id"):
        get(tmp_path, bad)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("team:\n  id: other\n  supervisor: boss\n", "must match the directory"),
        ("team:\n  id: alpha\n  pattern: chaos\n  supervisor: boss\n", "unknown pattern"),
        ("team:\n  id: alpha\n  pattern: supervisor\n", "supervisor pattern but has none"),
        (
            "team:\n  id: alpha\n  supervisor: boss\n  members: [a, boss]\n",
            "cannot be a member",
        ),
        ("team:\n  id: alpha\n  supervisor: boss\n  members: [a, a]\n", "listed twice"),
        ("team:\n  id: alpha\n  supervisor: boss\n  members: a\n", "list of agent ids"),
        ("team:\n  id: alpha\n  supervisor: boss\n  members: [../x]\n", "invalid agent id"),
        ("team: [1, 2]\n", "expected a mapping"),
        ("team: {id: alpha\n", "cannot be read"),
    ],
)
def test_invalid_definitions_are_refused(tmp_path, body, message):
    make(tmp_path, body=body)
    with pytest.raises(TeamError, match=message):
        get(tmp_path, "alpha")


def test_peer_to_peer_needs_no_supervisor(tmp_path):
    make(tmp_path, body="team:\n  id: alpha\n  pattern: peer_to_peer\n  members: [a, b]\n")
    team = get(tmp_path, "alpha")
    assert team.pattern is TeamPattern.PEER_TO_PEER
    assert team.supervisor is None
