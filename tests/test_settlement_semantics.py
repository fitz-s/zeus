# Created: 2026-04-27 (BATCH C of 2026-04-27 harness debate executor work)
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-27_harness_debate/round2_verdict.md
#   §1.1 #4 + §4.1 #4 + opponent §3.1 (relationship test for type-encoded HK
#   HKO antibody). Per Fitz "test relationships, not just functions" — these
#   tests verify the cross-module invariant survives, not just the function
#   arithmetic.

"""Relationship tests for SettlementRoundingPolicy + settle_market type encoding.

Three load-bearing relationship tests verify the cross-module invariant that a
wrong (city, policy) pair raises TypeError BEFORE any rounding happens. The
arithmetic correctness of WMO_HalfUp / HKO_Truncation themselves is incidental;
the load-bearing assertion is the type guard at the settle_market boundary.

Test count = 3 (per BATCH C dispatch baseline arithmetic 73 + 3 = 76).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.contracts.settlement_semantics import (
    HKO_Truncation,
    WMO_HalfUp,
    settle_market,
)


def test_hko_policy_required_for_hong_kong():
    """RELATIONSHIP: HK city + WMO policy → TypeError (wrong rounding for HK).

    Antibody for the YAML caution row in fatal_misreads.yaml:hong_kong_hko_explicit_caution_path.
    Type-encoded so the wrong combination is unconstructable, not merely
    documented (per Fitz Constraint #1).
    """
    with pytest.raises(TypeError, match=r"Hong Kong.*require.*HKO_Truncation"):
        settle_market("Hong Kong", Decimal("28.7"), WMO_HalfUp())


def test_hko_policy_invalid_for_non_hong_kong():
    """RELATIONSHIP: non-HK city + HKO policy → TypeError.

    HKO truncation is the wrong rounding semantics for any non-HK market;
    using it on (e.g.) New York would systematically produce 1°F-low
    settlement values vs the WU integer °F oracle.
    """
    with pytest.raises(TypeError, match=r"HKO_Truncation.*Hong Kong only"):
        settle_market("New York", Decimal("74.5"), HKO_Truncation())


def test_invalid_policy_type_rejected():
    """RELATIONSHIP: non-policy object → TypeError before any rounding happens.

    Defends the type contract at the settle_market boundary: only objects
    inheriting from SettlementRoundingPolicy may decide a settlement value;
    duck-typed substitutes are rejected.
    """
    class FakePolicy:  # NOT a SettlementRoundingPolicy subclass.
        def round_to_settlement(self, x: Decimal) -> int:
            return int(x)

    with pytest.raises(TypeError, match=r"requires a SettlementRoundingPolicy"):
        settle_market("New York", Decimal("74.5"), FakePolicy())  # type: ignore[arg-type]


# SIDECAR-3 (2026-04-28): C4 negative-half regression tests. Critic batch_C_review
# caught silent divergence between WMO_HalfUp (originally Decimal ROUND_HALF_UP =
# half-away-from-zero, -3.5→-4) and legacy round_wmo_half_up_value (np.floor(x+0.5) =
# asymmetric toward +∞, -3.5→-3). Legacy is the documented choice (file docstring
# settlement_semantics.py:19 + docs/reference/modules/contracts.md:89). DB has
# 11 negative settled values (-7..-1); raw forecast Monte Carlo can produce -X.5
# in NYC/Chicago winter — silent drift would have shifted settlement by 1°C on
# negative-half boundary cases. Three regression tests pin the legacy semantic.

def test_wmo_half_up_negative_half_rounds_toward_positive_infinity():
    """C4 regression: -3.5 → -3 (asymmetric half-up matches legacy + WMO 306)."""
    policy = WMO_HalfUp()
    assert policy.round_to_settlement(Decimal("-3.5")) == -3
    assert policy.round_to_settlement(Decimal("-0.5")) == 0
    assert policy.round_to_settlement(Decimal("-100.5")) == -100


def test_wmo_half_up_positive_half_rounds_up_unchanged():
    """Positive half-values unaffected by C4 fix; both semantics agree at +X.5."""
    policy = WMO_HalfUp()
    assert policy.round_to_settlement(Decimal("3.5")) == 4
    assert policy.round_to_settlement(Decimal("100.5")) == 101


def test_wmo_half_up_matches_legacy_round_wmo_half_up_value():
    """C4 regression: ABC must match legacy round_wmo_half_up_value byte-for-byte."""
    from src.contracts.settlement_semantics import round_wmo_half_up_value
    policy = WMO_HalfUp()
    test_cases = [3.5, -3.5, 0.5, -0.5, 28.5, -28.5, -100.5, 28.7, -28.7]
    for x in test_cases:
        legacy = int(round_wmo_half_up_value(x))
        new = policy.round_to_settlement(Decimal(str(x)))
        assert legacy == new, f"divergence at {x}: legacy={legacy} new={new}"
