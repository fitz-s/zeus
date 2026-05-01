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


# INV-X — for_city() routing antibody (ultrareview25_remediation 2026-05-01 P0-5)
def test_settlement_semantics_construction_routes_through_for_city():
    """RELATIONSHIP antibody: production code constructs SettlementSemantics
    ONLY through the `for_city()` factory (or the per-unit
    `default_wu_fahrenheit`/`default_wu_celsius` helpers it composes).

    This is the SOCIAL gate that makes the wrong-rounding-for-wrong-city
    failure mode structurally impossible WITHOUT requiring the type-encoded
    `settle_market()` migration. The factory dispatches:
      - settlement_source_type == 'hko'  → rounding_rule='oracle_truncate'
      - everything else                  → rounding_rule='wmo_half_up'
    so a caller cannot accidentally apply WMO half-up to Hong Kong (or
    oracle_truncate to anywhere else) without bypassing the factory.

    The 2026-05-01 review (docs/operations/repo_review_2026-05-01/SYNTHESIS.md
    P0-5 reclassification) found that production has zero direct
    `SettlementSemantics(...)` constructor calls outside settlement_semantics.py
    itself. This test pins that discipline so a future agent cannot
    silently introduce a direct construction with arbitrary rounding_rule —
    the test fails immediately and the maintainer has to either (a) route the
    new call through `for_city()`, or (b) extend the factory to handle the
    new dispatch case.

    settle_market() / SettlementRoundingPolicy / WMO_HalfUp / HKO_Truncation
    remain as the FUTURE TYPE-ENCODED migration target (Tier 3 P8 per the
    settlement_semantics.py:194 author note). Until that migration lands,
    the SOCIAL gate enforced by this test is what holds the line.
    """
    import re
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    semantics_file = repo_root / "src/contracts/settlement_semantics.py"

    # Pattern: a line that constructs SettlementSemantics with parens, e.g.
    # `SettlementSemantics(`, `cls(`, `= SettlementSemantics(`. We allow lines
    # inside settlement_semantics.py itself (factory + classmethods) and
    # rule out anywhere else under src/.
    construct_re = re.compile(r"\bSettlementSemantics\s*\(")

    offending = []
    for path in src_dir.rglob("*.py"):
        if path == semantics_file:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            # Skip imports and type annotations — they reference the class but
            # don't construct an instance.
            if stripped.startswith(("import ", "from ", "#")):
                continue
            if "->" in line and "SettlementSemantics" in line.split("->")[1]:
                continue
            if construct_re.search(line):
                offending.append(f"{path.relative_to(repo_root)}:{lineno}: {stripped}")

    assert not offending, (
        "INV-X violation: direct SettlementSemantics(...) construction "
        "detected outside src/contracts/settlement_semantics.py. Route the "
        "construction through `SettlementSemantics.for_city(city)` instead "
        "so the city↔rounding_rule dispatch contract holds. If a new "
        "settlement source family is being added, extend `for_city()` (and "
        "this test's docstring), not the call site. Offending lines:\n  "
        + "\n  ".join(offending)
    )

    # Also assert no `rounding_rule="..."` kwarg-with-string-literal appears
    # outside the canonical module — that's the dispatch-bypass shape (a
    # caller hard-coding a rounding rule rather than going through
    # for_city()). Plain attribute reads like `r = sem.rounding_rule` are
    # legit and not flagged.
    rule_re = re.compile(r"\brounding_rule\s*=\s*['\"]")
    rule_offenders = []
    for path in src_dir.rglob("*.py"):
        if path == semantics_file:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if rule_re.search(line):
                rule_offenders.append(
                    f"{path.relative_to(repo_root)}:{lineno}: {line.strip()}"
                )

    assert not rule_offenders, (
        "INV-X violation: bare `rounding_rule='...'` literal outside "
        "src/contracts/settlement_semantics.py. The string-dispatch "
        "rounding_rule must only appear inside the canonical module so the "
        "for_city() factory is the single source of dispatch. Offending "
        "lines:\n  " + "\n  ".join(rule_offenders)
    )
