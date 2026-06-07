import json
from pathlib import Path

from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    TRADE_AUTHORITY_FLAG,
    resolve_replacement_forecast_runtime_policy,
)


def test_current_replacement_settings_cannot_grant_live_authority_in_pr399() -> None:
    flags = json.loads((Path(__file__).resolve().parents[1] / "config/settings.json").read_text())["feature_flags"]

    policy = resolve_replacement_forecast_runtime_policy(flags)

    assert flags[TRADE_AUTHORITY_FLAG] is True
    assert flags[KELLY_INCREASE_FLAG] is False
    assert flags[DIRECTION_FLIP_FLAG] is False
    assert policy.status == "BLOCKED"
    assert "REPLACEMENT_PR399_LIVE_AUTHORITY_DISABLED" in policy.reason_codes
    assert policy.can_initiate_trade is False
    assert policy.can_increase_kelly is False
    assert policy.can_flip_direction is False
