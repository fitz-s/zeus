# Created: 2026-06-22
# Last reused/audited: 2026-07-02
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 (Pro
#   Extended) — D2 shift-bin reactor wiring. Pins the ADDITIVE integration points in
#   src/engine/event_reactor_adapter.py:
#     - the close-before-open gate: a SIBLING-different-bin selection with a live old
#       leg produces EXIT_OLD_LEG (lease EXIT_SUBMITTED) and NO new-bin entry; the old
#       leg must be proven zero/dust before a counter-entry is admitted (ENTER_NEW_BIN).
#       This applies even when the trigger is a forecast snapshot rather than an
#       already-labelled redecision event.
#     - true fresh-entry byte-identity: with no held family exposure, shift-bin is a
#       complete no-op (read_held_sibling_exposure → None → NOOP), so the entry path is
#       unaltered. Same-token held exposure belongs to D1 fill-up, not D2 shift-bin.
#     - the OLD-leg closure proof: read_old_leg_residual_usd returns 0.0 once the old
#       leg leaves position_current (voided/closed), and +inf on ambiguous truth so the
#       caller never falsely enters.
"""Reactor-level integration for D2 shift-bin: close-before-open + path byte-identity."""
from __future__ import annotations

import inspect
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from src.engine import event_reactor_adapter as era
from src.events.reactor import EventSubmissionReceipt
from src.state.schema.family_rebalance_intents_schema import ensure_table
from src.strategy import family_rebalance as fr
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


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_POSITION_CURRENT_DDL)
    conn.execute(_LIVE_CAP_DDL)
    conn.execute(_LIVE_ORDER_EVENTS_DDL)
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
# THE HAZARD this feature fixes: a sibling selection must NOT open the new bin while
# the old leg is live. Through the wiring this is EXIT_OLD_LEG, allow_entry False.
# ---------------------------------------------------------------------------
def test_reactor_runs_same_family_management_for_forecast_selections_too():
    """A forecast event with a held sibling is position management, not fresh entry."""

    src = inspect.getsource(era)
    assert "if _recapture.may_submit and allow_same_family_monitor_owned" not in src
    assert "if _recapture.may_submit:" in src
    assert "_shift_bin_wiring.read_held_sibling_exposure(" in src


def test_existing_and_new_shift_paths_share_old_leg_live_predicate():
    src = inspect.getsource(era)
    existing = src.index("_existing_shift_lease is not None")
    sibling = src.index("_held_sibling is not None", existing)

    assert "_shift_bin_wiring.old_leg_is_live(" in src[existing:sibling]
    assert "_shift_bin_wiring.old_leg_is_live(" in src[sibling:]
    assert "> float(_dust_floor_usd)" not in src[existing:sibling]


def test_reactor_fails_closed_when_held_family_cannot_bind_sibling():
    """Held-family truth with no old-leg binding must not fall through to fresh entry."""

    src = inspect.getsource(era)
    sibling_read = src.index("_shift_bin_wiring.read_held_sibling_exposure(")
    unresolved_gate = src.index("SHIFT_BIN_NO_SUBMIT:HELD_FAMILY_UNRESOLVED", sibling_read)
    entry_build = src.index("kelly = dataclass_replace(", sibling_read)

    assert "_entry_held_position_same_family_reason(" in src[sibling_read:unresolved_gate]
    assert unresolved_gate < entry_build


def test_reactor_family_truth_reads_attached_chain_backed_zero_cost_quarantine():
    """Attached chain-backed quarantine must be visible to the adapter fallback."""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ':memory:' AS trade")
    conn.execute(_POSITION_CURRENT_DDL.replace("position_current", "trade.position_current"))
    conn.execute("ALTER TABLE trade.position_current ADD COLUMN chain_state TEXT")
    ensure_table(conn)
    conn.execute(
        """
        INSERT INTO trade.position_current (
            position_id, phase, token_id, no_token_id, bin_label, direction,
            condition_id, city, target_date, temperature_metric, p_posterior,
            entry_ci_width, cost_basis_usd, chain_cost_basis_usd, shares, chain_shares,
            size_usd, updated_at, chain_state
        ) VALUES (
            'munich-chain-risk', 'quarantined', 'yes-30', 'no-30', '30C', 'buy_no',
            'cond-30', 'Munich', '2026-06-30', 'high', 0.88, 0.20,
            0.0, 0.0, 0.0, 29.14, 0.0, '2026-06-30T05:00:00',
            'entry_authority_quarantined'
        )
        """
    )
    proof = SimpleNamespace(
        candidate=SimpleNamespace(
            city="Munich",
            target_date="2026-06-30",
            metric="high",
        )
    )

    reason = era._entry_held_position_same_family_reason(conn, proof)

    assert reason is not None
    assert reason.startswith("OPEN_POSITION_SAME_FAMILY_MONITOR_OWNED:")
    assert "position_id=munich-chain-risk" in reason
    assert "bin_label=30C" in reason


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


def test_day0_remaining_day_forecast_determinism_does_not_mature_exit_authority(monkeypatch):
    payload = {
        "metric": "high",
        "rounded_value": 31,
        "high_so_far": 31.0,
        "observation_time": "2026-06-30T14:00:00+02:00",
    }
    family = SimpleNamespace(city="Munich", target_date="2026-06-30")
    monkeypatch.setattr(
        "src.signal.diurnal.build_day0_temporal_context",
        lambda *args, **kwargs: SimpleNamespace(
            daypart="morning",
            post_peak_confidence=0.0,
            current_local_timestamp=datetime(2026, 6, 30, 14, tzinfo=timezone.utc),
        ),
    )

    era._record_day0_remaining_day_exit_authority(
        payload=payload,
        family=family,
        metric="high",
        remaining_extremes_native=np.array([28.0, 29.0, 30.0]),
        decision_time=datetime(2026, 6, 30, 12, tzinfo=timezone.utc),
    )

    assert payload["_edli_day0_exit_authority_status"] == "immature"
    assert payload["_edli_day0_exit_authority_reason"] == (
        "day0_high_extreme_not_mature:daypart=morning,post_peak_confidence=0.000"
    )
    assert payload["_edli_day0_bound_classification"] == "DETERMINISTIC"
    assert payload["_edli_day0_model_bound_classification_role"] == (
        "forecast_remaining_window_evidence_only"
    )


def test_day0_immature_remaining_day_blocks_shift_exit_before_lease():
    payload = {
        "_edli_q_source": "day0_remaining_day",
        "_edli_day0_exit_authority_status": "immature",
        "_edli_day0_exit_authority_reason": (
            "day0_high_extreme_not_mature:"
            "daypart=pre_sunrise,post_peak_confidence=0.034"
        ),
    }
    reason = era._day0_shift_old_leg_exit_block_reason(
        event=SimpleNamespace(event_type="DAY0_EXTREME_UPDATED"),
        payload=payload,
        proof=SimpleNamespace(q_source="day0_remaining_day"),
    )

    assert reason == (
        "DAY0_IMMATURE_SHIFT_EXIT_AUTHORITY:"
        "day0_high_extreme_not_mature:"
        "daypart=pre_sunrise,post_peak_confidence=0.034"
    )

    src = inspect.getsource(era)
    sibling_read = src.index("_shift_bin_wiring.read_held_sibling_exposure(")
    block = src.index("_day0_shift_old_leg_exit_block_reason(", sibling_read)
    plan = src.index("_shift_bin_wiring.plan_shift_bin(", sibling_read)
    assert block < plan


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


def test_shift_bin_family_pending_guard_counts_third_sibling():
    conn = _conn()
    conn.execute(
        "INSERT INTO edli_live_cap_usage VALUES "
        "('u-c','event-c','edli_intent:event-c:tok-C','cmd-c', 4.25, 'RESERVED')"
    )

    truth = era._family_pending_entry_truth(
        conn,
        candidate_token_ids=("tok-A", "tok-B", "tok-C"),
        trade_conn=conn,
    )

    assert truth.truth_available is True
    assert truth.has_pending_or_unknown is True
    assert truth.pending_usd == pytest.approx(4.25)


def test_shift_bin_unknown_counter_entry_keeps_family_lease_active():
    conn = _conn()
    lease = sbw.acquire_rebalance_lease(
        conn,
        family_key="live|Tokyo|2026-06-23|high",
        operation="SHIFT_BIN",
        now_iso="t0",
        held_position_id="p-old",
        held_token_id="tok-A",
        held_bin_id="60-61F",
        selected_token_id="tok-B",
        selected_bin_id="62-63F",
        event_id="event-1",
    )
    assert lease is not None

    sbw.record_entry_unknown(
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


def test_shift_bin_submit_exception_unknown_advances_family_lease():
    conn = _conn()
    lease = sbw.acquire_rebalance_lease(
        conn,
        family_key="live|Tokyo|2026-06-23|high",
        operation="SHIFT_BIN",
        now_iso="t0",
        held_position_id="p-old",
        held_token_id="tok-A",
        held_bin_id="60-61F",
        selected_token_id="tok-B",
        selected_bin_id="62-63F",
        event_id="event-1",
    )
    assert lease is not None

    terminal_result = era._fallback_submit_result_after_live_command_failure(
        RuntimeError("venue call interrupted"),
        phase="calling_executor_submit",
        decision_time=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    receipt = EventSubmissionReceipt(
        submitted=False,
        event_id="event-1",
        causal_snapshot_id="snap-1",
        shift_bin_lease_payload={"intent_id": lease, "phase": "ENTER_NEW_BIN"},
    )
    command = SimpleNamespace(payload={"execution_command_id": "cmd-shift-entry-unknown"})

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
    assert row["new_entry_command_id"] == "cmd-shift-entry-unknown"
    assert row["abort_reason"] == "SHIFT_BIN_ENTRY_RECONCILE_REQUIRED:POST_SUBMIT_UNKNOWN"
    assert fr.active_lease_for_family(conn, "live|Tokyo|2026-06-23|high") == lease


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
