import json
from pathlib import Path

from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    resolve_replacement_forecast_runtime_policy,
)


def test_current_replacement_settings_are_direct_new_data_live_authority() -> None:
    flags = json.loads((Path(__file__).resolve().parents[1] / "config/settings.json").read_text())["feature_flags"]

    policy = resolve_replacement_forecast_runtime_policy(flags)

    assert flags[VETO_FLAG] is True
    assert flags[TRADE_AUTHORITY_FLAG] is True
    assert flags[KELLY_INCREASE_FLAG] is True
    assert flags[DIRECTION_FLIP_FLAG] is True
    assert policy.status == "LIVE_AUTHORITY"
    assert policy.can_initiate_trade is True
    assert policy.can_increase_kelly is True
    assert policy.can_flip_direction is True


def test_trade_authority_fixture_is_live_authority() -> None:
    flags = json.loads((Path(__file__).resolve().parents[1] / "config/settings.json").read_text())["feature_flags"]
    flags = dict(flags)
    flags[TRADE_AUTHORITY_FLAG] = True

    policy = resolve_replacement_forecast_runtime_policy(flags)

    assert policy.status == "LIVE_AUTHORITY"
    assert policy.reason_codes == ("REPLACEMENT_NEW_DATA_LIVE_AUTHORITY",)
    assert policy.can_initiate_trade is True
