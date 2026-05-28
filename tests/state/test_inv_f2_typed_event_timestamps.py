# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/findings_2026_05_28.md §F2
"""Acceptance tests: F2 — event timestamps must be typed/explicit.

F2 invariant (docs/findings_2026_05_28.md §F2):
  Every canonical event builder receives an explicit event_observed_at
  parameter. VENUE_POSITION_OBSERVED.occurred_at = venue_observed_at.
  REVIEW_REQUIRED.occurred_at = review_detected_at.
  CHAIN_SYNCED.occurred_at = chain_synced_at.
  entered_at is only used as event time when the event IS the entry fill.
  Lexicographic string max() must NOT be used for ISO timestamps with
  mixed Z / +00:00 suffixes.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_active_position(**overrides: Any) -> Any:
    from src.state.portfolio import Position

    defaults: dict[str, Any] = dict(
        trade_id="f2-001",
        market_id="mkt-f2",
        city="Chicago",
        cluster="Great Lakes",
        target_date="2026-06-15",
        bin_label="60-65",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.55,
        shares=25.0,
        cost_basis_usd=10.0,
        state="entered",
        chain_state="synced",
        token_id="tok-f2",
        unit="F",
        env="live",
        entered_at="2026-05-01T00:00:00Z",       # stale legacy timestamp
        chain_verified_at="2026-05-10T00:00:00Z", # also stale
        condition_id="cond-f2",
        strategy_key="center_buy",
        strategy="center_buy",
    )
    defaults.update(overrides)
    return Position(**defaults)


def _make_quarantined_position(**overrides: Any) -> Any:
    from src.state.portfolio import Position

    defaults: dict[str, Any] = dict(
        trade_id="f2-quar-001",
        market_id="mkt-f2-q",
        city="Chicago",
        cluster="Great Lakes",
        target_date="2026-06-15",
        bin_label="60-65",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.55,
        shares=25.0,
        cost_basis_usd=10.0,
        state="quarantined",
        chain_state="size_mismatch_unresolved",
        token_id="tok-f2-q",
        unit="F",
        env="live",
        quarantined_at="2026-05-01T00:00:00Z",       # stale legacy timestamp
        chain_verified_at="2026-05-10T00:00:00Z",     # also stale
        condition_id="cond-f2-q",
        strategy_key="center_buy",
        strategy="center_buy",
    )
    defaults.update(overrides)
    return Position(**defaults)


# ---------------------------------------------------------------------------
# Test 1: VENUE_POSITION_OBSERVED.occurred_at = venue_observed_at, not entered_at
# ---------------------------------------------------------------------------

def test_venue_position_observed_uses_venue_observed_at_not_entered_at() -> None:
    """F2 invariant: VENUE_POSITION_OBSERVED.occurred_at must equal the explicit
    venue_observed_at parameter, NOT the legacy position.entered_at or
    position.chain_verified_at attributes.

    The stale entered_at ("2026-05-01…") and chain_verified_at ("2026-05-10…")
    are both older than the observation time ("2026-05-28…"). With the old
    _non_empty fallback, the occurred_at would have been the entered_at value
    (first non-empty wins). With the F2 fix, occurred_at must equal the
    explicit venue_observed_at argument regardless of position attributes.
    """
    from src.engine.lifecycle_events import build_venue_position_observed_canonical_write

    stale_entered_at = "2026-05-01T00:00:00Z"
    stale_chain_verified_at = "2026-05-10T00:00:00Z"
    venue_observed_at = "2026-05-28T14:30:00Z"

    pos = _make_active_position(
        entered_at=stale_entered_at,
        chain_verified_at=stale_chain_verified_at,
    )

    events, _ = build_venue_position_observed_canonical_write(
        pos,
        venue_observed_at=venue_observed_at,
        sequence_no=1,
        source_module="tests.state.test_inv_f2_typed_event_timestamps",
    )

    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "VENUE_POSITION_OBSERVED"
    assert ev["occurred_at"] == venue_observed_at, (
        f"VENUE_POSITION_OBSERVED.occurred_at must equal venue_observed_at={venue_observed_at!r}; "
        f"got {ev['occurred_at']!r}. Pre-F2 behaviour would have returned the stale "
        f"entered_at={stale_entered_at!r} via _non_empty fallback."
    )
    # Explicit contract: must NOT be a stale legacy timestamp.
    assert ev["occurred_at"] != stale_entered_at
    assert ev["occurred_at"] != stale_chain_verified_at


# ---------------------------------------------------------------------------
# Test 2: REVIEW_REQUIRED.occurred_at = review_detected_at, not quarantined_at
# ---------------------------------------------------------------------------

def test_review_required_uses_review_detected_at_not_quarantined_at() -> None:
    """F2 invariant: REVIEW_REQUIRED.occurred_at must equal the explicit
    review_detected_at parameter, NOT the legacy position.quarantined_at or
    position.chain_verified_at attributes.
    """
    from src.engine.lifecycle_events import build_review_required_canonical_write

    stale_quarantined_at = "2026-05-01T00:00:00Z"
    stale_chain_verified_at = "2026-05-10T00:00:00Z"
    review_detected_at = "2026-05-28T14:45:00Z"

    pos = _make_quarantined_position(
        quarantined_at=stale_quarantined_at,
        chain_verified_at=stale_chain_verified_at,
    )

    events, _ = build_review_required_canonical_write(
        pos,
        review_detected_at=review_detected_at,
        reason="size_mismatch_unresolved_no_canonical_baseline",
        sequence_no=1,
        source_module="tests.state.test_inv_f2_typed_event_timestamps",
    )

    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "REVIEW_REQUIRED"
    assert ev["occurred_at"] == review_detected_at, (
        f"REVIEW_REQUIRED.occurred_at must equal review_detected_at={review_detected_at!r}; "
        f"got {ev['occurred_at']!r}. Pre-F2 behaviour would have returned the stale "
        f"quarantined_at={stale_quarantined_at!r} via _non_empty fallback."
    )
    assert ev["occurred_at"] != stale_quarantined_at
    assert ev["occurred_at"] != stale_chain_verified_at


# ---------------------------------------------------------------------------
# Test 3: _max_iso_chronological handles mixed Z / +00:00 suffix correctly
# ---------------------------------------------------------------------------

def test_max_iso_chronological_handles_mixed_suffix_correctly() -> None:
    """F2 invariant: _max_iso_chronological must return the chronologically
    latest timestamp, not the lexicographically largest string.

    The bug case: lexicographic max fails when strings mix 'Z' (0x5A) and
    '+00:00' (0x2B) suffixes because '+' < 'Z' in ASCII. For example:
      "2026-05-28T10:30:00+00:00" vs "2026-05-28T10:00:00Z"
    Lexicographic: "2026-05-28T10:00:00Z" wins (Z > +).
    Chronological: "2026-05-28T10:30:00+00:00" wins (it's 30 minutes later).
    """
    from src.engine.lifecycle_events import _max_iso_chronological

    # Case 1: +00:00 suffix string is chronologically later but
    # lexicographically smaller than the Z suffix string.
    later_with_offset = "2026-05-28T10:30:00+00:00"
    earlier_with_z = "2026-05-28T10:00:00Z"

    result = _max_iso_chronological(earlier_with_z, later_with_offset)
    assert result == later_with_offset, (
        f"Expected the chronologically later string {later_with_offset!r} "
        f"but got {result!r}. Lexicographic max would have returned {earlier_with_z!r} "
        f"because 'Z' (0x5A) > '+' (0x2B) in ASCII."
    )

    # Case 2: Same instant, different suffix representations.
    same_z = "2026-05-28T12:00:00Z"
    same_offset = "2026-05-28T12:00:00+00:00"
    result2 = _max_iso_chronological(same_z, same_offset)
    # Either representation is acceptable for equal timestamps.
    assert result2 in (same_z, same_offset)

    # Case 3: Normal ordering still works with all-Z inputs.
    result3 = _max_iso_chronological(
        "2026-05-01T00:00:00Z",
        "2026-05-28T00:00:00Z",
        "2026-05-15T00:00:00Z",
    )
    assert result3 == "2026-05-28T00:00:00Z"

    # Case 4: Normal ordering with all +00:00 inputs.
    result4 = _max_iso_chronological(
        "2026-05-01T00:00:00+00:00",
        "2026-05-28T00:00:00+00:00",
        "2026-05-15T00:00:00+00:00",
    )
    assert result4 == "2026-05-28T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Test 4: AST static check — F2 builders do NOT use _non_empty for occurred_at
# ---------------------------------------------------------------------------

def test_no_f2_builder_uses_non_empty_fallback_for_event_occurred_at() -> None:
    """F2 invariant (static AST): the three F2 builders must derive their
    occurred_at from an explicit parameter, not from _non_empty(legacy_field, ...).

    Walks the AST of lifecycle_events.py and for each of the three named F2
    builders asserts that no assignment ``occurred_at = _non_empty(...)``
    exists inside the function body.

    This test is a structural antibody: once the F2 fix ships, any regression
    that reintroduces the fallback chain will be caught here before runtime.
    """
    import ast as _ast

    module_path = Path(__file__).parents[2] / "src" / "engine" / "lifecycle_events.py"
    source = module_path.read_text()
    tree = _ast.parse(source)

    F2_BUILDER_NAMES = {
        "build_venue_position_observed_canonical_write",
        "build_review_required_canonical_write",
        "build_reconciliation_rescue_canonical_write",
    }

    def _has_non_empty_occurred_at(func_node: _ast.FunctionDef) -> bool:
        """Return True if func_node contains: occurred_at = _non_empty(...)"""
        for node in _ast.walk(func_node):
            if not isinstance(node, _ast.Assign):
                continue
            # LHS must be `occurred_at`
            if len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not (isinstance(target, _ast.Name) and target.id == "occurred_at"):
                continue
            # RHS must be a Call to _non_empty
            rhs = node.value
            if not isinstance(rhs, _ast.Call):
                continue
            func = rhs.func
            if isinstance(func, _ast.Name) and func.id == "_non_empty":
                return True
        return False

    found_builders: set[str] = set()
    violations: list[str] = []

    for node in _ast.walk(tree):
        if not isinstance(node, _ast.FunctionDef):
            continue
        if node.name not in F2_BUILDER_NAMES:
            continue
        found_builders.add(node.name)
        if _has_non_empty_occurred_at(node):
            violations.append(node.name)

    # Assert all three builders were actually found (protects against name drift).
    missing = F2_BUILDER_NAMES - found_builders
    assert not missing, (
        f"F2 builders not found in lifecycle_events.py: {sorted(missing)}. "
        f"If a builder was renamed, update F2_BUILDER_NAMES in this test."
    )

    assert not violations, (
        f"F2 violation: the following builders still assign "
        f"occurred_at = _non_empty(...) instead of using an explicit "
        f"event_observed_at parameter: {sorted(violations)}. "
        f"See docs/findings_2026_05_28.md §F2."
    )
