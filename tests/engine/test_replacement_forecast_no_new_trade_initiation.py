from tests.test_replacement_forecast_runtime_policy import _flags

from src.data.replacement_forecast_runtime_policy import SHADOW_FLAG, TRADE_AUTHORITY_FLAG, VETO_FLAG, resolve_replacement_forecast_runtime_policy


def test_replacement_policy_cannot_initiate_new_trade_in_pr399() -> None:
    policy = resolve_replacement_forecast_runtime_policy(_flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True}))

    assert policy.can_initiate_trade is False
