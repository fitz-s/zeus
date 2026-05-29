# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL P2 hard seam; CRITIC_SYNTHESIS_2026-05-29 Cons-SEV-1.C (unit
#   mis-scale). Live defect: build_ens_residual_evidence.py:204/224 converts the SETTLEMENT
#   value with members_unit, not the settlement's own unit. Masked today only because
#   members_unit == settlement unit for sampled rows (both degF); corrupts ~50C when a
# Lifecycle: created=2026-05-29; last_reviewed=2026-05-29; last_reused=never
# Purpose: Relationship invariant asserting residual_celsius converts each side (members and settlement) by its OWN unit — not a shared members_unit.
# Reuse: Run after any change to residual_celsius or unit-conversion logic in the residual computation path.

#   city has degC members + degF settlement (units ARE mixed across sources).
"""Relationship invariant: a residual must be computed in ONE consistent unit, with
each side converted by ITS OWN unit. ensemble members and the settlement value can be
stored in different units (mixed across sources) — the residual arithmetic must not
assume they share a unit.
"""

from __future__ import annotations

import pytest

from src.contracts.residual_value import residual_celsius


def test_both_fahrenheit_residual_in_celsius():
    # members mean 74 degF -> 23.333 C ; settlement 77 degF -> 25 C ; residual = -1.667
    r = residual_celsius([72.0, 74.0, 76.0], "degF", 77.0, "degF")
    assert r == pytest.approx((74 - 32) * 5 / 9 - (77 - 32) * 5 / 9, abs=1e-6)


def test_mixed_units_each_converted_by_its_own_unit():
    """members degC, settlement degF — the case the legacy members_unit-for-both
    conversion corrupts by ~50C. Contract converts each side correctly -> ~0."""
    # members mean 23 C ; settlement 73.4 degF -> 23 C ; residual ~ 0.0
    r = residual_celsius([22.0, 24.0], "degC", 73.4, "degF")
    assert r == pytest.approx(0.0, abs=0.05)


def test_legacy_members_unit_for_settlement_would_be_wrong():
    """Demonstrates the masked bug: had we converted the settlement with members_unit
    (degC) instead of its own (degF), the residual would be off by ~50C. The contract
    path must NOT reproduce that."""
    correct = residual_celsius([22.0, 24.0], "degC", 73.4, "degF")
    legacy_wrong = (23.0) - 73.4  # settlement 'converted' with degC = left as 73.4
    assert abs(correct - legacy_wrong) > 40.0  # the masked corruption magnitude


def test_invalid_members_unit_rejected():
    with pytest.raises(Exception):
        residual_celsius([20.0], "K", 70.0, "degF")


def test_invalid_settlement_unit_rejected():
    with pytest.raises(Exception):
        residual_celsius([20.0], "degC", 70.0, "K")


def test_empty_members_rejected():
    with pytest.raises(Exception):
        residual_celsius([], "degC", 70.0, "degF")
