# Created: 2026-06-06
# Last reused/audited: 2026-06-07
# Authority basis: PR_SPEC.md §2 FIX-1 (evidence load-bearing for LIVE_AUTHORITY).
# History: re-authored 2026-06-07. The post-f0368a188c body asserted
#   reason_codes == ("REPLACEMENT_NEW_DATA_LIVE_AUTHORITY",) for the flags+evidence
#   path; FIX-1 makes the authority basis name reflect WHICH evidence carried it.
from tests.test_replacement_forecast_runtime_policy import _capital_objective_evidence, _flags, _passing_evidence

from src.data.replacement_forecast_runtime_policy import (
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    resolve_replacement_forecast_runtime_policy,
)


def test_live_authority_basis_names_the_evidence_that_carried_it() -> None:
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})

    policy = resolve_replacement_forecast_runtime_policy(
        flags,
        promotion_evidence=_passing_evidence(),
        capital_objective_evidence=_capital_objective_evidence(),
    )

    assert policy.status == "LIVE_AUTHORITY"
    # promotion-evidence path takes precedence when it passes.
    assert policy.reason_codes == ("REPLACEMENT_PROMOTED_WITH_EVIDENCE",)
    assert policy.can_initiate_trade is True


def test_trade_authority_without_any_evidence_is_blocked() -> None:
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})

    policy = resolve_replacement_forecast_runtime_policy(flags)

    assert policy.status == "BLOCKED"
    assert "REPLACEMENT_PROMOTION_EVIDENCE_REQUIRED" in policy.reason_codes
    assert "REPLACEMENT_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED" in policy.reason_codes
    assert policy.can_initiate_trade is False
