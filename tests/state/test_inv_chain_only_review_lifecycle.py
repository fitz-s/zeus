# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/archive/2026-Q2/plans_historical/2026-05-27-chain-local-refactor-part2-findings.md (Finding D1)
"""Antibody invariants: `ChainOnlyFact` carries a typed review lifecycle.

Finding D1 (P1/P2, Part-2 audit 2026-05-27): PR C2/E2 in PR #347
replaced fake `Position` with `ChainOnlyFact` and durable suppression
rows, but `check_quarantine_timeouts()` still iterated
`portfolio.positions` only — no 48h timeout/escalation fold for
`chain_only_facts`. README said "48h forced exit eval" but no consumer
implemented it for the new carrier. Consequence: chain-only inventory
could block entries indefinitely until operator manually cleared.

PR D1 introduces a typed review lifecycle on `ChainOnlyFact`:

  UNRESOLVED   — detected within the last 48h, entry gate fires.
  EXPIRED      — past the 48h review window; entry gate STILL fires
                 (expiry is escalation, not auto-resolve). Operator
                 dashboards surface expired facts for triage.
  ACKNOWLEDGED — operator reviewed and chose to keep active; gate fires.
  RESOLVED     — suppression_reason flipped to operator_quarantine_clear
                 or settled_position; entry gate does NOT fire.

`review_state` is derived from `suppression_reason` + `first_seen_at` age
by `src.state.portfolio._derive_chain_only_review_state`. No schema
migration — existing suppression_reason transitions already encode the
operator-side state machine.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.contracts.position_truth import (
    CHAIN_ONLY_REVIEW_WINDOW_HOURS,
    ChainOnlyFact,
    ChainOnlyReviewState,
)
from src.state.canonical_asset_exposure import chain_only_worst_case_add_usd
from src.state.portfolio import (
    PortfolioState,
    _derive_chain_only_review_state,
)


def test_chain_only_review_state_enum_members() -> None:
    """Lifecycle states are typed; bare strings are forbidden."""
    assert ChainOnlyReviewState.UNRESOLVED.value == "unresolved"
    assert ChainOnlyReviewState.EXPIRED.value == "expired"
    assert ChainOnlyReviewState.ACKNOWLEDGED.value == "acknowledged"
    assert ChainOnlyReviewState.RESOLVED.value == "resolved"


def test_derive_unresolved_for_recent_chain_only_quarantined() -> None:
    """Fresh chain_only_quarantined row is UNRESOLVED."""
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    first_seen = (now - timedelta(hours=2)).isoformat()
    state = _derive_chain_only_review_state(
        suppression_reason="chain_only_quarantined",
        first_seen_at=first_seen,
        now=now,
    )
    assert state == ChainOnlyReviewState.UNRESOLVED


def test_derive_expired_after_review_window() -> None:
    """chain_only_quarantined past 48h is EXPIRED (escalation, not resolution)."""
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    first_seen = (
        now - timedelta(hours=CHAIN_ONLY_REVIEW_WINDOW_HOURS + 1)
    ).isoformat()
    state = _derive_chain_only_review_state(
        suppression_reason="chain_only_quarantined",
        first_seen_at=first_seen,
        now=now,
    )
    assert state == ChainOnlyReviewState.EXPIRED


def test_derive_resolved_for_operator_clear() -> None:
    state = _derive_chain_only_review_state(
        suppression_reason="operator_quarantine_clear",
        first_seen_at="2026-05-01T00:00:00Z",
    )
    assert state == ChainOnlyReviewState.RESOLVED


def test_derive_resolved_for_settled() -> None:
    state = _derive_chain_only_review_state(
        suppression_reason="settled_position",
        first_seen_at="2026-05-01T00:00:00Z",
    )
    assert state == ChainOnlyReviewState.RESOLVED


def test_derive_unresolved_for_unknown_reason() -> None:
    """Defensive: unknown suppression_reason fails-safe to UNRESOLVED so the gate fires."""
    state = _derive_chain_only_review_state(
        suppression_reason="future_reason_not_yet_known",
        first_seen_at="2026-05-27T11:00:00Z",
    )
    assert state == ChainOnlyReviewState.UNRESOLVED


def test_derive_unresolved_for_missing_first_seen() -> None:
    """Missing first_seen_at is fail-safe to UNRESOLVED (cannot prove expiry)."""
    state = _derive_chain_only_review_state(
        suppression_reason="chain_only_quarantined",
        first_seen_at="",
    )
    assert state == ChainOnlyReviewState.UNRESOLVED


def test_derive_unresolved_for_unparseable_first_seen() -> None:
    """Garbage first_seen_at is fail-safe to UNRESOLVED."""
    state = _derive_chain_only_review_state(
        suppression_reason="chain_only_quarantined",
        first_seen_at="not-an-iso-timestamp",
    )
    assert state == ChainOnlyReviewState.UNRESOLVED


def test_blocks_entry_property() -> None:
    """Only current unresolved/acknowledged facts block unrelated new entries."""
    base = dict(
        token_id="t", condition_id="c", size=1.0, avg_price=0.4,
        cost_basis=0.4, first_seen_at="2026-05-27T11:00:00Z",
        last_seen_at="2026-05-27T11:00:00Z",
    )
    for s in (ChainOnlyReviewState.UNRESOLVED, ChainOnlyReviewState.ACKNOWLEDGED):
        assert ChainOnlyFact(review_state=s, **base).blocks_entry is True, s
    assert ChainOnlyFact(review_state=ChainOnlyReviewState.EXPIRED, **base).blocks_entry is False
    assert ChainOnlyFact(review_state=ChainOnlyReviewState.RESOLVED, **base).blocks_entry is False


def test_entry_gate_blocks_on_unresolved_chain_only_fact() -> None:
    """Quarantine excision T2: the retired portfolio-wide
    ``_has_quarantined_positions`` gate is replaced by the worst-case exposure
    reducer (src.state.canonical_asset_exposure.chain_only_worst_case_add_usd)
    + family-scoped block (blocked_family_keys) — both still respect
    ChainOnlyFact.blocks_entry exactly as the retired gate did. This test
    asserts the exposure leg: an UNRESOLVED fact's size counts (shares x $1
    CTF bound), same review_state gating the retired gate used.
    """
    fact = ChainOnlyFact(
        token_id="t", condition_id="c", size=1.0, avg_price=0.4,
        cost_basis=0.4, first_seen_at="2026-05-27T11:00:00Z",
        last_seen_at="2026-05-27T11:00:00Z",
        review_state=ChainOnlyReviewState.UNRESOLVED,
    )
    portfolio = PortfolioState(positions=[], chain_only_facts=[fact])
    add_usd, _any_unmapped = chain_only_worst_case_add_usd(None, portfolio)
    assert add_usd == 1.0


def test_entry_gate_does_not_block_on_resolved_chain_only_fact() -> None:
    fact = ChainOnlyFact(
        token_id="t", condition_id="c", size=1.0, avg_price=0.4,
        cost_basis=0.4, first_seen_at="2026-05-27T11:00:00Z",
        last_seen_at="2026-05-27T11:00:00Z",
        review_state=ChainOnlyReviewState.RESOLVED,
    )
    portfolio = PortfolioState(positions=[], chain_only_facts=[fact])
    add_usd, _any_unmapped = chain_only_worst_case_add_usd(None, portfolio)
    assert add_usd == 0.0


def test_entry_gate_does_not_block_on_expired_chain_only_fact() -> None:
    """Expiry is review debt, not a permanent global entry freeze."""
    fact = ChainOnlyFact(
        token_id="t", condition_id="c", size=1.0, avg_price=0.4,
        cost_basis=0.4, first_seen_at="2026-05-20T11:00:00Z",
        last_seen_at="2026-05-27T11:00:00Z",
        review_state=ChainOnlyReviewState.EXPIRED,
    )
    portfolio = PortfolioState(positions=[], chain_only_facts=[fact])
    add_usd, _any_unmapped = chain_only_worst_case_add_usd(None, portfolio)
    assert add_usd == 0.0
