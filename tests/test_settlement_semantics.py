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
