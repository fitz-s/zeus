# Created: 2026-06-06
# Last reused/audited: 2026-06-07
# Authority basis: PR_SPEC.md §2 FIX-1 (evidence load-bearing for LIVE_AUTHORITY); §0.3 antibody.
# History: re-authored 2026-06-07 to invert the post-f0368a188c "evidence does not
#   gate" contamination assertions. FIX-1 requires a PASSING evidence object before
#   LIVE_AUTHORITY; flags alone can never reach it.
#   2026-06-07 (ITEM B): FIX-1 tightened OR -> AND. A SINGLE passing evidence object
#   is no longer sufficient; this file now matches its own name — NO single-evidence
#   path reaches LIVE_AUTHORITY. Promotion (statistical validation) and capital
#   objective (empirical winner + after-cost EV) are DIFFERENT proofs, BOTH required.
from tests.test_replacement_forecast_runtime_policy import _capital_objective_evidence, _flags, _passing_evidence

from src.data.replacement_forecast_runtime_policy import (
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    resolve_replacement_forecast_runtime_policy,
)


def test_flags_alone_cannot_reach_live_authority() -> None:
    """§0.3 antibody: flags-all-true with NO evidence must fail closed, never
    LIVE_AUTHORITY. Evidence is load-bearing by type, not theater."""

    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})
    policy = resolve_replacement_forecast_runtime_policy(flags)

    assert policy.status == "BLOCKED"
    assert "REPLACEMENT_PROMOTION_EVIDENCE_REQUIRED" in policy.reason_codes
    assert "REPLACEMENT_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED" in policy.reason_codes
    assert policy.can_initiate_trade is False


def test_passing_promotion_evidence_alone_does_not_grant_live_authority() -> None:
    """ITEM B: promotion evidence is NECESSARY but NOT SUFFICIENT. Without a passing
    capital-objective evidence the path is BLOCKED (strictly-more-restrictive than
    SHADOW_VETO_ONLY; the existing no-evidence guard is preserved)."""
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})
    policy = resolve_replacement_forecast_runtime_policy(flags, promotion_evidence=_passing_evidence())

    assert policy.status != "LIVE_AUTHORITY"
    assert policy.status == "BLOCKED"
    assert "REPLACEMENT_LIVE_AUTHORITY_REQUIRES_EVIDENCE" in policy.reason_codes
    assert "REPLACEMENT_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED" in policy.reason_codes
    assert policy.can_initiate_trade is False


def test_passing_capital_objective_evidence_alone_does_not_grant_live_authority() -> None:
    """ITEM B: capital-objective evidence is NECESSARY but NOT SUFFICIENT. Without a
    passing promotion evidence the path is BLOCKED."""
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})
    policy = resolve_replacement_forecast_runtime_policy(flags, capital_objective_evidence=_capital_objective_evidence())

    assert policy.status != "LIVE_AUTHORITY"
    assert policy.status == "BLOCKED"
    assert "REPLACEMENT_LIVE_AUTHORITY_REQUIRES_EVIDENCE" in policy.reason_codes
    assert "REPLACEMENT_PROMOTION_EVIDENCE_REQUIRED" in policy.reason_codes
    assert policy.can_initiate_trade is False


def test_only_both_passing_evidence_objects_grant_live_authority() -> None:
    """ITEM B antibody: only the conjunction of BOTH passing evidence objects
    reaches LIVE_AUTHORITY (AND, not OR)."""
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})
    policy = resolve_replacement_forecast_runtime_policy(
        flags,
        promotion_evidence=_passing_evidence(),
        capital_objective_evidence=_capital_objective_evidence(),
    )

    assert policy.status == "LIVE_AUTHORITY"
    assert policy.reason_codes == ("REPLACEMENT_PROMOTED_WITH_EVIDENCE",)
    assert policy.can_initiate_trade is True
