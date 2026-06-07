from tests.test_replacement_forecast_runtime_policy import _capital_objective_evidence, _flags, _passing_evidence

from src.data.replacement_forecast_runtime_policy import (
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    resolve_replacement_forecast_runtime_policy,
)


def test_runtime_policy_helper_returns_direct_live_authority_when_trade_flagged() -> None:
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})

    policy = resolve_replacement_forecast_runtime_policy(
        flags,
        promotion_evidence=_passing_evidence(),
        capital_objective_evidence=_capital_objective_evidence(),
    )

    assert policy.status == "LIVE_AUTHORITY"
    assert policy.can_initiate_trade is True
