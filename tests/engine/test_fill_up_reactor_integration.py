# Created: 2026-06-22
# Last audited: 2026-07-02
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 (Pro
#   Extended) — D1 fill-up reactor wiring. These tests pin the ADDITIVE integration
#   points in src/engine/event_reactor_adapter.py:
#     - the same-token pending-entry reducer (_same_token_pending_entry_usd) used in
#       the residual sizing,
#     - the gate's entry-path byte-identity guarantee: for a NON-fill-up candidate
#       (no held same token / not a redecision) the fill-up orchestration is a
#       complete no-op (read_held_same_token_exposure → None → NOOP), so
#       _robust_stake_usd is unchanged and the fresh-entry stake/admission/submit
#       runs exactly as before,
#     - the residual override seam: an approved fill-up overrides the family-total
#       stake to the residual delta.
"""Reactor-level integration for D1 fill-up: entry-path byte-identity + residual."""
from __future__ import annotations

import sqlite3
import inspect
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.engine import event_reactor_adapter as era
from src.events.reactor import EventSubmissionReceipt
from src.state.schema.family_rebalance_intents_schema import ensure_table
from src.strategy import family_rebalance as fr
from src.strategy import fill_up_wiring as fuw


_POSITION_CURRENT_DDL = """
CREATE TABLE position_current (
    position_id TEXT, phase TEXT, token_id TEXT, no_token_id TEXT,
    bin_label TEXT, direction TEXT, condition_id TEXT, city TEXT,
    target_date TEXT, temperature_metric TEXT, p_posterior REAL,
    entry_ci_width REAL, cost_basis_usd REAL, chain_cost_basis_usd REAL,
    shares REAL, chain_shares REAL, size_usd REAL, updated_at TEXT
)
"""

_LIVE_CAP_DDL = """
CREATE TABLE edli_live_cap_usage (
    usage_id TEXT, event_id TEXT, final_intent_id TEXT,
    execution_command_id TEXT, reserved_notional_usd REAL,
    reservation_status TEXT
)
"""
_LIVE_ORDER_EVENTS_DDL = """
CREATE TABLE edli_live_order_events (
    aggregate_id TEXT, event_type TEXT, payload_json TEXT
)
"""
_LIVE_ORDER_PROJECTION_DDL = """
CREATE TABLE edli_live_order_projection (
    aggregate_id TEXT, event_id TEXT, final_intent_id TEXT,
    current_state TEXT, last_sequence INTEGER, last_event_type TEXT,
    last_event_hash TEXT, pending_reconcile INTEGER, venue_order_id TEXT,
    updated_at TEXT, schema_version INTEGER
)
"""
_VENUE_COMMANDS_DDL = """
CREATE TABLE venue_commands (
    command_id TEXT, decision_id TEXT, intent_kind TEXT, token_id TEXT,
    state TEXT, size REAL, price REAL, updated_at TEXT, created_at TEXT
)
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_POSITION_CURRENT_DDL)
    conn.execute(_LIVE_CAP_DDL)
    conn.execute(_LIVE_ORDER_EVENTS_DDL)
    conn.execute(_LIVE_ORDER_PROJECTION_DDL)
    conn.execute(_VENUE_COMMANDS_DDL)
    ensure_table(conn)
    return conn


def _insert_held(conn, *, position_id="p1", token_id="tok-A", phase="active",
                 bin_label="60-61F", direction="buy_yes", p_posterior=0.50,
                 entry_ci_width=0.20, cost_basis_usd=4.0):
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, token_id, no_token_id, bin_label, direction,
            condition_id, city, target_date, temperature_metric, p_posterior,
            entry_ci_width, cost_basis_usd, chain_cost_basis_usd, shares, chain_shares,
            size_usd, updated_at
        ) VALUES (?, ?, ?, '', ?, ?, 'cond-1', 'Tokyo', '2026-06-23', 'high',
                  ?, ?, ?, NULL, ?, NULL, ?, '2026-06-22T06:00:00')
        """,
        (position_id, phase, token_id, bin_label, direction, p_posterior,
         entry_ci_width, cost_basis_usd, cost_basis_usd, cost_basis_usd),
    )


# ---------------------------------------------------------------------------
# _same_token_pending_entry_usd — residual netting of in-flight same-token entry.
# ---------------------------------------------------------------------------
def test_same_token_pending_zero_when_no_live_cap_tables():
    conn = sqlite3.connect(":memory:")  # no live-cap tables
    assert era._same_token_pending_entry_usd(conn, token_id="tok-A") == 0.0


def test_same_token_pending_sums_unmaterialized_same_token_reservations():
    conn = _conn()
    # Two RESERVED live-cap rows: one for tok-A (our token), one for tok-B.
    conn.execute(
        "INSERT INTO edli_live_cap_usage VALUES "
        "('u1','e1','edli_intent:e1:tok-A','cmd1', 3.0, 'RESERVED')"
    )
    conn.execute(
        "INSERT INTO edli_live_cap_usage VALUES "
        "('u2','e2','edli_intent:e2:tok-B','cmd2', 9.0, 'RESERVED')"
    )
    # tok-A is NOT yet materialized in position_current/venue_commands → counts.
    pending = era._same_token_pending_entry_usd(conn, token_id="tok-A", trade_conn=conn)
    assert pending == pytest.approx(3.0)  # only the tok-A row, not tok-B


def test_same_token_pending_excludes_materialized():
    conn = _conn()
    conn.execute(
        "INSERT INTO edli_live_cap_usage VALUES "
        "('u1','e1','edli_intent:e1:tok-A','cmd1', 3.0, 'RESERVED')"
    )
    # Mark tok-A materialized in position_current (the represented-in-trade-truth check).
    _insert_held(conn, token_id="tok-A", cost_basis_usd=3.0)
    pending = era._same_token_pending_entry_usd(conn, token_id="tok-A", trade_conn=conn)
    assert pending == pytest.approx(0.0)  # represented → not double-counted as pending


def test_family_pending_counts_selected_sibling_token_live_cap():
    conn = _conn()
    conn.execute(
        "INSERT INTO edli_live_cap_usage VALUES "
        "('u1','e1','edli_intent:e1:tok-B','cmd1', 6.5, 'RESERVED')"
    )

    pending = era._family_pending_entry_usd(
        conn,
        candidate_token_ids=("tok-A", "tok-B"),
        trade_conn=conn,
    )

    assert pending == pytest.approx(6.5)


def test_family_pending_truth_blocks_third_sibling_pending_reconcile():
    conn = _conn()
    conn.execute(
        """
        INSERT INTO edli_live_order_projection VALUES (
            'agg-C', 'event-1', 'edli_intent:event-1:tok-C',
            'PENDING_RECONCILE', 4, 'SubmitUnknown', 'hash',
            1, NULL, '2026-07-02T00:00:00+00:00', 1
        )
        """
    )

    truth = era._family_pending_entry_truth(
        conn,
        candidate_token_ids=("tok-A", "tok-B", "tok-C"),
        trade_conn=conn,
    )

    assert truth.truth_available is True
    assert truth.has_pending_or_unknown is True
    assert "edli_live_order_projection" in truth.sources


def test_family_pending_truth_ignores_materialized_filled_command():
    conn = _conn()
    conn.execute(
        """
        INSERT INTO venue_commands VALUES (
            'cmd-filled', 'dec-filled', 'ENTRY', 'tok-A', 'FILLED',
            10.0, 0.25, '2026-07-02T00:00:00+00:00',
            '2026-07-02T00:00:00+00:00'
        )
        """
    )
    _insert_held(conn, token_id="tok-A", cost_basis_usd=2.5)

    truth = era._family_pending_entry_truth(
        conn,
        candidate_token_ids=("tok-A",),
        trade_conn=conn,
    )

    assert truth.truth_available is True
    assert truth.pending_usd == pytest.approx(0.0)
    assert truth.has_pending_or_unknown is False


def test_family_pending_truth_blocks_unmaterialized_filled_command():
    conn = _conn()
    conn.execute(
        """
        INSERT INTO venue_commands VALUES (
            'cmd-filled-gap', 'dec-filled-gap', 'ENTRY', 'tok-A', 'FILLED',
            10.0, 0.25, '2026-07-02T00:00:00+00:00',
            '2026-07-02T00:00:00+00:00'
        )
        """
    )

    truth = era._family_pending_entry_truth(
        conn,
        candidate_token_ids=("tok-A",),
        trade_conn=conn,
    )

    assert truth.truth_available is True
    assert truth.has_pending_or_unknown is True
    assert "venue_commands_filled_unmaterialized" in truth.sources


def test_live_cap_representation_matches_execution_command_id_to_command_id():
    conn = _conn()
    conn.execute(
        """
        INSERT INTO venue_commands VALUES (
            'cmd-execution', 'dec-different', 'ENTRY', 'tok-A', 'REJECTED',
            10.0, 0.25, '2026-07-02T00:00:00+00:00',
            '2026-07-02T00:00:00+00:00'
        )
        """
    )

    assert era._durable_live_cap_usage_is_represented_in_trade_truth(
        conn,
        execution_command_id="cmd-execution",
        final_intent_id="edli_intent:event-1:tok-A",
    ) is True


def test_live_cap_filled_without_position_truth_still_blocks_family():
    conn = _conn()
    conn.execute(
        """
        INSERT INTO edli_live_cap_usage VALUES (
            'u-filled-gap', 'e-filled-gap', 'edli_intent:e-filled-gap:tok-A',
            'cmd-filled-gap', 7.0, 'RESERVED'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands VALUES (
            'cmd-filled-gap', 'dec-filled-gap', 'ENTRY', 'tok-A', 'FILLED',
            10.0, 0.70, '2026-07-02T00:00:00+00:00',
            '2026-07-02T00:00:00+00:00'
        )
        """
    )

    truth = era._family_pending_entry_truth(
        conn,
        candidate_token_ids=("tok-A",),
        trade_conn=conn,
    )

    assert truth.truth_available is True
    assert truth.has_pending_or_unknown is True
    assert truth.pending_usd == pytest.approx(7.0)
    assert "family_pending_notional" in truth.sources


def test_fill_up_unknown_entry_keeps_family_lease_active():
    conn = _conn()
    lease = fuw.acquire_rebalance_lease(
        conn,
        family_key="live|Tokyo|2026-06-23|high",
        operation="FILL_UP",
        now_iso="t0",
        held_position_id="p1",
        held_token_id="tok-A",
        held_bin_id="60-61F",
        selected_token_id="tok-A",
        selected_bin_id="60-61F",
        event_id="event-1",
    )
    assert lease is not None

    fuw.record_fill_up_entry_unknown(
        conn,
        lease,
        now_iso="t1",
        new_entry_command_id="cmd-entry",
        reason="POST_SUBMIT_UNKNOWN",
    )

    row = conn.execute(
        "SELECT status, new_entry_command_id FROM family_rebalance_intents WHERE intent_id=?",
        (lease,),
    ).fetchone()
    assert row["status"] == "ENTRY_UNKNOWN"
    assert row["new_entry_command_id"] == "cmd-entry"
    assert fr.active_lease_for_family(conn, "live|Tokyo|2026-06-23|high") == lease


def test_entries_pause_gate_runs_before_fill_up_lease_planning():
    src = inspect.getsource(era.event_bound_live_adapter_from_trade_conn)
    pause_gate = src.index("_entry_pause_blocks_live_submit(live_cap_conn or trade_conn)")
    receipt_build = src.index("no_submit_receipt = build_event_bound_no_submit_receipt(")

    assert pause_gate < receipt_build


def test_post_plan_no_submit_aborts_fill_up_lease():
    conn = _conn()
    lease = fuw.acquire_rebalance_lease(
        conn,
        family_key="live|Tokyo|2026-06-23|high",
        operation="FILL_UP",
        now_iso="t0",
        held_position_id="p1",
        held_token_id="tok-A",
        held_bin_id="60-61F",
        selected_token_id="tok-A",
        selected_bin_id="60-61F",
        event_id="event-1",
    )
    assert lease is not None

    era._abort_family_rebalance_entry_payloads_after_no_submit(
        conn,
        fill_up_lease_payload={"intent_id": lease},
        now_iso="t1",
        reason="RISK_GUARD_BLOCKED",
    )

    row = conn.execute(
        "SELECT status, abort_reason FROM family_rebalance_intents WHERE intent_id=?",
        (lease,),
    ).fetchone()
    assert row["status"] == "ABORTED"
    assert row["abort_reason"] == "FILL_UP_POST_PLAN_NO_SUBMIT:RISK_GUARD_BLOCKED"
    assert fr.active_lease_for_family(conn, "live|Tokyo|2026-06-23|high") is None


def test_fill_up_submit_exception_unknown_advances_family_lease():
    conn = _conn()
    lease = fuw.acquire_rebalance_lease(
        conn,
        family_key="live|Tokyo|2026-06-23|high",
        operation="FILL_UP",
        now_iso="t0",
        held_position_id="p1",
        held_token_id="tok-A",
        held_bin_id="60-61F",
        selected_token_id="tok-A",
        selected_bin_id="60-61F",
        event_id="event-1",
    )
    assert lease is not None

    terminal_result = era._fallback_submit_result_after_live_command_failure(
        RuntimeError("venue call interrupted"),
        phase="calling_executor_submit",
        decision_time=datetime(2026, 7, 2, tzinfo=UTC),
    )
    receipt = EventSubmissionReceipt(
        submitted=False,
        event_id="event-1",
        causal_snapshot_id="snap-1",
        fill_up_lease_payload={"intent_id": lease},
    )
    command = SimpleNamespace(payload={"execution_command_id": "cmd-entry-unknown"})

    era._advance_family_rebalance_lease_after_submit(
        trade_conn=conn,
        no_submit_receipt=receipt,
        command=command,
        submit_result=terminal_result,
        now_iso="2026-07-02T00:00:01+00:00",
    )

    row = conn.execute(
        "SELECT status, new_entry_command_id, abort_reason FROM family_rebalance_intents WHERE intent_id=?",
        (lease,),
    ).fetchone()
    assert row["status"] == "ENTRY_UNKNOWN"
    assert row["new_entry_command_id"] == "cmd-entry-unknown"
    assert row["abort_reason"] == "FILL_UP_ENTRY_RECONCILE_REQUIRED:POST_SUBMIT_UNKNOWN"
    assert fr.active_lease_for_family(conn, "live|Tokyo|2026-06-23|high") == lease


def test_fill_up_submit_rejected_aborts_family_lease():
    conn = _conn()
    lease = fuw.acquire_rebalance_lease(
        conn,
        family_key="live|Tokyo|2026-06-23|high",
        operation="FILL_UP",
        now_iso="t0",
        held_position_id="p1",
        held_token_id="tok-A",
        held_bin_id="60-61F",
        selected_token_id="tok-A",
        selected_bin_id="60-61F",
        event_id="event-1",
    )
    assert lease is not None

    receipt = EventSubmissionReceipt(
        submitted=False,
        event_id="event-1",
        causal_snapshot_id="snap-1",
        fill_up_lease_payload={"intent_id": lease},
    )
    command = SimpleNamespace(payload={"execution_command_id": "cmd-entry-rejected"})
    submit_result = era.EventBoundExecutorSubmitResult(
        status="REJECTED",
        reason_code="deterministic_reject",
        venue_call_started=True,
        venue_ack_received=True,
        side_effect_known=True,
    )

    era._advance_family_rebalance_lease_after_submit(
        trade_conn=conn,
        no_submit_receipt=receipt,
        command=command,
        submit_result=submit_result,
        now_iso="2026-07-02T00:00:01+00:00",
    )

    row = conn.execute(
        "SELECT status, new_entry_command_id, abort_reason FROM family_rebalance_intents WHERE intent_id=?",
        (lease,),
    ).fetchone()
    assert row["status"] == "ABORTED"
    assert row["new_entry_command_id"] is None
    assert row["abort_reason"] == "FILL_UP_SUBMIT_NO_ACK:REJECTED"
    assert fr.active_lease_for_family(conn, "live|Tokyo|2026-06-23|high") is None


# ---------------------------------------------------------------------------
# Entry-path byte-identity: the gate is a no-op for non-fill-up candidates.
# ---------------------------------------------------------------------------
def test_gate_noop_for_fresh_entry_token():
    """A fresh-entry token (no held same-token position) → read returns None →
    plan_fill_up NOOP → the caller leaves _robust_stake_usd UNCHANGED. This is the
    contract that keeps the fresh-entry path byte-identical."""
    conn = _conn()
    held = fuw.read_held_same_token_exposure(conn, token_id="tok-FRESH")
    assert held is None
    plan = fuw.plan_fill_up(
        conn, is_redecision_event=True, family_key="live|Tokyo|2026-06-23|high",
        event_id="e1", selected_token_id="tok-FRESH", selected_bin_id="b",
        selected_direction="buy_yes", held=held, q_current_lcb=0.6,
        target_total_exposure_usd=10.0, same_token_pending_entry_usd=0.0,
        venue_min_increment_usd=1.0, now_iso="t0",
    )
    assert plan.kind == "NOOP"
    assert plan.residual_stake_usd is None  # caller keeps the family-total stake
    # No lease was taken on the fresh-entry path.
    assert conn.execute("SELECT COUNT(*) FROM family_rebalance_intents").fetchone()[0] == 0


def test_gate_residual_override_for_held_same_token():
    """A held same-token strengthening redecision overrides the family-total stake to
    the residual (target 10 - current 4 - pending 0 = 6) — exactly ONE residual delta,
    never a second full Kelly stake."""
    conn = _conn()
    _insert_held(conn, token_id="tok-A", cost_basis_usd=4.0, p_posterior=0.50, entry_ci_width=0.20)
    held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")
    assert held is not None and held.entry_q_lcb == pytest.approx(0.40)
    family_total_stake = 10.0  # what the ΔU kernel would have emitted
    plan = fuw.plan_fill_up(
        conn, is_redecision_event=True, family_key="live|Tokyo|2026-06-23|high",
        event_id="e1", selected_token_id="tok-A", selected_bin_id="60-61F",
        selected_direction="buy_yes", held=held, q_current_lcb=0.55,
        target_total_exposure_usd=family_total_stake, same_token_pending_entry_usd=0.0,
        venue_min_increment_usd=1.0, now_iso="t0",
    )
    assert plan.kind == "APPLY"
    # The overridden stake is the RESIDUAL, strictly LESS than the family total.
    assert plan.residual_stake_usd == pytest.approx(6.0)
    assert plan.residual_stake_usd < family_total_stake


def test_gate_aborts_when_at_target_emits_no_order():
    conn = _conn()
    _insert_held(conn, token_id="tok-A", cost_basis_usd=10.0)  # already at target
    held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")
    plan = fuw.plan_fill_up(
        conn, is_redecision_event=True, family_key="live|Tokyo|2026-06-23|high",
        event_id="e1", selected_token_id="tok-A", selected_bin_id="60-61F",
        selected_direction="buy_yes", held=held, q_current_lcb=0.55,
        target_total_exposure_usd=10.0, same_token_pending_entry_usd=0.0,
        venue_min_increment_usd=1.0, now_iso="t0",
    )
    assert plan.kind == "ABORT"
    assert plan.residual_stake_usd is None  # NO order


# ---------------------------------------------------------------------------
# Selection-scope admission: a held SAME-TOKEN proof must survive selection for an
# EDLI redecision (so the fill-up candidate can be selected), but stay dropped for
# a fresh entry (allow_same_family_monitor_owned=False) — byte-identical entry path.
# ---------------------------------------------------------------------------
def _held_token_proof(*, token_id="tok-A", direction="buy_yes"):
    import json as _json
    from src.types.market import Bin
    from src.events.candidate_binding import MarketTopologyCandidate

    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    depth = {
        "YES": {"asks": [{"price": "0.40", "size": "1000"}], "bids": [{"price": "0.30", "size": "100"}]},
        "NO": {"asks": [{"price": "0.55", "size": "1000"}], "bids": [{"price": "0.40", "size": "100"}]},
    }
    row = {
        "snapshot_id": "snap", "condition_id": "cond-1",
        "yes_token_id": "tok-A", "no_token_id": "no-1",
        "selected_outcome_token_id": "", "outcome_label": "",
        "min_tick_size": "0.01", "min_order_size": "5",
        "fee_details_json": _json.dumps({"fee_rate_fraction": 0.0}),
        "neg_risk": 0, "orderbook_depth_json": _json.dumps(depth),
        "tradeability_status_json": "{}", "book_hash": "bh",
    }
    ep, _pf, _c = era._execution_price_from_snapshot(row, selected_token_id=token_id, direction=direction)
    return era._CandidateProof(
        candidate=MarketTopologyCandidate(
            city="Tokyo", target_date="2026-06-23", metric="high",
            condition_id="cond-1", yes_token_id="tok-A", no_token_id="no-1", bin=bin_x,
        ),
        token_id=token_id, direction=direction, row=row,
        executable_snapshot_id="snap", execution_price=ep,
        q_posterior=0.55, q_lcb_5pct=0.52, c_cost_95pct=None, p_fill_lcb=1.0,
        trade_score=1.0, p_value=0.01, passed_prefilter=True,
        native_quote_available=True, p_cal_vector_hash="ch", p_live_vector_hash="lh",
        missing_reason=None,
    )


def test_held_same_token_proof_survives_redecision_scope():
    """For an EDLI redecision (allow_same_family_monitor_owned=True), a held SAME-TOKEN
    proof is ADMITTED to selection so the fill-up candidate can win — this is the
    admission widening that lets the fill-up gate fire."""
    conn = _conn()
    _insert_held(conn, token_id="tok-A", cost_basis_usd=4.0)
    proof = _held_token_proof(token_id="tok-A")
    scoped = era._selection_scoped_proofs(
        proofs=(proof,),
        held_position_conn=conn,
        allow_same_family_monitor_owned=True,
    )
    assert len(scoped) == 1  # admitted for redecision


def test_held_same_token_proof_dropped_for_fresh_entry_byte_identical():
    """For a FRESH entry (allow_same_family_monitor_owned=False) the held same-token
    proof is DROPPED exactly as before — the entry path is byte-identical."""
    conn = _conn()
    _insert_held(conn, token_id="tok-A", cost_basis_usd=4.0)
    proof = _held_token_proof(token_id="tok-A")
    scoped = era._selection_scoped_proofs(
        proofs=(proof,),
        held_position_conn=conn,
        allow_same_family_monitor_owned=False,
    )
    assert scoped == ()  # dropped: a held token is not a fresh-entry candidate


def test_qkernel_same_token_unstrengthened_fill_up_is_not_selection_actionable():
    """A non-strengthened held token must not starve sibling shift re-selection."""

    conn = _conn()
    _insert_held(
        conn,
        token_id="tok-A",
        cost_basis_usd=4.0,
        p_posterior=0.50,
        entry_ci_width=0.20,
    )
    proof = _held_token_proof(token_id="tok-A")
    bin_id = era._candidate_bin_id(proof)
    proof = replace(
        proof,
        selection_authority_applied="qkernel_spine",
        qkernel_execution_economics={
            "source": "qkernel_spine",
            "candidate_id": f"DIRECT_YES:{bin_id}",
            "route_id": f"DIRECT_YES:{bin_id}@proof",
            "side": "YES",
            "bin_id": bin_id,
            "payoff_q_point": 0.50,
            "payoff_q_lcb": 0.35,
            "q_dot_payoff": 0.50,
            "edge_lcb": 0.15,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": 10.0,
            "optimal_delta_u": 0.02,
            "cost": 0.20,
            "false_edge_rate": 0.01,
            "direction_law_ok": True,
            "coherence_allows": True,
            "selection_guard_basis": "SELECTION_BETA_95",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": 0.35,
        },
    )

    reason = era._qkernel_same_token_fill_up_selection_rejection_reason(
        proof=proof,
        held_position_conn=conn,
        locked_opportunity_conn=None,
    )

    assert reason == "BELIEF_NOT_STRENGTHENED"


def test_two_concurrent_redecisions_one_lease_one_order():
    """Two EDLI events for the same family: only one acquires the lease and produces
    a residual order; the other aborts (no second order)."""
    conn = _conn()
    _insert_held(conn, token_id="tok-A", cost_basis_usd=4.0)
    held = fuw.read_held_same_token_exposure(conn, token_id="tok-A")
    kw = dict(
        is_redecision_event=True, family_key="live|Tokyo|2026-06-23|high",
        event_id="e1", selected_token_id="tok-A", selected_bin_id="60-61F",
        selected_direction="buy_yes", held=held, q_current_lcb=0.55,
        target_total_exposure_usd=10.0, same_token_pending_entry_usd=0.0,
        venue_min_increment_usd=1.0, now_iso="t0",
    )
    first = fuw.plan_fill_up(conn, **kw)
    second = fuw.plan_fill_up(conn, **kw)
    applied = [p for p in (first, second) if p.kind == "APPLY"]
    aborted = [p for p in (first, second) if p.kind == "ABORT"]
    assert len(applied) == 1
    assert len(aborted) == 1
    assert aborted[0].residual_stake_usd is None
