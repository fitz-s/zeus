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

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.state.schema.family_rebalance_intents_schema import ensure_table
from src.strategy import shift_bin_wiring as sbw


_POSITION_CURRENT_DDL = """
CREATE TABLE position_current (
    position_id TEXT, phase TEXT, token_id TEXT, no_token_id TEXT,
    bin_label TEXT, direction TEXT, condition_id TEXT, city TEXT,
    target_date TEXT, temperature_metric TEXT, p_posterior REAL,
    entry_ci_width REAL, cost_basis_usd REAL, chain_cost_basis_usd REAL,
    shares REAL, chain_shares REAL, size_usd REAL, updated_at TEXT,
    chain_state TEXT, last_monitor_prob REAL, last_monitor_prob_is_fresh INTEGER
)
"""

_COLLATERAL_LEDGER_DDL = """
CREATE TABLE collateral_ledger_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pusd_balance_micro INTEGER NOT NULL,
  pusd_allowance_micro INTEGER NOT NULL,
  usdc_e_legacy_balance_micro INTEGER NOT NULL,
  ctf_token_balances_json TEXT NOT NULL,
  ctf_token_allowances_json TEXT NOT NULL,
  reserved_pusd_for_buys_micro INTEGER NOT NULL DEFAULT 0,
  reserved_tokens_for_sells_json TEXT NOT NULL DEFAULT '{}',
  captured_at TEXT NOT NULL,
  authority_tier TEXT NOT NULL,
  raw_balance_payload_hash TEXT
)
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_POSITION_CURRENT_DDL)
    ensure_table(conn)
    return conn


def _insert_chain_collateral(conn, balances: dict[str, int]) -> None:
    conn.execute(_COLLATERAL_LEDGER_DDL)
    conn.execute(
        """
        INSERT INTO collateral_ledger_snapshots (
            pusd_balance_micro, pusd_allowance_micro, usdc_e_legacy_balance_micro,
            ctf_token_balances_json, ctf_token_allowances_json,
            reserved_pusd_for_buys_micro, reserved_tokens_for_sells_json,
            captured_at, authority_tier, raw_balance_payload_hash
        ) VALUES (0, 0, 0, ?, '{}', 0, '{}', ?, 'CHAIN', 'hash')
        """,
        (json.dumps(balances), datetime.now(timezone.utc).isoformat()),
    )


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
    chain_shares=10.0,
    chain_state="synced",
    city="Tokyo",
    target_date="2026-06-23",
    metric="high",
    # p_posterior=0.50, entry_ci_width=0.20 (below) => entry_q_lcb = 0.40. Default
    # last_monitor_prob is WEAKENED relative to that entry certification so existing
    # residual/dust/blocking-focused tests keep exercising EXIT_OLD_LEG unchanged —
    # tests targeting the belief gate itself override these two explicitly.
    last_monitor_prob=0.10,
    last_monitor_prob_is_fresh=1,
):
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, token_id, no_token_id, bin_label, direction,
            condition_id, city, target_date, temperature_metric, p_posterior,
            entry_ci_width, cost_basis_usd, chain_cost_basis_usd, shares, chain_shares,
            size_usd, updated_at, chain_state, last_monitor_prob, last_monitor_prob_is_fresh
        ) VALUES (?, ?, ?, ?, ?, ?, 'cond-1', ?, ?, ?, 0.50, 0.20, ?, ?, 10.0, ?,
                  ?, '2026-06-22T06:00:00', ?, ?, ?)
        """,
        (position_id, phase, token_id, no_token_id, bin_label, direction, city, target_date,
         metric, cost_basis_usd, chain_cost_basis_usd, chain_shares, cost_basis_usd,
         chain_state, last_monitor_prob, last_monitor_prob_is_fresh),
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


def test_sibling_different_bin_reads_tuple_rows_without_row_factory():
    """Bare sqlite3 tuple rows must not make held sibling exposure disappear."""
    conn = sqlite3.connect(":memory:")
    conn.execute(_POSITION_CURRENT_DDL)
    ensure_table(conn)
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


def test_sibling_different_bin_reads_attached_position_current_schema():
    """Attached trade/world position_current must not disappear at column discovery."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ':memory:' AS trade")
    conn.execute(_POSITION_CURRENT_DDL.replace("position_current", "trade.position_current"))
    ensure_table(conn)
    conn.execute(
        """
        INSERT INTO trade.position_current (
            position_id, phase, token_id, no_token_id, bin_label, direction,
            condition_id, city, target_date, temperature_metric, p_posterior,
            entry_ci_width, cost_basis_usd, chain_cost_basis_usd, shares, chain_shares, size_usd, updated_at
        ) VALUES (
            'p-attached', 'quarantined', 'yes-30', 'no-30', '30C', 'buy_no',
            'cond-30', 'Munich', '2026-06-30', 'high', 0.88, 0.20,
            0.0, 21.27, 29.14, 29.14, 0.0, '2026-06-30T05:00:00'
        )
        """
    )

    held = sbw.read_held_sibling_exposure(
        conn,
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        selected_token_id="no-29",
        selected_bin_label="29C",
    )

    assert held is not None
    assert held.position_id == "p-attached"
    assert held.token_id == "no-30"
    assert held.current_live_usd == pytest.approx(21.27)


def test_sibling_reads_attached_chain_backed_zero_cost_quarantine():
    """Chain-backed quarantine is old-leg exposure even before fill economics bind."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ':memory:' AS trade")
    conn.execute(_POSITION_CURRENT_DDL.replace("position_current", "trade.position_current"))
    ensure_table(conn)
    conn.execute(
        """
        INSERT INTO trade.position_current (
            position_id, phase, token_id, no_token_id, bin_label, direction,
            condition_id, city, target_date, temperature_metric, p_posterior,
            entry_ci_width, cost_basis_usd, chain_cost_basis_usd, shares, chain_shares,
            size_usd, updated_at, chain_state
        ) VALUES (
            'p-chain-risk', 'quarantined', 'yes-30', 'no-30', '30C', 'buy_no',
            'cond-30', 'Munich', '2026-06-30', 'high', 0.88, 0.20,
            0.0, 0.0, 0.0, 29.14, 0.0, '2026-06-30T05:00:00',
            'entry_authority_quarantined'
        )
        """
    )

    held = sbw.read_held_sibling_exposure(
        conn,
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        selected_token_id="no-29",
        selected_bin_label="29C",
    )

    assert held is not None
    assert held.position_id == "p-chain-risk"
    assert held.token_id == "no-30"
    assert held.current_live_usd == pytest.approx(29.14)


def test_active_shift_lease_for_family_reads_existing_shift_before_fill_up():
    """A multi-cycle SHIFT_BIN lease must be visible to the reactor before D1 fill-up."""
    conn = _conn()
    plan = sbw.plan_shift_bin(
        conn,
        is_redecision_event=True,
        family_key="live|Tokyo|2026-06-23|high",
        event_id="evt-1",
        selected_token_id="tok-B",
        selected_bin_id="62-63F",
        selected_direction="buy_yes",
        held=sbw.HeldSiblingExposure(
            position_id="p-old",
            token_id="tok-A",
            bin_label="60-61F",
            direction="buy_yes",
            current_live_usd=4.0,
            entry_q_lcb=0.80,   # weakened belief so the live residual still exits
            current_q_lcb=0.20,
        ),
        old_leg_residual_usd=4.0,
        has_unowned_pending_or_unknown_entry=False,
        now_iso="2026-06-22T06:00:00+00:00",
        old_leg_dust_floor_usd=1.0,
    )
    assert plan.kind == "EXIT_OLD_LEG"

    lease = sbw.active_shift_lease_for_family(
        conn,
        family_key="live|Tokyo|2026-06-23|high",
    )

    assert lease is not None
    assert lease.intent_id == plan.lease_intent_id
    assert lease.status == "EXIT_SUBMITTED"
    assert lease.held_token_id == "tok-A"
    assert lease.selected_token_id == "tok-B"


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


def test_quarantined_chain_backed_buy_no_sibling_is_returned():
    """A quarantine label is not closure when chain shares are still positive."""
    conn = _conn()
    _insert_held(
        conn,
        position_id="munich-30-no",
        token_id="yes-30",
        no_token_id="no-30",
        phase="quarantined",
        bin_label="30C",
        direction="buy_no",
        cost_basis_usd=21.27,
        chain_cost_basis_usd=21.27,
        chain_shares=29.14,
        city="Munich",
        target_date="2026-06-30",
        metric="high",
    )

    held = sbw.read_held_sibling_exposure(
        conn, city="Munich", target_date="2026-06-30", temperature_metric="high",
        selected_token_id="no-29", selected_bin_label="29C",
    )

    assert held is not None
    assert held.position_id == "munich-30-no"
    assert held.token_id == "no-30"
    assert held.current_live_usd == pytest.approx(21.27)


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


def test_old_leg_residual_reads_tuple_rows_without_row_factory():
    conn = sqlite3.connect(":memory:")
    conn.execute(_POSITION_CURRENT_DDL)
    ensure_table(conn)
    _insert_held(conn, token_id="tok-A", cost_basis_usd=4.0)

    assert sbw.read_old_leg_residual_usd(conn, token_id="tok-A") == pytest.approx(4.0)


def test_old_leg_residual_prefers_chain_cost_basis():
    conn = _conn()
    _insert_held(conn, token_id="tok-A", cost_basis_usd=4.0, chain_cost_basis_usd=7.0)
    assert sbw.read_old_leg_residual_usd(conn, token_id="tok-A") == pytest.approx(7.0)


def test_old_leg_residual_reads_quarantined_chain_backed_row():
    conn = _conn()
    _insert_held(
        conn,
        token_id="yes-30",
        no_token_id="no-30",
        phase="quarantined",
        direction="buy_no",
        cost_basis_usd=21.27,
        chain_cost_basis_usd=21.27,
        chain_shares=29.14,
    )

    assert sbw.read_old_leg_residual_usd(conn, token_id="no-30") == pytest.approx(21.27)


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


def test_old_leg_residual_zero_when_chain_collateral_has_no_token():
    conn = _conn()
    _insert_held(
        conn,
        token_id="tok-A",
        cost_basis_usd=4.0,
        chain_cost_basis_usd=7.0,
        chain_shares=0.0,
    )
    _insert_chain_collateral(conn, {})

    assert sbw.read_old_leg_residual_usd(conn, token_id="tok-A") == pytest.approx(0.0)


def test_old_leg_residual_chain_zero_overrides_stale_local_projection():
    """Fresh CHAIN zero is closure truth even when local position_current is stale.

    This is the live redecision bug: after a sell/zero-collateral proof, the local
    projection can still carry cost. Close-before-open must not keep emitting
    EXIT_OLD_LEG from a row whose chain_state has already been marked zero; otherwise
    the selected new YES/NO leg never reaches final intent. A positive synced
    chain_shares row is different: it is current chain evidence and must stay live.
    """
    conn = _conn()
    _insert_held(
        conn,
        token_id="tok-A",
        cost_basis_usd=4.0,
        chain_cost_basis_usd=7.0,
        chain_shares=10.0,
        chain_state="chain_confirmed_zero",
    )
    _insert_chain_collateral(conn, {})

    assert sbw.read_old_leg_residual_usd(conn, token_id="tok-A") == pytest.approx(0.0)


def test_old_leg_residual_keeps_synced_chain_position_over_collateral_zero():
    """Regression for Kuala Lumpur 2026-07-02.

    A fresh collateral snapshot that lacks the token is not allowed to erase an
    active/synced position_current row with positive chain_shares. That false
    zero made shift-bin think the 33C NO old leg was closed and admitted a new
    34C YES entry in the same family.
    """
    conn = _conn()
    _insert_held(
        conn,
        token_id="yes-33",
        no_token_id="no-33",
        bin_label="33C",
        direction="buy_no",
        cost_basis_usd=15.9313,
        chain_cost_basis_usd=15.9313,
        chain_shares=20.69,
        chain_state="synced",
        city="Kuala Lumpur",
        target_date="2026-07-02",
        metric="high",
    )
    _insert_chain_collateral(conn, {})

    residual = sbw.read_old_leg_residual_usd(conn, token_id="no-33")

    assert residual == pytest.approx(15.9313)


def test_old_leg_residual_keeps_zero_cost_current_risk_chain_position():
    """Current chain risk keeps share sellability without fabricating USD residual."""

    conn = _conn()
    _insert_held(
        conn,
        token_id="yes-30",
        no_token_id="no-30",
        phase="quarantined",
        direction="buy_no",
        cost_basis_usd=0.0,
        chain_cost_basis_usd=0.0,
        chain_shares=29.14,
        chain_state="entry_authority_quarantined",
    )
    _insert_chain_collateral(conn, {})

    residual = sbw.read_old_leg_residual(conn, token_id="no-30")

    assert residual.shares == pytest.approx(29.14)
    assert residual.usd is None
    assert sbw.read_old_leg_residual_usd(conn, token_id="no-30") == pytest.approx(0.0)
    assert sbw.old_leg_is_live(residual, min_order_shares=1.0, dust_floor_usd=0.20) is True


def test_old_leg_residual_does_not_treat_sub_min_shares_as_usd():
    conn = _conn()
    _insert_held(
        conn,
        token_id="tok-A",
        cost_basis_usd=0.0,
        chain_cost_basis_usd=0.0,
        chain_shares=0.9,
        chain_state="synced",
    )

    residual = sbw.read_old_leg_residual(conn, token_id="tok-A")

    assert residual.shares == pytest.approx(0.9)
    assert residual.usd is None
    assert sbw.read_old_leg_residual_usd(conn, token_id="tok-A") == pytest.approx(0.0)
    assert sbw.old_leg_is_live(residual, min_order_shares=1.0, dust_floor_usd=0.20) is False


def test_old_leg_live_predicate_treats_equal_dust_floor_as_live():
    residual = sbw.OldLegResidual(shares=0.5, usd=0.20, source="position_current_usd")

    assert sbw.old_leg_is_live(residual, min_order_shares=1.0, dust_floor_usd=0.20) is True


def test_chain_zero_old_leg_admits_new_bin_under_shift_lease():
    """Once chain collateral and chain_state prove the old leg is zero, shift-bin admits new entry."""
    conn = _conn()
    _insert_held(
        conn,
        position_id="p-old",
        token_id="tok-A",
        bin_label="60-61F",
        cost_basis_usd=4.0,
        chain_cost_basis_usd=7.0,
        chain_shares=10.0,
        chain_state="chain_confirmed_zero",
    )
    _insert_chain_collateral(conn, {})
    held = _old_leg(conn)
    residual = sbw.read_old_leg_residual_usd(conn, token_id="tok-A")

    plan = _plan(conn, held=held, old_leg_residual_usd=residual)

    assert residual == pytest.approx(0.0)
    assert plan.kind == "ENTER_NEW_BIN"
    assert plan.allow_entry is True
    row = conn.execute(
        "SELECT status FROM family_rebalance_intents WHERE intent_id=?",
        (plan.lease_intent_id,),
    ).fetchone()
    assert row["status"] == "ENTRY_SUBMITTED"


def test_old_leg_residual_keeps_local_value_when_chain_collateral_has_token():
    conn = _conn()
    _insert_held(conn, token_id="tok-A", cost_basis_usd=4.0, chain_cost_basis_usd=7.0)
    _insert_chain_collateral(conn, {"tok-A": 10_000_000})

    assert sbw.read_old_leg_residual_usd(conn, token_id="tok-A") == pytest.approx(7.0)


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
