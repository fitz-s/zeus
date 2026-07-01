# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 (Pro
#   Extended) — D1 fill-up wiring. These tests cover the additive orchestration
#   that connects the committed safety primitives (decide_fill_up + the family-
#   rebalance lease) to the live money path WITHOUT altering the fresh-entry path.
#   The single load-bearing safety wire is the RESIDUAL stake override: an approved
#   fill-up emits exactly `delta_entry_usd` (target - current_live - pending), never
#   a second full Kelly stake; every non-fill-up case is a complete no-op (the
#   entry path is byte-identical).
"""TDD for src/strategy/fill_up_wiring.

The wiring exposes pure, explicitly-parameterized helpers so the live decision body
calls them in ONE fully-gated block:

  - ``read_held_same_token_exposure`` — reads the held same-token position truth
    (position_id, bin/direction, entry q_lcb, current_live_usd) from
    ``position_current``. Returns None for a fresh-entry candidate (no held same
    token) so the caller leaves the entry path untouched.
  - ``plan_fill_up`` — orchestrates ``decide_fill_up`` + the family-rebalance lease
    acquire. Returns a typed plan: APPLY (residual stake + lease id), NOOP (no held
    same token = fresh entry, no lease taken), or ABORT (held same token but the
    predicate denied / a concurrent lease holds the family — lease advanced ABORTED
    or never acquired; emit NO order).
  - ``presubmit_reread_aborts`` — the final pre-submit gate: re-reads family exposure
    and returns an abort reason when a new unowned/unknown same-family entry appeared
    between admission and submit.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.state.schema.family_rebalance_intents_schema import ensure_table
from src.strategy import fill_up_wiring as fuw


# ---------------------------------------------------------------------------
# Fixtures: an in-memory position_current + the lease table (world.db schema).
# ---------------------------------------------------------------------------
_POSITION_CURRENT_DDL = """
CREATE TABLE position_current (
    position_id TEXT,
    phase TEXT,
    token_id TEXT,
    no_token_id TEXT,
    bin_label TEXT,
    direction TEXT,
    condition_id TEXT,
    city TEXT,
    target_date TEXT,
    temperature_metric TEXT,
    p_posterior REAL,
    entry_ci_width REAL,
    cost_basis_usd REAL,
    chain_cost_basis_usd REAL,
    chain_shares REAL,
    size_usd REAL,
    updated_at TEXT
)
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_POSITION_CURRENT_DDL)
    ensure_table(conn)  # family_rebalance_intents (lease table)
    return conn


def _insert_held(
    conn: sqlite3.Connection,
    *,
    position_id="p1",
    phase="active",
    token_id="tok-A",
    bin_label="60-61F",
    direction="buy_yes",
    p_posterior=0.50,
    entry_ci_width=0.20,  # entry q_lcb = 0.50 - 0.10 = 0.40
    cost_basis_usd=4.0,
    chain_cost_basis_usd=None,
    chain_shares=10.0,
):
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, token_id, no_token_id, bin_label, direction,
            condition_id, city, target_date, temperature_metric,
            p_posterior, entry_ci_width, cost_basis_usd, chain_cost_basis_usd,
            chain_shares, size_usd, updated_at
        ) VALUES (?, ?, ?, '', ?, ?, 'cond-1', 'Tokyo', '2026-06-23', 'high',
                  ?, ?, ?, ?, ?, ?, '2026-06-22T06:00:00')
        """,
        (position_id, phase, token_id, bin_label, direction,
         p_posterior, entry_ci_width, cost_basis_usd, chain_cost_basis_usd,
         chain_shares, cost_basis_usd),
    )


# ---------------------------------------------------------------------------
# read_held_same_token_exposure
# ---------------------------------------------------------------------------
def test_no_held_same_token_returns_none_fresh_entry_path():
    """A fresh-entry candidate (no held same-token position) returns None — the
    caller MUST then leave the entry path completely untouched."""
    conn = _conn()
    assert fuw.read_held_same_token_exposure(conn, token_id="tok-FRESH") is None


def test_held_same_token_reads_entry_q_lcb_and_current_live():
    conn = _conn()
    _insert_held(conn, p_posterior=0.50, entry_ci_width=0.20, cost_basis_usd=4.0)
    held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")
    assert held is not None
    assert held.position_id == "p1"
    assert held.bin_label == "60-61F"
    assert held.direction == "buy_yes"
    assert held.entry_q_lcb == pytest.approx(0.40)  # 0.50 - 0.20/2
    assert held.current_live_usd == pytest.approx(4.0)


def test_held_same_token_reads_tuple_rows_without_row_factory():
    """Maintenance/recovery callers may pass a bare sqlite3 connection."""
    conn = sqlite3.connect(":memory:")
    conn.execute(_POSITION_CURRENT_DDL)
    ensure_table(conn)
    _insert_held(conn, p_posterior=0.50, entry_ci_width=0.20, cost_basis_usd=4.0)

    held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")

    assert held is not None
    assert held.position_id == "p1"
    assert held.bin_label == "60-61F"
    assert held.direction == "buy_yes"
    assert held.entry_q_lcb == pytest.approx(0.40)
    assert held.current_live_usd == pytest.approx(4.0)


def test_held_same_token_prefers_chain_cost_basis_when_present():
    conn = _conn()
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, token_id, no_token_id, bin_label, direction,
            condition_id, city, target_date, temperature_metric,
            p_posterior, entry_ci_width, cost_basis_usd, chain_cost_basis_usd,
            chain_shares, size_usd, updated_at
        ) VALUES ('p2', 'active', 'tok-A', '', '60-61F', 'buy_yes',
                  'cond-1', 'Tokyo', '2026-06-23', 'high',
                  0.50, 0.20, 4.0, 7.0, 10.0, 4.0, '2026-06-22T06:00:00')
        """
    )
    held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")
    assert held is not None
    # chain truth (7.0) is the authoritative committed exposure when present.
    assert held.current_live_usd == pytest.approx(7.0)


def test_held_same_token_reads_quarantined_chain_backed_position():
    """A chain-backed quarantine row is still a held token for fill-up planning."""
    conn = _conn()
    _insert_held(
        conn,
        phase="quarantined",
        token_id="tok-A",
        p_posterior=0.60,
        entry_ci_width=0.10,
        cost_basis_usd=4.0,
        chain_cost_basis_usd=7.0,
        chain_shares=11.0,
    )

    held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")

    assert held is not None
    assert held.position_id == "p1"
    assert held.entry_q_lcb == pytest.approx(0.55)
    assert held.current_live_usd == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# plan_fill_up — the orchestration: decide_fill_up + lease acquire.
# ---------------------------------------------------------------------------
def _plan(conn, *, held, **over):
    kw = dict(
        conn=conn,
        is_redecision_event=True,
        family_key="live|Tokyo|2026-06-23|high",
        event_id="evt-1",
        selected_token_id="tok-A",
        selected_bin_id="60-61F",
        selected_direction="buy_yes",
        held=held,
        q_current_lcb=0.55,
        target_total_exposure_usd=10.0,
        same_token_pending_entry_usd=0.0,
        venue_min_increment_usd=1.0,
        has_unowned_pending_or_unknown_entry=False,
        now_iso="2026-06-22T06:40:00",
        q_strengthening_floor=0.0,
    )
    kw.update(over)
    return fuw.plan_fill_up(**kw)


def test_plan_fresh_entry_is_noop_no_lease():
    """No held same-token exposure => NOOP, no lease acquired, caller untouched."""
    conn = _conn()
    plan = _plan(conn, held=None)
    assert plan.kind == "NOOP"
    assert plan.residual_stake_usd is None
    assert plan.lease_intent_id is None
    # No lease row was written.
    assert conn.execute("SELECT COUNT(*) FROM family_rebalance_intents").fetchone()[0] == 0


def test_plan_approved_fill_up_returns_residual_and_acquires_lease():
    """Held same token, belief strengthened, current<target => APPLY the residual
    delta (10 - 4 - 0 = 6), never the full target; a lease is acquired."""
    conn = _conn()
    _insert_held(conn, cost_basis_usd=4.0)
    held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")
    plan = _plan(conn, held=held, target_total_exposure_usd=10.0)
    assert plan.kind == "APPLY"
    assert plan.residual_stake_usd == pytest.approx(6.0)
    assert plan.lease_intent_id is not None
    row = conn.execute(
        "SELECT status, operation, delta_entry_usd FROM family_rebalance_intents WHERE intent_id=?",
        (plan.lease_intent_id,),
    ).fetchone()
    assert row["status"] in ("PLANNED", "ENTRY_SUBMITTED")
    assert row["operation"] == "FILL_UP"
    assert row["delta_entry_usd"] == pytest.approx(6.0)


def test_plan_denied_at_target_aborts_lease_no_order():
    """Held same token but already AT target => decide_fill_up denies; the lease is
    advanced ABORTED and no residual stake is returned (emit NO order)."""
    conn = _conn()
    _insert_held(conn, cost_basis_usd=10.0)  # current == target
    held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")
    plan = _plan(conn, held=held, target_total_exposure_usd=10.0)
    assert plan.kind == "ABORT"
    assert plan.residual_stake_usd is None
    # A lease row exists and was advanced to ABORTED with the predicate reason.
    rows = conn.execute(
        "SELECT status, abort_reason FROM family_rebalance_intents"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "ABORTED"
    assert rows[0]["abort_reason"]  # carries the decide_fill_up deny reason


def test_plan_denied_belief_not_strengthened_aborts():
    conn = _conn()
    _insert_held(conn, p_posterior=0.50, entry_ci_width=0.20)  # entry q_lcb 0.40
    held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")
    plan = _plan(conn, held=held, q_current_lcb=0.40)  # not strengthened
    assert plan.kind == "ABORT"
    assert plan.residual_stake_usd is None


def test_plan_denied_unowned_pending_aborts():
    """Fail closed on the double-submit hazard: an unowned pending/unknown family
    entry blocks the fill-up entirely."""
    conn = _conn()
    _insert_held(conn, cost_basis_usd=4.0)
    held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")
    plan = _plan(conn, held=held, has_unowned_pending_or_unknown_entry=True)
    assert plan.kind == "ABORT"
    assert plan.residual_stake_usd is None


def test_plan_subtracts_pending_from_residual():
    conn = _conn()
    _insert_held(conn, cost_basis_usd=4.0)
    held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")
    plan = _plan(conn, held=held, target_total_exposure_usd=10.0,
                 same_token_pending_entry_usd=3.0)
    assert plan.kind == "APPLY"
    assert plan.residual_stake_usd == pytest.approx(3.0)  # 10 - 4 - 3


def test_plan_concurrent_lease_collision_is_abort_no_second_order():
    """Two EDLI events for the same family: the FIRST acquires the lease; the SECOND
    finds the family already leased (acquire returns None) and must ABORT (no second
    order) — the core concurrency guard against the 2026-06-16 double-rest class."""
    conn = _conn()
    _insert_held(conn, cost_basis_usd=4.0)
    held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")
    first = _plan(conn, held=held)
    assert first.kind == "APPLY"
    assert first.lease_intent_id is not None
    second = _plan(conn, held=held)
    assert second.kind == "ABORT"
    assert second.residual_stake_usd is None
    assert second.lease_intent_id is None  # never acquired a second lease
    # Exactly ONE active lease for the family.
    active = conn.execute(
        "SELECT COUNT(*) FROM family_rebalance_intents WHERE status='PLANNED'"
    ).fetchone()[0]
    assert active == 1


def test_plan_non_redecision_event_is_noop():
    """A non-redecision event (fresh FSR entry) is never a fill-up even with a held
    same token — NOOP, leave the entry path untouched, no lease."""
    conn = _conn()
    _insert_held(conn, cost_basis_usd=4.0)
    held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")
    plan = _plan(conn, held=held, is_redecision_event=False)
    assert plan.kind == "NOOP"
    assert conn.execute("SELECT COUNT(*) FROM family_rebalance_intents").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Lease terminal advance (COMPLETE on submit ack, ABORTED on a late abort).
# ---------------------------------------------------------------------------
def test_complete_lease_on_submit_ack():
    conn = _conn()
    _insert_held(conn, cost_basis_usd=4.0)
    held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")
    plan = _plan(conn, held=held)
    assert plan.kind == "APPLY"
    fuw.complete_fill_up_lease(conn, plan.lease_intent_id, now_iso="2026-06-22T06:41:00",
                               new_entry_command_id="cmd-1")
    row = conn.execute(
        "SELECT status, new_entry_command_id FROM family_rebalance_intents WHERE intent_id=?",
        (plan.lease_intent_id,),
    ).fetchone()
    assert row["status"] == "COMPLETE"
    assert row["new_entry_command_id"] == "cmd-1"


def test_abort_lease_late():
    conn = _conn()
    _insert_held(conn, cost_basis_usd=4.0)
    held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")
    plan = _plan(conn, held=held)
    fuw.abort_fill_up_lease(conn, plan.lease_intent_id, now_iso="t1",
                            reason="PRESUBMIT_REREAD_NEW_FAMILY_ENTRY")
    row = conn.execute(
        "SELECT status, abort_reason FROM family_rebalance_intents WHERE intent_id=?",
        (plan.lease_intent_id,),
    ).fetchone()
    assert row["status"] == "ABORTED"
    assert row["abort_reason"] == "PRESUBMIT_REREAD_NEW_FAMILY_ENTRY"


# ---------------------------------------------------------------------------
# presubmit_reread_aborts — final exposure reread before submit.
# ---------------------------------------------------------------------------
def test_presubmit_reread_clean_no_abort():
    """Only the held same-token position exists (the one this fill-up owns) =>
    no new blocking entry => no abort."""
    conn = _conn()
    _insert_held(conn, position_id="p1", token_id="tok-A", cost_basis_usd=4.0)
    reason = fuw.presubmit_reread_aborts(
        conn,
        city="Tokyo", target_date="2026-06-23", temperature_metric="high",
        owned_position_id="p1", owned_token_id="tok-A",
    )
    assert reason is None


def test_presubmit_reread_new_unowned_same_family_entry_aborts():
    """A NEW unowned same-family entry (different bin) appeared between admission and
    submit => abort, no order (the 2026-06-16 double-rest hazard reappearing)."""
    conn = _conn()
    _insert_held(conn, position_id="p1", token_id="tok-A", bin_label="60-61F",
                 cost_basis_usd=4.0)
    # A sibling bin entry showed up after admission, not owned by this fill-up.
    _insert_held(conn, position_id="p2", token_id="tok-B", bin_label="62-63F",
                 phase="pending_entry", cost_basis_usd=5.0)
    reason = fuw.presubmit_reread_aborts(
        conn,
        city="Tokyo", target_date="2026-06-23", temperature_metric="high",
        owned_position_id="p1", owned_token_id="tok-A",
    )
    assert reason is not None
    assert "p2" in reason or "FAMILY" in reason.upper()
