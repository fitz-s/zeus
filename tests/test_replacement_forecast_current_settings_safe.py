import json
from pathlib import Path

from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    resolve_replacement_forecast_runtime_policy,
)


def test_current_replacement_settings_are_shadow_veto_only_in_pr399() -> None:
    flags = json.loads((Path(__file__).resolve().parents[1] / "config/settings.json").read_text())["feature_flags"]

    policy = resolve_replacement_forecast_runtime_policy(flags)

    assert flags[VETO_FLAG] is True
    assert flags[TRADE_AUTHORITY_FLAG] is False
    assert flags[KELLY_INCREASE_FLAG] is False
    assert flags[DIRECTION_FLIP_FLAG] is False
    assert policy.status == "SHADOW_VETO_ONLY"
    assert policy.can_initiate_trade is False
    assert policy.can_increase_kelly is False
    assert policy.can_flip_direction is False


def test_trade_authority_fixture_still_fails_closed_in_pr399() -> None:
    flags = json.loads((Path(__file__).resolve().parents[1] / "config/settings.json").read_text())["feature_flags"]
    flags = dict(flags)
    flags[TRADE_AUTHORITY_FLAG] = True

    policy = resolve_replacement_forecast_runtime_policy(flags)

    assert policy.status == "BLOCKED"
    assert "REPLACEMENT_PR399_LIVE_AUTHORITY_DISABLED" in policy.reason_codes
    assert policy.can_initiate_trade is False
