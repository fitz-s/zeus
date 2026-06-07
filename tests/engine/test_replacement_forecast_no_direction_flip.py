from tests.test_replacement_forecast_runtime_policy import _flags

from src.data.replacement_forecast_runtime_policy import DIRECTION_FLIP_FLAG, KELLY_INCREASE_FLAG, SHADOW_FLAG, TRADE_AUTHORITY_FLAG, VETO_FLAG, resolve_replacement_forecast_runtime_policy


def test_replacement_policy_cannot_flip_direction_in_pr399_even_when_flagged() -> None:
    policy = resolve_replacement_forecast_runtime_policy(
        _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True, KELLY_INCREASE_FLAG: True, DIRECTION_FLIP_FLAG: True})
    )

    assert policy.status == "BLOCKED"
    assert policy.can_flip_direction is False
