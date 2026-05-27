# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/plans/2026-05-27-chain-local-position-model-refactor.md (Finding 7, PR B)
"""Invariants for Finding 7: chain snapshot completeness vs per-position venue visibility
are distinct domains and must have distinct names.

Two enums currently both named `ChainState` exist in the repo:
  - src/state/chain_state.py.ChainState       — per-cycle snapshot completeness
  - src/contracts/semantic_types.py.ChainState — per-position venue visibility

PR B keeps both class names wire-compatible but adds domain-specific aliases:
  - src/state/chain_state.py.ChainSnapshotCompleteness
  - src/contracts/semantic_types.py.VenueVisibilityStatus

This test verifies both aliases exist and point to the correct underlying enum.
A future PR may flip imports to the new names; legacy `ChainState` imports
stay green throughout that migration.
"""
from __future__ import annotations

import pytest


def test_chain_snapshot_completeness_alias_present() -> None:
    from src.state.chain_state import ChainSnapshotCompleteness, ChainState

    assert ChainSnapshotCompleteness is ChainState, (
        "ChainSnapshotCompleteness must alias src/state/chain_state.py.ChainState "
        "(per-cycle snapshot completeness). Renames must keep this alias for one release."
    )
    # Domain sanity: snapshot-completeness members start with CHAIN_*.
    assert {m.value for m in ChainSnapshotCompleteness} == {
        "chain_synced",
        "chain_empty",
        "chain_unknown",
    }


def test_venue_visibility_status_alias_present() -> None:
    from src.contracts.semantic_types import ChainState, VenueVisibilityStatus

    assert VenueVisibilityStatus is ChainState, (
        "VenueVisibilityStatus must alias src/contracts/semantic_types.py.ChainState "
        "(per-position venue visibility). Renames must keep this alias for one release."
    )
    # Domain sanity: visibility members include synced / local_only / chain_only.
    values = {m.value for m in VenueVisibilityStatus}
    for required in ("synced", "local_only", "chain_only", "exit_pending_missing"):
        assert required in values, f"{required!r} missing from VenueVisibilityStatus"


def test_two_chain_state_enums_are_NOT_the_same_class() -> None:
    """The two ChainState enums must remain different Python classes — they
    represent different real-world objects (Finding 7). If a future refactor
    accidentally unified them, this test fires."""
    from src.contracts.semantic_types import ChainState as VisibilityChainState
    from src.state.chain_state import ChainState as SnapshotChainState

    assert VisibilityChainState is not SnapshotChainState, (
        "Two ChainState enums collapsed into one class. They represent different "
        "domains (per-position visibility vs per-cycle snapshot completeness) "
        "and MUST remain distinct types — see Finding 7."
    )


def test_position_truth_module_exports_typed_facts() -> None:
    """PR B introduces typed facts. Verify imports work without side effects."""
    from src.contracts.position_truth import (
        CanonicalPositionEventKind,
        CausalityStatus,
        ChainOnlyFact,
        FillAuthority,
        LocalIntent,
        RecoveryAuthority,
        RecoveryGapFact,
        VenueOrderFact,
        VenuePositionFact,
        VenueTradeFact,
    )

    # FillAuthority covers the legacy string set plus the new
    # VENUE_POSITION_OBSERVED degraded-recovery slot.
    assert FillAuthority.VENUE_POSITION_OBSERVED.value == "venue_position_observed"

    # CanonicalPositionEventKind includes the new VENUE_POSITION_OBSERVED
    # and REVIEW_REQUIRED events that PR C/D will emit.
    kinds = {k.value for k in CanonicalPositionEventKind}
    assert "venue_position_observed" in kinds
    assert "review_required" in kinds


def test_local_intent_rejects_unknown_direction() -> None:
    """LocalIntent.direction is a typed boundary — synthetic chain-only inventory
    (direction='unknown') MUST NOT enter the local-intent type."""
    from src.contracts.position_truth import LocalIntent

    with pytest.raises(ValueError):
        LocalIntent(
            decision_id="d1",
            snapshot_id="s1",
            position_id="p1",
            market_id="m1",
            condition_id="c1",
            token_id="t1",
            direction="unknown",
            intended_notional_usd=10.0,
            submitted_limit_price=0.40,
            created_at="2026-05-27T12:00:00Z",
        )


def test_recovery_gap_fact_balance_only_is_NOT_training_eligible() -> None:
    """Finding 5 boundary: aggregate chain balance + intent + no trade fact =
    BALANCE_ONLY recovery, never training-eligible."""
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
    position_fact = VenuePositionFact(
        token_id="t1",
        condition_id="c1",
        size=25.0,
        avg_price=0.40,
        cost_basis=10.0,
        snapshot_id="s2",
        snapshot_completeness="chain_synced",
        observed_at="2026-05-27T12:01:00Z",
    )

    balance_only = RecoveryGapFact(
        intent=intent,
        position_fact=position_fact,
        recovery_authority=RecoveryAuthority.BALANCE_ONLY,
        causality_status=CausalityStatus.UNVERIFIED,
    )
    assert not balance_only.training_eligible

    trade_verified = RecoveryGapFact(
        intent=intent,
        position_fact=position_fact,
        recovery_authority=RecoveryAuthority.TRADE_VERIFIED,
        causality_status=CausalityStatus.OK,
    )
    assert trade_verified.training_eligible
