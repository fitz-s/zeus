from tests.test_replacement_forecast_runtime_policy import _capital_objective_evidence, _flags, _passing_evidence

from src.data.replacement_forecast_runtime_policy import (
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    resolve_replacement_forecast_runtime_policy,
)


def test_promotion_evidence_alone_cannot_enable_live_authority() -> None:
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})
    policy = resolve_replacement_forecast_runtime_policy(flags, promotion_evidence=_passing_evidence())

    assert policy.status == "BLOCKED"
    assert "REPLACEMENT_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED" in policy.reason_codes
    assert policy.can_initiate_trade is False


def test_capital_objective_evidence_alone_cannot_enable_live_authority() -> None:
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})
    policy = resolve_replacement_forecast_runtime_policy(flags, capital_objective_evidence=_capital_objective_evidence())

    assert policy.status == "BLOCKED"
    assert "REPLACEMENT_PROMOTION_EVIDENCE_REQUIRED" in policy.reason_codes
    assert policy.can_initiate_trade is False
