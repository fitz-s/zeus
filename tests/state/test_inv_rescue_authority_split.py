# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/archive/2026-Q2/plans_historical/2026-05-27-chain-local-position-model-refactor.md (Finding 5, PR C3)
"""Antibody invariants: rescue authority discriminated by linked venue trade fact.

Finding 5 (P1 likely bug): chain_reconciliation rescue previously promoted a
pending entry to active/verified using only aggregate chain balance — fill
time, exact submitted order identity, and exact avg-fill economics were all
implicit. The fix (PR C3): set fill_authority to a degraded
FILL_AUTHORITY_VENUE_POSITION_OBSERVED slot when there is no linked venue
trade fact, and to FILL_AUTHORITY_VENUE_CONFIRMED_FULL when one exists.

Why entry_fill_verified is not flipped to False:
  Several downstream consumers still gate on `entry_fill_verified`. Decoupling
  them from rescue-derived bools is PR D scope (typed FillAuthority projection
  via position_current schema). PR C3 introduces the authority signal that PR
  D's training gate will consume; entry_fill_verified remains a tradable-bool
  marker until that migration completes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_chain_reconciliation_imports_degraded_authority_constant() -> None:
    """The degraded recovery authority constant must be importable from portfolio."""
    from src.state.portfolio import (
        FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        FILL_AUTHORITY_VENUE_POSITION_OBSERVED,
    )

    assert FILL_AUTHORITY_VENUE_POSITION_OBSERVED == "venue_position_observed"
    assert FILL_AUTHORITY_VENUE_CONFIRMED_FULL == "venue_confirmed_full"
    assert FILL_AUTHORITY_VENUE_POSITION_OBSERVED != FILL_AUTHORITY_VENUE_CONFIRMED_FULL


def test_chain_reconciliation_rescue_branch_discriminates_authority() -> None:
    """Static-scan chain_reconciliation.py for the discrimination structure.

    The rescue branch must assign BOTH:
      FILL_AUTHORITY_VENUE_CONFIRMED_FULL      when linked-fill-fact is True
      FILL_AUTHORITY_VENUE_POSITION_OBSERVED   when linked-fill-fact is False

    This is a source-shape test; the runtime path tests live in
    test_live_safety_invariants.py.
    """
    src = (REPO_ROOT / "src" / "state" / "chain_reconciliation.py").read_text(encoding="utf-8")

    assert "rescued.fill_authority = FILL_AUTHORITY_VENUE_CONFIRMED_FULL" in src, (
        "Rescue branch must assign FILL_AUTHORITY_VENUE_CONFIRMED_FULL when "
        "_pending_entry_has_linked_fill_fact(pos) is True (Finding 5)."
    )
    assert "rescued.fill_authority = FILL_AUTHORITY_VENUE_POSITION_OBSERVED" in src, (
        "Rescue branch must assign FILL_AUTHORITY_VENUE_POSITION_OBSERVED when "
        "no linked fill fact exists (Finding 5 — degraded recovery)."
    )


def test_recovery_gap_fact_training_eligibility_gate() -> None:
    """RecoveryGapFact.training_eligible is the type-boundary that PR D's
    training gate must consult. Verify both terminal cases here."""
    from src.contracts.position_truth import (
        CausalityStatus,
        LocalIntent,
        RecoveryAuthority,
        RecoveryGapFact,
        VenuePositionFact,
    )

    intent = LocalIntent(
        decision_id="d1",
        snapshot_id="s1",
        position_id="p1",
        market_id="m1",
        condition_id="c1",
        token_id="t1",
        direction="buy_yes",
        intended_notional_usd=10.0,
        submitted_limit_price=0.40,
        created_at="2026-05-27T12:00:00Z",
    )
    venue_fact = VenuePositionFact(
        token_id="t1",
        condition_id="c1",
        size=25.0,
        avg_price=0.40,
        cost_basis=10.0,
        snapshot_id="s2",
        snapshot_completeness="chain_synced",
        observed_at="2026-05-27T12:01:00Z",
    )

    degraded = RecoveryGapFact(
        intent=intent,
        position_fact=venue_fact,
        recovery_authority=RecoveryAuthority.BALANCE_ONLY,
        causality_status=CausalityStatus.UNVERIFIED,
    )
    assert not degraded.training_eligible

    verified = RecoveryGapFact(
        intent=intent,
        position_fact=venue_fact,
        recovery_authority=RecoveryAuthority.TRADE_VERIFIED,
        causality_status=CausalityStatus.OK,
    )
    assert verified.training_eligible
