from tests.test_replacement_forecast_runtime_policy import _capital_objective_evidence, _flags, _passing_evidence

from src.data.replacement_forecast_runtime_policy import SHADOW_FLAG, TRADE_AUTHORITY_FLAG, VETO_FLAG, resolve_replacement_forecast_runtime_policy


def test_hook_policy_surface_has_no_live_authority_in_pr399() -> None:
    policy = resolve_replacement_forecast_runtime_policy(
        _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True}),
        promotion_evidence=_passing_evidence(),
        capital_objective_evidence=_capital_objective_evidence(),
    )

    assert policy.status == "BLOCKED"
    assert policy.can_initiate_trade is False
