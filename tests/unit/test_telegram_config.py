"""TelegramConfig: what the environment must give, and what it must not leak."""

import pytest

from openclaw.domain.shared.errors import AuthenticationError, ValidationError
from openclaw.infrastructure.channels.telegram import TelegramConfig

ENV = {"TELEGRAM_BOT_TOKEN": "123:secret", "TELEGRAM_ALLOWED_USER_IDS": "42"}


def test_defaults():
    config = TelegramConfig.from_env(ENV, default_team="research")
    assert config.allowed_user_ids == frozenset({42})
    assert config.team == "research"
    assert config.approval_timeout == 300.0
    assert config.api_url == "https://api.telegram.org"


def test_overrides_and_several_user_ids():
    config = TelegramConfig.from_env(
        {
            **ENV,
            "TELEGRAM_ALLOWED_USER_IDS": "1, 2;3 4",
            "TELEGRAM_TEAM": "executive",
            "TELEGRAM_APPROVAL_TIMEOUT": "12.5",
            "TELEGRAM_API_URL": "http://localhost:8081/",
        }
    )
    assert config.allowed_user_ids == frozenset({1, 2, 3, 4})
    assert (config.team, config.approval_timeout) == ("executive", 12.5)
    assert config.api_url == "http://localhost:8081/"


def test_a_missing_token_is_an_authentication_error():
    with pytest.raises(AuthenticationError, match="TELEGRAM_BOT_TOKEN"):
        TelegramConfig.from_env({"TELEGRAM_ALLOWED_USER_IDS": "42"})


@pytest.mark.parametrize("ids", ["", "   ", ",", "abc", "0", "-5", "12abc"])
def test_the_list_of_authorized_users_is_mandatory_and_numeric(ids):
    with pytest.raises(ValidationError, match="TELEGRAM_ALLOWED_USER_IDS"):
        TelegramConfig.from_env({**ENV, "TELEGRAM_ALLOWED_USER_IDS": ids})


@pytest.mark.parametrize("value", ["0", "-1", "soon"])
def test_the_approval_timeout_must_be_positive(value):
    with pytest.raises(ValidationError, match="TELEGRAM_APPROVAL_TIMEOUT"):
        TelegramConfig.from_env({**ENV, "TELEGRAM_APPROVAL_TIMEOUT": value})


def test_the_token_is_not_in_the_repr():
    assert "secret" not in repr(TelegramConfig.from_env(ENV))
