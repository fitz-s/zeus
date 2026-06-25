# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 (Pro
#   Extended) — D2 shift-bin wiring. The additive orchestration that connects the
#   committed primitives (decide_shift_bin + the family-rebalance lease, operation=
#   SHIFT_BIN) to the live money path WITHOUT altering the fresh-entry OR the D1
#   fill-up path. The load-bearing safety property: NO new-bin entry while the old
#   leg has live/partial/unknown exposure; the lease carries the close-before-open
#   state across reactor cycles (EXIT_SUBMITTED on cycle N → counter-entry on a later
#   cycle once the old residual is proven zero/dust).
"""TDD for src/strategy/shift_bin_wiring.

  - ``read_held_sibling_exposure`` — find a held position in the SAME family but a
    DIFFERENT bin/token than the fresh selection (the OLD leg to close). None when
    the fresh selection is same-token (fill-up) or no family position is held.
  - ``read_old_leg_residual_usd`` — current live committed USD of a specific OLD
    token from canonical position_current (chain cost basis preferred). 0.0 when the
    old leg is no longer held (proven closed).
  - ``plan_shift_bin`` — acquire the SHIFT_BIN lease + run decide_shift_bin. Returns a
    typed plan: EXIT_OLD_LEG (lease in EXIT_SUBMITTED, no entry), ENTER_NEW_BIN (old
    leg proven closed, admit the counter-entry under the SAME lease), ABORT (blocking
    exposure / concurrent lease — no exit, no entry), NOOP (not a shift-bin).
"""
from __future__ import annotations

import sqlite3

import pytest

from src.state.schema.family_rebalance_intents_schema import ensure_table
from src.strategy import shift_bin_wiring as sbw


_POSITION_CURRENT_DDL = """
CREATE TABLE position_current (
    position_id TEXT, phase TEXT, token_id TEXT, no_token_id TEXT,
    bin_label TEXT, direction TEXT, condition_id TEXT, city TEXT,
    target_date TEXT, temperature_metric TEXT, p_posterior REAL,
    entry_ci_width REAL, cost_basis_usd REAL, chain_cost_basis_usd REAL,
    shares REAL, size_usd REAL, updated_at TEXT
)
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_POSITION_CURRENT_DDL)
    ensure_table(conn)
    return conn


def _insert_held(
    conn,
    *,
    position_id="p-old",
    token_id="tok-A",
    no_token_id="",
    phase="active",
    bin_label="60-61F",
    direction="buy_yes",
    cost_basis_usd=4.0,
    chain_cost_basis_usd=None,
    city="Tokyo",
    target_date="2026-06-23",
    metric="high",
):
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, token_id, no_token_id, bin_label, direction,
            condition_id, city, target_date, temperature_metric, p_posterior,
            entry_ci_width, cost_basis_usd, chain_cost_basis_usd, shares, size_usd, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'cond-1', ?, ?, ?, 0.50, 0.20, ?, ?, 10.0, ?,
                  '2026-06-22T06:00:00')
        """,
        (position_id, phase, token_id, no_token_id, bin_label, direction, city, target_date,
         metric, cost_basis_usd, chain_cost_basis_usd, cost_basis_usd),
    )


# ---------------------------------------------------------------------------
# read_held_sibling_exposure
# ---------------------------------------------------------------------------
def test_no_family_position_returns_none():
    conn = _conn()
    held = sbw.read_held_sibling_exposure(
        conn, city="Tokyo", target_date="2026-06-23", temperature_metric="high",
        selected_token_id="tok-B", selected_bin_label="62-63F",
    )
    assert held is None


def test_sibling_different_bin_is_returned():
    """A held position in the same family but a DIFFERENT bin/token is the OLD leg."""
    conn = _conn()
    _insert_held(conn, token_id="tok-A", bin_label="60-61F", cost_basis_usd=4.0)
    held = sbw.read_held_sibling_exposure(
        conn, city="Tokyo", target_date="2026-06-23", temperature_metric="high",
        selected_token_id="tok-B", selected_bin_label="62-63F",
    )
    assert held is not None
    assert held.position_id == "p-old"
    assert held.token_id == "tok-A"
    assert held.bin_label == "60-61F"
    assert held.current_live_usd == pytest.approx(4.0)


def test_buy_no_sibling_returns_no_token_as_sellable_old_leg():
    """SHIFT_BIN must sell the held-side NO token, not the row's YES/condition token."""
    conn = _conn()
    _insert_held(
        conn,
        token_id="yes-tok-A",
        no_token_id="no-tok-A",
        bin_label="60-61F",
        direction="buy_no",
        cost_basis_usd=4.0,
    )
    held = sbw.read_held_sibling_exposure(
        conn, city="Tokyo", target_date="2026-06-23", temperature_metric="high",
        selected_token_id="no-tok-B", selected_bin_label="62-63F",
    )
    assert held is not None
    assert held.token_id == "no-tok-A"
    assert held.direction == "buy_no"
    assert held.current_live_usd == pytest.approx(4.0)


def test_same_token_held_is_not_a_sibling():
    """Selected token == held token is FILL-UP, not a sibling shift → None."""
    conn = _conn()
    _insert_held(conn, token_id="tok-A", bin_label="60-61F", cost_basis_usd=4.0)
    held = sbw.read_held_sibling_exposure(
        conn, city="Tokyo", target_date="2026-06-23", temperature_metric="high",
        selected_token_id="tok-A", selected_bin_label="60-61F",
    )
    assert held is None


def test_other_family_position_is_not_a_sibling():
    conn = _conn()
    _insert_held(conn, token_id="tok-A", city="Seoul")
    held = sbw.read_held_sibling_exposure(
        conn, city="Tokyo", target_date="2026-06-23", temperature_metric="high",
        selected_token_id="tok-B", selected_bin_label="62-63F",
    )
    assert held is None


# ---------------------------------------------------------------------------
# read_old_leg_residual_usd — closure proof from canonical truth.
# ---------------------------------------------------------------------------
def test_old_leg_residual_reads_live_usd():
    conn = _conn()
    _insert_held(conn, token_id="tok-A", cost_basis_usd=4.0)
    assert sbw.read_old_leg_residual_usd(conn, token_id="tok-A") == pytest.approx(4.0)


def test_old_leg_residual_prefers_chain_cost_basis():
    conn = _conn()
    _insert_held(conn, token_id="tok-A", cost_basis_usd=4.0, chain_cost_basis_usd=7.0)
    assert sbw.read_old_leg_residual_usd(conn, token_id="tok-A") == pytest.approx(7.0)


def test_old_leg_residual_reads_buy_no_no_token():
    conn = _conn()
    _insert_held(
        conn,
        token_id="yes-tok-A",
        no_token_id="no-tok-A",
        direction="buy_no",
        cost_basis_usd=4.0,
        chain_cost_basis_usd=7.0,
    )
    assert sbw.read_old_leg_residual_usd(conn, token_id="no-tok-A") == pytest.approx(7.0)


def test_old_leg_residual_zero_when_not_held():
    """Old leg no longer present in position_current (voided / closed) → 0.0 (proven
    closed)."""
    conn = _conn()
    assert sbw.read_old_leg_residual_usd(conn, token_id="tok-GONE") == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# plan_shift_bin — lease acquire + decide_shift_bin orchestration.
# ---------------------------------------------------------------------------
def _plan(conn, *, held, **over):
    kw = dict(
        conn=conn,
        is_redecision_event=True,
        family_key="live|Tokyo|2026-06-23|high",
        event_id="evt-1",
        selected_token_id="tok-B",
        selected_bin_id="bin-B",
        selected_direction="buy_yes",
        held=held,
        old_leg_residual_usd=4.0,
        has_unowned_pending_or_unknown_entry=False,
        old_leg_dust_floor_usd=1.0,
        now_iso="2026-06-22T06:40:00",
    )
    kw.update(over)
    return sbw.plan_shift_bin(**kw)


def _old_leg(conn):
    return sbw.read_held_sibling_exposure(
        conn, city="Tokyo", target_date="2026-06-23", temperature_metric="high",
        selected_token_id="tok-B", selected_bin_label="62-63F",
    )


def test_plan_no_sibling_is_noop_no_lease():
    conn = _conn()
    plan = _plan(conn, held=None)
    assert plan.kind == "NOOP"
    assert plan.lease_intent_id is None
    assert conn.execute("SELECT COUNT(*) FROM family_rebalance_intents").fetchone()[0] == 0


def test_plan_live_old_leg_exits_first_lease_in_exit_submitted():
    """Sibling held with live old leg → EXIT_OLD_LEG; lease acquired in SHIFT_BIN op;
    NO counter-entry admitted; old leg identity recorded on the lease."""
    conn = _conn()
    _insert_held(conn, position_id="p-old", token_id="tok-A", bin_label="60-61F", cost_basis_usd=4.0)
    held = _old_leg(conn)
    plan = _plan(conn, held=held, old_leg_residual_usd=4.0)
    assert plan.kind == "EXIT_OLD_LEG"
    assert plan.allow_entry is False
    assert plan.lease_intent_id is not None
    assert plan.old_position_id == "p-old"
    assert plan.old_token_id == "tok-A"
    row = conn.execute(
        "SELECT operation, status, held_position_id, held_token_id FROM family_rebalance_intents WHERE intent_id=?",
        (plan.lease_intent_id,),
    ).fetchone()
    assert row["operation"] == "SHIFT_BIN"
    assert row["status"] == "EXIT_SUBMITTED"
    assert row["held_position_id"] == "p-old"
    assert row["held_token_id"] == "tok-A"


def test_plan_buy_no_live_old_leg_records_sellable_no_token():
    conn = _conn()
    _insert_held(
        conn,
        position_id="p-old",
        token_id="yes-tok-A",
        no_token_id="no-tok-A",
        bin_label="60-61F",
        direction="buy_no",
        cost_basis_usd=4.0,
    )
    held = sbw.read_held_sibling_exposure(
        conn, city="Tokyo", target_date="2026-06-23", temperature_metric="high",
        selected_token_id="no-tok-B", selected_bin_label="62-63F",
    )
    plan = _plan(conn, held=held, selected_token_id="no-tok-B", old_leg_residual_usd=4.0)
    assert plan.kind == "EXIT_OLD_LEG"
    assert plan.old_token_id == "no-tok-A"
    row = conn.execute(
        "SELECT held_token_id FROM family_rebalance_intents WHERE intent_id=?",
        (plan.lease_intent_id,),
    ).fetchone()
    assert row["held_token_id"] == "no-tok-A"


def test_plan_blocking_exposure_aborts_no_exit_no_entry():
    conn = _conn()
    _insert_held(conn, position_id="p-old", token_id="tok-A", cost_basis_usd=4.0)
    held = _old_leg(conn)
    plan = _plan(conn, held=held, has_unowned_pending_or_unknown_entry=True)
    assert plan.kind == "ABORT"
    assert plan.allow_entry is False
    rows = conn.execute("SELECT status, abort_reason FROM family_rebalance_intents").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "ABORTED"
    assert "BLOCK" in (rows[0]["abort_reason"] or "").upper()


def test_plan_old_leg_closed_admits_entry_under_lease():
    """Old leg proven closed (residual 0) → ENTER_NEW_BIN, lease ENTRY_SUBMITTED."""
    conn = _conn()
    # No old-leg row in position_current (closed) but the sibling identity is carried.
    held = sbw.HeldSiblingExposure(
        position_id="p-old", token_id="tok-A", bin_label="60-61F",
        direction="buy_yes", current_live_usd=0.0,
    )
    plan = _plan(conn, held=held, old_leg_residual_usd=0.0)
    assert plan.kind == "ENTER_NEW_BIN"
    assert plan.allow_entry is True
    assert plan.lease_intent_id is not None
    row = conn.execute(
        "SELECT operation, status FROM family_rebalance_intents WHERE intent_id=?",
        (plan.lease_intent_id,),
    ).fetchone()
    assert row["operation"] == "SHIFT_BIN"
    assert row["status"] == "ENTRY_SUBMITTED"


def test_plan_concurrent_lease_collision_aborts_no_second_order():
    """Two EDLI shift events for one family: the first acquires the lease; the second
    finds it leased (acquire returns None) and ABORTS — no second exit, no order."""
    conn = _conn()
    _insert_held(conn, position_id="p-old", token_id="tok-A", cost_basis_usd=4.0)
    held = _old_leg(conn)
    first = _plan(conn, held=held)
    second = _plan(conn, held=held)
    kinds = sorted([first.kind, second.kind])
    assert kinds == ["ABORT", "EXIT_OLD_LEG"]
    aborted = first if first.kind == "ABORT" else second
    assert aborted.lease_intent_id is None  # never acquired a second lease
    active = conn.execute(
        "SELECT COUNT(*) FROM family_rebalance_intents WHERE status='EXIT_SUBMITTED'"
    ).fetchone()[0]
    assert active == 1


def test_plan_non_redecision_is_noop():
    conn = _conn()
    _insert_held(conn, position_id="p-old", token_id="tok-A", cost_basis_usd=4.0)
    held = _old_leg(conn)
    plan = _plan(conn, held=held, is_redecision_event=False)
    assert plan.kind == "NOOP"
    assert conn.execute("SELECT COUNT(*) FROM family_rebalance_intents").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Lease terminal / intermediate advances.
# ---------------------------------------------------------------------------
def test_record_exit_command_advances_lease():
    conn = _conn()
    _insert_held(conn, position_id="p-old", token_id="tok-A", cost_basis_usd=4.0)
    held = _old_leg(conn)
    plan = _plan(conn, held=held)
    sbw.record_exit_submitted(conn, plan.lease_intent_id, now_iso="t1",
                              old_exit_command_id="exit-cmd-1", status="EXIT_SUBMITTED")
    row = conn.execute(
        "SELECT status, old_exit_command_id FROM family_rebalance_intents WHERE intent_id=?",
        (plan.lease_intent_id,),
    ).fetchone()
    assert row["status"] == "EXIT_SUBMITTED"
    assert row["old_exit_command_id"] == "exit-cmd-1"


def test_complete_shift_bin_lease_on_entry_ack():
    conn = _conn()
    held = sbw.HeldSiblingExposure(
        position_id="p-old", token_id="tok-A", bin_label="60-61F",
        direction="buy_yes", current_live_usd=0.0,
    )
    plan = _plan(conn, held=held, old_leg_residual_usd=0.0)
    assert plan.kind == "ENTER_NEW_BIN"
    sbw.complete_shift_bin_lease(conn, plan.lease_intent_id, now_iso="t2",
                                 new_entry_command_id="entry-cmd-1")
    row = conn.execute(
        "SELECT status, new_entry_command_id FROM family_rebalance_intents WHERE intent_id=?",
        (plan.lease_intent_id,),
    ).fetchone()
    assert row["status"] == "COMPLETE"
    assert row["new_entry_command_id"] == "entry-cmd-1"


def test_exit_only_complete_when_fresh_no_longer_passes():
    """Old leg closed but the fresh recompute no longer selects the sibling → end the
    rebalance EXIT_ONLY_COMPLETE (NOT a false exit; the exit was independently
    justified). No counter-entry."""
    conn = _conn()
    _insert_held(conn, position_id="p-old", token_id="tok-A", cost_basis_usd=4.0)
    held = _old_leg(conn)
    plan = _plan(conn, held=held)
    sbw.exit_only_complete(conn, plan.lease_intent_id, now_iso="t3",
                           reason="FRESH_RECOMPUTE_NO_CANDIDATE")
    row = conn.execute(
        "SELECT status, abort_reason FROM family_rebalance_intents WHERE intent_id=?",
        (plan.lease_intent_id,),
    ).fetchone()
    assert row["status"] == "EXIT_ONLY_COMPLETE"


def test_abort_shift_bin_lease_releases_family():
    conn = _conn()
    _insert_held(conn, position_id="p-old", token_id="tok-A", cost_basis_usd=4.0)
    held = _old_leg(conn)
    plan = _plan(conn, held=held)
    sbw.abort_shift_bin_lease(conn, plan.lease_intent_id, now_iso="t4",
                              reason="EXIT_UNKNOWN_SIDE_EFFECT")
    row = conn.execute(
        "SELECT status, abort_reason FROM family_rebalance_intents WHERE intent_id=?",
        (plan.lease_intent_id,),
    ).fetchone()
    assert row["status"] == "ABORTED"
    assert row["abort_reason"] == "EXIT_UNKNOWN_SIDE_EFFECT"
