from tests.test_replacement_forecast_runtime_policy import _flags

from src.data.replacement_forecast_runtime_policy import KELLY_INCREASE_FLAG, SHADOW_FLAG, TRADE_AUTHORITY_FLAG, VETO_FLAG, resolve_replacement_forecast_runtime_policy


def test_replacement_policy_cannot_increase_kelly_in_pr399_even_when_flagged() -> None:
    policy = resolve_replacement_forecast_runtime_policy(
        _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True, KELLY_INCREASE_FLAG: True})
    )

    assert policy.status == "BLOCKED"
    assert policy.can_increase_kelly is False
