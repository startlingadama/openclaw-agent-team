"""Command-line parsing: the position of `-v` does not change the task."""

import pytest

from openclaw.entrypoints.cli import build_parser, parse_args


def parse(*argv):
    return parse_args(build_parser(), list(argv))


@pytest.mark.parametrize(
    "argv",
    [
        ("run", "code-executor", "-v", "calcule 6*7"),
        ("run", "-v", "code-executor", "calcule 6*7"),
        ("run", "code-executor", "calcule 6*7", "-v"),
    ],
)
def test_the_flag_can_be_anywhere(argv):
    args = parse(*argv)
    assert (args.agent, args.task, args.verbose) == ("code-executor", ["calcule 6*7"], True)


def test_words_after_the_flag_are_part_of_the_task():
    args = parse("run", "github", "-v", "open", "an", "issue")
    assert args.task == ["open", "an", "issue"]


def test_an_unknown_flag_is_still_refused():
    with pytest.raises(SystemExit):
        parse("run", "github", "-v", "--nope", "x")


def test_team_run_and_other_commands_are_unchanged():
    assert parse("team", "run", "-v", "default", "do it").task == ["do it"]
    assert parse("team", "run", "default", "-v", "do it").task == ["do it"]
    assert parse("runs", "-n", "3").limit == 3
