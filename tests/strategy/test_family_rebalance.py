# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 (Pro
#   Extended) — D1 fill-up. The same-token "never increase exposure" invariant
#   (family_exclusive_dedup) guards the 2026-06-16 double-rest defect; the consult's
#   safe lift is a RESIDUAL resize only (delta = target - current_live - pending),
#   NEVER a second full entry, gated on belief strengthening + no unowned pending.
"""ANTIBODY: fill-up admits ONLY a residual top-up of an existing same-token held
position when belief strengthened — never a second full Kelly entry, never when a
sibling/unowned/pending command exists. The critical safety lift for D1."""
from __future__ import annotations

import pytest

from src.strategy.family_rebalance import decide_fill_up


def _base(**over):
    kw = dict(
        is_redecision_event=True,
        selected_token_id="tok-A", selected_bin_id="bin-A", selected_direction="buy_yes",
        held_token_id="tok-A", held_bin_id="bin-A", held_direction="buy_yes",
        q_current_lcb=0.40, q_entry_lcb=0.30,
        target_total_exposure_usd=10.0, current_live_exposure_usd=4.0,
        same_token_pending_entry_usd=0.0, venue_min_increment_usd=1.0,
        has_unowned_pending_or_unknown_entry=False,
    )
    kw.update(over)
    return kw


def test_fill_up_residual_only_never_full_target():
    """current<target, belief strengthened → allow ONLY the residual delta (6),
    never the full target (10) — the core anti-over-exposure property."""
    d = decide_fill_up(**_base(target_total_exposure_usd=10.0, current_live_exposure_usd=4.0))
    assert d.allow is True
    assert d.delta_entry_usd == pytest.approx(6.0)  # 10 - 4 - 0, NOT 10


def test_fill_up_subtracts_same_token_pending():
    """A live same-token pending entry counts against the residual (no double-stack)."""
    d = decide_fill_up(**_base(current_live_exposure_usd=4.0, same_token_pending_entry_usd=3.0))
    assert d.allow is True
    assert d.delta_entry_usd == pytest.approx(3.0)  # 10 - 4 - 3


def test_no_fill_up_when_already_at_target():
    d = decide_fill_up(**_base(current_live_exposure_usd=10.0))
    assert d.allow is False
    assert d.delta_entry_usd <= 0.0


def test_no_fill_up_when_over_target():
    d = decide_fill_up(**_base(current_live_exposure_usd=12.0))
    assert d.allow is False


def test_no_fill_up_when_residual_below_venue_min():
    d = decide_fill_up(**_base(current_live_exposure_usd=9.5, venue_min_increment_usd=1.0))
    assert d.allow is False  # residual 0.5 < 1.0


def test_no_fill_up_when_belief_not_strengthened():
    """q_current <= q_entry → not a strengthening, no add even if residual is positive."""
    d = decide_fill_up(**_base(q_current_lcb=0.30, q_entry_lcb=0.30))
    assert d.allow is False
    assert "STRENGTHEN" in d.reason.upper()


def test_no_fill_up_when_entry_q_lcb_missing():
    """v1: do not fill-up a held position lacking an entry q_lcb authority."""
    d = decide_fill_up(**_base(q_entry_lcb=None))
    assert d.allow is False


def test_sibling_token_is_not_fill_up():
    """A different bin/token in the family is SHIFT-BIN, not fill-up — deny here."""
    d = decide_fill_up(**_base(selected_token_id="tok-B", selected_bin_id="bin-B"))
    assert d.allow is False
    assert "SAME_TOKEN" in d.reason.upper() or "SIBLING" in d.reason.upper()


def test_no_fill_up_without_held_exposure():
    """No held position → this is a fresh entry, not a fill-up."""
    d = decide_fill_up(**_base(held_token_id=None, held_bin_id=None, held_direction=None))
    assert d.allow is False


def test_unowned_pending_or_unknown_entry_blocks_fill_up():
    """Fail closed: any unowned pending/unknown ENTRY command in the family blocks."""
    d = decide_fill_up(**_base(has_unowned_pending_or_unknown_entry=True))
    assert d.allow is False
    assert "PENDING" in d.reason.upper() or "UNKNOWN" in d.reason.upper()


def test_non_redecision_event_denied():
    d = decide_fill_up(**_base(is_redecision_event=False))
    assert d.allow is False


def test_q_strengthening_floor_hysteresis():
    """With a hysteresis floor, a marginal strengthening below the floor is denied."""
    d = decide_fill_up(**_base(q_current_lcb=0.31, q_entry_lcb=0.30, q_strengthening_floor=0.03))
    assert d.allow is False  # 0.31 - 0.30 = 0.01 < 0.03 floor
    d2 = decide_fill_up(**_base(q_current_lcb=0.34, q_entry_lcb=0.30, q_strengthening_floor=0.03))
    assert d2.allow is True  # 0.04 >= 0.03


# --- lease manager (concurrency guard) -------------------------------------

def _lease_conn():
    import sqlite3
    from src.state.schema.family_rebalance_intents_schema import ensure_table
    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    return conn


def test_lease_acquire_then_second_concurrent_acquire_is_none():
    """The core concurrency guard: one active lease per family. A second acquire on
    the same family while the first is active returns None (no second order)."""
    from src.strategy.family_rebalance import acquire_rebalance_lease

    conn = _lease_conn()
    first = acquire_rebalance_lease(
        conn, family_key="live|Tokyo|2026-06-23|high", operation="FILL_UP",
        now_iso="2026-06-22T06:40:00", held_position_id="p1",
    )
    assert first is not None
    second = acquire_rebalance_lease(
        conn, family_key="live|Tokyo|2026-06-23|high", operation="FILL_UP",
        now_iso="2026-06-22T06:40:01", held_position_id="p1",
    )
    assert second is None  # family already leased — fail closed


def test_lease_release_allows_next_acquire():
    from src.strategy.family_rebalance import acquire_rebalance_lease, advance_rebalance_lease

    conn = _lease_conn()
    fk = "live|Milan|2026-06-23|high"
    first = acquire_rebalance_lease(conn, family_key=fk, operation="FILL_UP", now_iso="t0")
    advance_rebalance_lease(conn, first, status="COMPLETE", now_iso="t1")
    second = acquire_rebalance_lease(conn, family_key=fk, operation="FILL_UP", now_iso="t2")
    assert second is not None  # released — next rebalance may acquire


def test_lease_intermediate_status_keeps_family_locked():
    from src.strategy.family_rebalance import acquire_rebalance_lease, advance_rebalance_lease

    conn = _lease_conn()
    fk = "live|Dallas|2026-06-23|high"
    first = acquire_rebalance_lease(conn, family_key=fk, operation="SHIFT_BIN", now_iso="t0")
    advance_rebalance_lease(conn, first, status="EXIT_SUBMITTED", now_iso="t1")  # still active
    second = acquire_rebalance_lease(conn, family_key=fk, operation="SHIFT_BIN", now_iso="t2")
    assert second is None  # mid-flight rebalance still holds the family


def test_lease_distinct_families_independent():
    from src.strategy.family_rebalance import acquire_rebalance_lease

    conn = _lease_conn()
    a = acquire_rebalance_lease(conn, family_key="live|Tokyo|2026-06-23|high", operation="FILL_UP", now_iso="t0")
    b = acquire_rebalance_lease(conn, family_key="live|Seoul|2026-06-23|high", operation="FILL_UP", now_iso="t0")
    assert a is not None and b is not None and a != b
