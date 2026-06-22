# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 (Pro
#   Extended) — D2 shift-bin reactor wiring. Pins the ADDITIVE integration points in
#   src/engine/event_reactor_adapter.py:
#     - the close-before-open gate: a SIBLING-different-bin redecision with a live old
#       leg produces EXIT_OLD_LEG (lease EXIT_SUBMITTED) and NO new-bin entry; the old
#       leg must be proven zero/dust before a counter-entry is admitted (ENTER_NEW_BIN),
#     - entry-path + D1 fill-up byte-identity: for a fresh entry OR a same-token fill-up
#       the shift-bin orchestration is a complete no-op (read_held_sibling_exposure →
#       None → NOOP), so neither working path is altered,
#     - the OLD-leg closure proof: read_old_leg_residual_usd returns 0.0 once the old
#       leg leaves position_current (voided/closed), and +inf on ambiguous truth so the
#       caller never falsely enters.
"""Reactor-level integration for D2 shift-bin: close-before-open + path byte-identity."""
from __future__ import annotations

import sqlite3

import pytest

from src.engine import event_reactor_adapter as era
from src.state.schema.family_rebalance_intents_schema import ensure_table
from src.strategy import fill_up_wiring as fuw
from src.strategy import shift_bin_wiring as sbw


_POSITION_CURRENT_DDL = """
CREATE TABLE position_current (
    position_id TEXT, phase TEXT, token_id TEXT, no_token_id TEXT,
    bin_label TEXT, direction TEXT, condition_id TEXT, city TEXT,
    target_date TEXT, temperature_metric TEXT, p_posterior REAL,
    entry_ci_width REAL, cost_basis_usd REAL, chain_cost_basis_usd REAL,
    shares REAL, chain_shares REAL, size_usd REAL, updated_at TEXT
)
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_POSITION_CURRENT_DDL)
    ensure_table(conn)
    return conn


def _insert_held(conn, *, position_id="p-old", token_id="tok-A", phase="active",
                 bin_label="60-61F", direction="buy_yes", cost_basis_usd=4.0,
                 chain_cost_basis_usd=None, shares=10.0, city="Tokyo",
                 target_date="2026-06-23", metric="high"):
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, token_id, no_token_id, bin_label, direction,
            condition_id, city, target_date, temperature_metric, p_posterior,
            entry_ci_width, cost_basis_usd, chain_cost_basis_usd, shares, chain_shares,
            size_usd, updated_at
        ) VALUES (?, ?, ?, '', ?, ?, 'cond-1', ?, ?, ?, 0.50, 0.20, ?, ?, ?, ?, ?,
                  '2026-06-22T06:00:00')
        """,
        (position_id, phase, token_id, bin_label, direction, city, target_date,
         metric, cost_basis_usd, chain_cost_basis_usd, shares, shares, cost_basis_usd),
    )


# ---------------------------------------------------------------------------
# Closure proof: read_old_leg_residual_usd over the reactor's position_current view.
# ---------------------------------------------------------------------------
def test_old_leg_residual_live_then_zero_on_close():
    conn = _conn()
    _insert_held(conn, token_id="tok-A", cost_basis_usd=4.0)
    assert sbw.read_old_leg_residual_usd(conn, token_id="tok-A") == pytest.approx(4.0)
    # Old leg closed (row removed / voided) → proven zero.
    conn.execute("DELETE FROM position_current WHERE token_id='tok-A'")
    assert sbw.read_old_leg_residual_usd(conn, token_id="tok-A") == pytest.approx(0.0)


def test_old_leg_residual_ambiguous_truth_is_inf_never_enter():
    """A read against a connection with NO position_current returns +inf so the caller
    treats the old leg as STILL LIVE (exit first), never falsely enters."""
    bare = sqlite3.connect(":memory:")
    assert sbw.read_old_leg_residual_usd(bare, token_id="tok-A") == float("inf")


# ---------------------------------------------------------------------------
# THE HAZARD this feature fixes: a sibling redecision must NOT open the new bin while
# the old leg is live. Through the wiring this is EXIT_OLD_LEG, allow_entry False.
# ---------------------------------------------------------------------------
def test_sibling_live_old_leg_is_exit_old_leg_no_entry():
    conn = _conn()
    _insert_held(conn, position_id="p-old", token_id="tok-A", bin_label="60-61F", cost_basis_usd=4.0)
    held = sbw.read_held_sibling_exposure(
        conn, city="Tokyo", target_date="2026-06-23", temperature_metric="high",
        selected_token_id="tok-B", selected_bin_label="62-63F",
    )
    assert held is not None and held.token_id == "tok-A"
    plan = sbw.plan_shift_bin(
        conn, is_redecision_event=True, family_key="live|Tokyo|2026-06-23|high",
        event_id="e1", selected_token_id="tok-B", selected_bin_id="bin-B",
        selected_direction="buy_yes", held=held,
        old_leg_residual_usd=sbw.read_old_leg_residual_usd(conn, token_id="tok-A"),
        has_unowned_pending_or_unknown_entry=False, now_iso="t0",
        old_leg_dust_floor_usd=1.0,
    )
    assert plan.kind == "EXIT_OLD_LEG"
    assert plan.allow_entry is False  # NO new-bin entry while old leg live
    assert plan.old_token_id == "tok-A"


def test_sibling_after_old_leg_closed_admits_one_entry():
    """The multi-cycle close-before-open: once the old leg is gone from
    position_current (proven closed), the SAME family lease (released after EXIT only
    on a terminal status — here we model a fresh redecision) admits exactly the
    counter-entry."""
    conn = _conn()
    # Old leg already closed: no position_current row, but the sibling identity is
    # carried into a fresh redecision (the reactor recomputed selection on fresh books).
    held = sbw.HeldSiblingExposure(
        position_id="p-old", token_id="tok-A", bin_label="60-61F",
        direction="buy_yes", current_live_usd=0.0,
    )
    plan = sbw.plan_shift_bin(
        conn, is_redecision_event=True, family_key="live|Tokyo|2026-06-23|high",
        event_id="e2", selected_token_id="tok-B", selected_bin_id="bin-B",
        selected_direction="buy_yes", held=held,
        old_leg_residual_usd=sbw.read_old_leg_residual_usd(conn, token_id="tok-A"),  # 0.0
        has_unowned_pending_or_unknown_entry=False, now_iso="t1",
        old_leg_dust_floor_usd=1.0,
    )
    assert plan.kind == "ENTER_NEW_BIN"
    assert plan.allow_entry is True


# ---------------------------------------------------------------------------
# Path byte-identity: shift-bin is a NO-OP for a fresh entry AND a same-token fill-up.
# ---------------------------------------------------------------------------
def test_fresh_entry_no_family_position_shift_bin_noop():
    """No held family position → read_held_sibling_exposure None → plan NOOP, no lease,
    no order. The fresh-entry path is untouched."""
    conn = _conn()
    held = sbw.read_held_sibling_exposure(
        conn, city="Tokyo", target_date="2026-06-23", temperature_metric="high",
        selected_token_id="tok-FRESH", selected_bin_label="80-81F",
    )
    assert held is None
    plan = sbw.plan_shift_bin(
        conn, is_redecision_event=True, family_key="live|Tokyo|2026-06-23|high",
        event_id="e1", selected_token_id="tok-FRESH", selected_bin_id="bin-FRESH",
        selected_direction="buy_yes", held=held, old_leg_residual_usd=0.0,
        has_unowned_pending_or_unknown_entry=False, now_iso="t0",
    )
    assert plan.kind == "NOOP"
    assert conn.execute("SELECT COUNT(*) FROM family_rebalance_intents").fetchone()[0] == 0


def test_same_token_fillup_is_not_a_sibling_shift_noop():
    """A same-token held position (the D1 fill-up case) is NOT a sibling →
    read_held_sibling_exposure returns None → shift-bin NOOP, leaving D1 fill-up to own
    it. The two D-paths never both fire."""
    conn = _conn()
    _insert_held(conn, token_id="tok-A", bin_label="60-61F", cost_basis_usd=4.0)
    # Same token selected → not a sibling.
    held = sbw.read_held_sibling_exposure(
        conn, city="Tokyo", target_date="2026-06-23", temperature_metric="high",
        selected_token_id="tok-A", selected_bin_label="60-61F",
    )
    assert held is None
    # And D1 still sees it as a same-token fill-up candidate.
    fillup_held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")
    assert fillup_held is not None


def test_blocking_unowned_exposure_aborts_no_exit_no_entry():
    conn = _conn()
    _insert_held(conn, position_id="p-old", token_id="tok-A", cost_basis_usd=4.0)
    held = sbw.read_held_sibling_exposure(
        conn, city="Tokyo", target_date="2026-06-23", temperature_metric="high",
        selected_token_id="tok-B", selected_bin_label="62-63F",
    )
    plan = sbw.plan_shift_bin(
        conn, is_redecision_event=True, family_key="live|Tokyo|2026-06-23|high",
        event_id="e1", selected_token_id="tok-B", selected_bin_id="bin-B",
        selected_direction="buy_yes", held=held, old_leg_residual_usd=4.0,
        has_unowned_pending_or_unknown_entry=True, now_iso="t0",
        old_leg_dust_floor_usd=1.0,
    )
    assert plan.kind == "ABORT"
    assert plan.allow_entry is False


def test_two_concurrent_shift_events_one_lease_one_exit():
    """Two EDLI shift events for one family → one lease (one exit), the other aborts."""
    conn = _conn()
    _insert_held(conn, position_id="p-old", token_id="tok-A", cost_basis_usd=4.0)
    held = sbw.read_held_sibling_exposure(
        conn, city="Tokyo", target_date="2026-06-23", temperature_metric="high",
        selected_token_id="tok-B", selected_bin_label="62-63F",
    )
    kw = dict(
        is_redecision_event=True, family_key="live|Tokyo|2026-06-23|high",
        event_id="e1", selected_token_id="tok-B", selected_bin_id="bin-B",
        selected_direction="buy_yes", held=held, old_leg_residual_usd=4.0,
        has_unowned_pending_or_unknown_entry=False, now_iso="t0",
        old_leg_dust_floor_usd=1.0,
    )
    first = sbw.plan_shift_bin(conn, **kw)
    second = sbw.plan_shift_bin(conn, **kw)
    kinds = sorted([first.kind, second.kind])
    assert kinds == ["ABORT", "EXIT_OLD_LEG"]
    active = conn.execute(
        "SELECT COUNT(*) FROM family_rebalance_intents WHERE status='EXIT_SUBMITTED'"
    ).fetchone()[0]
    assert active == 1
