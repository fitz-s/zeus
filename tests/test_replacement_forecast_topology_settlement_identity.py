# Lifecycle: created=2026-06-07; last_reviewed=2026-06-07; last_reused=2026-06-07
# Purpose: H4 antibody — settlement_unit/rounding_rule must be part of topology-core equivalence so mismatched settlement semantics never share a market via hash-mismatch fallback.
# Reuse: Run with pytest; update if _topology_core or topology equivalence logic in replacement_forecast_bundle_reader changes.
# Created: 2026-06-07
# Last reused or audited: 2026-06-07
# Authority basis: REAUDIT_0_1.md §2 H4 (topology-core drops settlement_unit/rounding_rule) + §4.
"""H4 antibody — settlement identity must be part of topology-core equivalence.

Relationship test across the posterior->market settlement boundary: two
topologies with IDENTICAL physical Celsius geometry (lower_c/upper_c/center_c/
settlement_step_c) but DIFFERENT settlement semantics (rounding_rule or
settlement_unit) must NOT be treated as equivalent. ``rounding_rule`` changes
which integer the oracle settles to at a bin boundary, so two posteriors that
agree on geometry but disagree on rounding settle to different outcomes — they
are NOT the same settlement identity and must not bind to the same market via
the hash-mismatch fallback.
"""

from __future__ import annotations

from src.data.replacement_forecast_bundle_reader import (
    _topology_core,
    _topology_core_equivalent,
)


def _bin(*, rounding_rule: str = "wmo_half_up", settlement_unit: str = "C", display_unit: str = "C") -> dict[str, object]:
    return {
        "bin_id": "warm",
        "lower_c": 20.0,
        "upper_c": 21.0,
        "center_c": 20.5,
        "settlement_step_c": 1.0,
        "display_unit": display_unit,
        "settlement_unit": settlement_unit,
        "rounding_rule": rounding_rule,
    }


def test_topology_core_retains_rounding_rule() -> None:
    core = _topology_core([_bin(rounding_rule="wmo_half_up")])
    assert core is not None
    assert core[0]["rounding_rule"] == "wmo_half_up"


def test_topology_core_retains_settlement_unit() -> None:
    core = _topology_core([_bin(settlement_unit="F")])
    assert core is not None
    assert core[0]["settlement_unit"] == "F"


def test_topology_core_excludes_display_unit_pure_presentation() -> None:
    # display_unit is pure presentation; differing display_unit alone (same
    # geometry + settlement identity) must remain equivalent.
    left = [_bin(display_unit="C")]
    right = [_bin(display_unit="F")]
    assert _topology_core_equivalent(left, right) is True


def test_different_rounding_rule_same_geometry_not_equivalent() -> None:
    # Same physical geometry, different rounding_rule (wmo_half_up vs the hko
    # oracle_truncate) => DIFFERENT settlement identity => NOT equivalent.
    left = [_bin(rounding_rule="wmo_half_up")]
    right = [_bin(rounding_rule="oracle_truncate")]
    assert _topology_core_equivalent(left, right) is False


def test_different_settlement_unit_same_geometry_not_equivalent() -> None:
    left = [_bin(settlement_unit="C")]
    right = [_bin(settlement_unit="F")]
    assert _topology_core_equivalent(left, right) is False


def test_identical_settlement_identity_is_equivalent() -> None:
    left = [_bin(rounding_rule="wmo_half_up", settlement_unit="C")]
    right = [_bin(rounding_rule="wmo_half_up", settlement_unit="C")]
    assert _topology_core_equivalent(left, right) is True
