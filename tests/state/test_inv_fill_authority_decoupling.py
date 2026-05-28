# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/plans/2026-05-27-chain-local-refactor-part2-findings.md (Finding D0, PR D0)
"""Relationship invariants: fill_authority decouples from entry_fill_verified as rescue authority.

Part-3 audit Finding D0 / PR D0: the rescue branch in chain_reconciliation.py
previously set entry_fill_verified=True and order_status="filled" for ALL
rescued positions, including balance-only (fill_authority=venue_position_observed)
ones. This test pins the new invariants:

1. Balance-only rescued position (FILL_AUTHORITY_VENUE_POSITION_OBSERVED):
   - has_tradable_exposure() == True   (EXPOSURE gates must still manage it)
   - has_verified_trade_fill() == False  (no linked venue trade fact)
   - entry_fill_verified stays False   (must NOT be set True by rescue)

2. Trade-verified rescued position (FILL_AUTHORITY_VENUE_CONFIRMED_FULL):
   - has_tradable_exposure() == True
   - has_verified_trade_fill() == True

3. is_training_eligible_position() remains False for balance-only.

4. Helper-level EXPOSURE contract: has_tradable_exposure() is True for all
   authorities that should be managed by exit/riskguard gates, ensuring any
   future EXPOSURE gate consumer picks up balance-only positions.

These tests are RED on current code (before the rescue branch fix in
chain_reconciliation.py:935 and before the helpers exist in portfolio.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest


@dataclass
class _PosStub:
    fill_authority: str = ""
    entry_fill_verified: bool = False
    order_status: str = "pending"


# ---------------------------------------------------------------------------
# STEP 2 tests — helpers exist and return correct values
# ---------------------------------------------------------------------------


def test_has_verified_trade_fill_importable() -> None:
    """has_verified_trade_fill must be importable from portfolio."""
    from src.state.portfolio import has_verified_trade_fill  # noqa: F401


def test_has_tradable_exposure_importable() -> None:
    """has_tradable_exposure must be importable from portfolio."""
    from src.state.portfolio import has_tradable_exposure  # noqa: F401


def test_has_verified_trade_fill_true_for_fill_grade_authorities() -> None:
    """All FILL_GRADE_FILL_AUTHORITIES should return True from has_verified_trade_fill."""
    from src.state.portfolio import FILL_GRADE_FILL_AUTHORITIES, has_verified_trade_fill

    for authority in sorted(FILL_GRADE_FILL_AUTHORITIES):
        pos = _PosStub(fill_authority=authority)
        assert has_verified_trade_fill(pos) is True, (
            f"has_verified_trade_fill must be True for authority={authority!r}"
        )


def test_has_verified_trade_fill_false_for_balance_only() -> None:
    """Balance-only (venue_position_observed) has NO verified trade fill."""
    from src.state.portfolio import (
        FILL_AUTHORITY_VENUE_POSITION_OBSERVED,
        has_verified_trade_fill,
    )

    pos = _PosStub(fill_authority=FILL_AUTHORITY_VENUE_POSITION_OBSERVED)
    assert has_verified_trade_fill(pos) is False


def test_has_verified_trade_fill_false_for_none_authority() -> None:
    from src.state.portfolio import FILL_AUTHORITY_NONE, has_verified_trade_fill

    pos = _PosStub(fill_authority=FILL_AUTHORITY_NONE)
    assert has_verified_trade_fill(pos) is False


def test_has_tradable_exposure_true_for_balance_only() -> None:
    """Balance-only position has tradable exposure — EXPOSURE gates must manage it."""
    from src.state.portfolio import (
        FILL_AUTHORITY_VENUE_POSITION_OBSERVED,
        has_tradable_exposure,
    )

    pos = _PosStub(fill_authority=FILL_AUTHORITY_VENUE_POSITION_OBSERVED)
    assert has_tradable_exposure(pos) is True, (
        "Balance-only rescued position must have has_tradable_exposure==True so "
        "riskguard/exit gates still manage it (Finding D0, PR D0)."
    )


def test_has_tradable_exposure_true_for_venue_confirmed_full() -> None:
    from src.state.portfolio import (
        FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        has_tradable_exposure,
    )

    pos = _PosStub(fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL)
    assert has_tradable_exposure(pos) is True


def test_has_tradable_exposure_false_for_none_authority() -> None:
    """Positions with no fill authority have no tradable exposure."""
    from src.state.portfolio import FILL_AUTHORITY_NONE, has_tradable_exposure

    pos = _PosStub(fill_authority=FILL_AUTHORITY_NONE)
    assert has_tradable_exposure(pos) is False


def test_has_tradable_exposure_false_for_optimistic_submitted() -> None:
    """Optimistic-submitted (no venue confirmation) has no tradable exposure."""
    from src.state.portfolio import (
        FILL_AUTHORITY_OPTIMISTIC_SUBMITTED,
        has_tradable_exposure,
    )

    pos = _PosStub(fill_authority=FILL_AUTHORITY_OPTIMISTIC_SUBMITTED)
    assert has_tradable_exposure(pos) is False


# ---------------------------------------------------------------------------
# STEP 3 tests — rescue branch sets entry_fill_verified correctly
# (These fail on pre-fix code where line 935 sets entry_fill_verified=True
#  unconditionally before the fill_authority discrimination.)
# ---------------------------------------------------------------------------


def test_rescue_branch_balance_only_entry_fill_verified_stays_false() -> None:
    """Balance-only rescue: entry_fill_verified must NOT be set True.

    Pre-fix: chain_reconciliation.py:935 sets entry_fill_verified=True before
    checking _pending_entry_has_linked_fill_fact. After fix, balance-only
    (else branch) must leave entry_fill_verified=False.

    This test verifies the rescue branch structure statically — the runtime
    path is covered by test_live_safety_invariants.py.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    src = (repo_root / "src" / "state" / "chain_reconciliation.py").read_text(encoding="utf-8")

    # After fix, entry_fill_verified=True must appear INSIDE the
    # _pending_entry_has_linked_fill_fact(pos) True branch, not before it.
    # We check that the True assignment is guarded by the linked-fill-fact check.
    #
    # Strategy: locate the rescue block. Verify entry_fill_verified=True is
    # NOT set unconditionally before the if _pending_entry_has_linked_fill_fact line.
    # The fix moves (or removes) the unconditional assignment to the True branch only.

    # Find the index of the unconditional assignment
    unconditional_idx = src.find("rescued.entry_fill_verified = True")
    linked_fill_check_idx = src.find("if _pending_entry_has_linked_fill_fact(pos):")

    assert unconditional_idx != -1 or linked_fill_check_idx != -1, (
        "Neither the unconditional entry_fill_verified assignment nor the "
        "_pending_entry_has_linked_fill_fact check found in chain_reconciliation.py"
    )

    # If unconditional assignment exists, it must appear AFTER the
    # _pending_entry_has_linked_fill_fact check (i.e., inside the True branch).
    if unconditional_idx != -1 and linked_fill_check_idx != -1:
        assert unconditional_idx > linked_fill_check_idx, (
            "entry_fill_verified=True must appear inside (after) the "
            "_pending_entry_has_linked_fill_fact(pos) guard, not before it. "
            "PRE-FIX: line 935 sets it unconditionally before line 945 check. "
            "POST-FIX: the assignment must be inside the True branch only."
        )


def test_rescue_branch_balance_only_order_status_not_filled() -> None:
    """Balance-only rescue: order_status must NOT be set to 'filled'.

    Pre-fix: chain_reconciliation.py:936 sets order_status='filled' before
    the fill_authority discrimination. After fix, balance-only (else branch)
    must NOT set order_status='filled'.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    src = (repo_root / "src" / "state" / "chain_reconciliation.py").read_text(encoding="utf-8")

    # Same logic: order_status="filled" assignment must appear after the
    # _pending_entry_has_linked_fill_fact check.
    unconditional_idx = src.find('rescued.order_status = "filled"')
    linked_fill_check_idx = src.find("if _pending_entry_has_linked_fill_fact(pos):")

    if unconditional_idx != -1 and linked_fill_check_idx != -1:
        assert unconditional_idx > linked_fill_check_idx, (
            'rescued.order_status = "filled" must appear inside (after) the '
            "_pending_entry_has_linked_fill_fact(pos) guard, not before it. "
            "PRE-FIX: line 936 sets it unconditionally before line 945 check."
        )


# ---------------------------------------------------------------------------
# Cross-signal relationship invariants
# ---------------------------------------------------------------------------


def test_balance_only_not_training_eligible_but_has_tradable_exposure() -> None:
    """Key relationship: balance-only is NOT training-eligible but IS tradable.

    This is the central semantic split introduced by PR D0: fill_authority
    discriminates two orthogonal properties:
      - has_tradable_exposure (riskguard/exit must manage it)  → True
      - is_training_eligible_position (learning/calibration)  → False
      - has_verified_trade_fill (fill-economics accuracy)      → False
    """
    from src.state.portfolio import (
        FILL_AUTHORITY_VENUE_POSITION_OBSERVED,
        has_tradable_exposure,
        has_verified_trade_fill,
        is_training_eligible_position,
    )

    pos = _PosStub(fill_authority=FILL_AUTHORITY_VENUE_POSITION_OBSERVED)
    assert has_tradable_exposure(pos) is True
    assert has_verified_trade_fill(pos) is False
    assert is_training_eligible_position(pos) is False


def test_trade_verified_position_has_all_signals_true() -> None:
    """Trade-verified (venue_confirmed_full) satisfies all three gates."""
    from src.state.portfolio import (
        FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        has_tradable_exposure,
        has_verified_trade_fill,
        is_training_eligible_position,
    )

    pos = _PosStub(fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL)
    assert has_tradable_exposure(pos) is True
    assert has_verified_trade_fill(pos) is True
    assert is_training_eligible_position(pos) is True
