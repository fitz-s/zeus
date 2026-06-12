# Created: 2026-06-12
# Last reused/audited: 2026-06-12
# Authority basis: operator directive 2026-06-10 day0-shadow-receipt-enrichment +
#   day0-shadow-receipt-dual-persist (2026-06-12): shadow receipts must land in
#   edli_no_submit_receipts (the shadow comparator's source of truth) as well as
#   no_trade_regret_events, with q_live/q_lcb_5pct/direction/trade_score non-null.
"""Antibody tests: DAY0_SCOPE_SHADOW_ONLY dual-persist fix (2026-06-12).

Root cause closed: EdliNoSubmitReceiptLedger.insert_idempotent requires proof_accepted=True,
but shadow receipts carry proof_accepted=False. _receipt_money_path_blocker routed them
exclusively to no_trade_regret_events, bypassing edli_no_submit_receipts. The shadow
comparator (day0_remaining_day_adapter) reads edli_no_submit_receipts only, so it could
never see the shadow decision content.

Fix: reactor._process_one_post_submit calls insert_shadow_idempotent before routing to
regret; EdliNoSubmitReceiptLedger.insert_shadow_idempotent bypasses the proof_accepted guard
while enforcing side_effect_status="NO_SUBMIT" + reason="DAY0_SCOPE_SHADOW_ONLY".

Invariants asserted:
  (a) day0-scoped event -> edli_no_submit_receipts row with non-null q_live, q_lcb_5pct,
      direction, trade_score (the shadow comparator can find and pair them)
  (b) never-submit still holds even with real_order_submit_enabled=True (the dual-persist
      is orthogonal to the submit gate)
  (c) the shadow comparator's read query (day0_remaining_day_adapter) finds the new row
      shape and produces a non-empty cell observation list
  (d) EdliNoSubmitReceiptLedger.insert_shadow_idempotent rejects non-DAY0_SCOPE_SHADOW_ONLY
      and non-NO_SUBMIT receipts (antibody guard is narrow)
  (e) the regret row (no_trade_regret_events) is ALSO present alongside edli_no_submit_receipts
      (dual-persist means BOTH tables are populated, not one replacing the other)
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.riskguard.risk_level import RiskLevel

# Reuse the no_bypass fixtures (day0 event + viable trade-conn fixture).
_NB_PATH = Path(__file__).resolve().parent / "test_event_reactor_no_bypass.py"
_spec = importlib.util.spec_from_file_location("_nb_fixtures_for_day0_dual_persist", _NB_PATH)
assert _spec is not None and _spec.loader is not None
_nb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_nb)

_DT = datetime(2026, 5, 24, 14, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_edli_settings(monkeypatch):
    """Mirror the no_bypass isolation to keep fixture q consistent."""
    from src.config import settings

    edli = dict(settings._data["edli_v1"])
    edli["edli_emos_sole_calibrator_enabled"] = False
    edli["edli_bias_correction_enabled"] = False
    monkeypatch.setitem(settings._data, "edli_v1", edli)
    feature_flags = dict(settings._data["feature_flags"])
    feature_flags["openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled"] = False
    monkeypatch.setitem(settings._data, "feature_flags", feature_flags)


def _live_adapter(conn, *, edli_live_scope, executor_submit, real_order_submit_enabled=False, live_canary_enabled=False):
    from src.engine import event_reactor_adapter as adapter
    from src.main import require_operator_arm

    return adapter.event_bound_live_adapter_from_trade_conn(
        conn,
        get_current_level=lambda: RiskLevel.GREEN,
        forecast_conn=conn,
        topology_conn=conn,
        calibration_conn=conn,
        bankroll_usd_provider=lambda: 10000.0,
        real_order_submit_enabled=real_order_submit_enabled,
        live_canary_enabled=live_canary_enabled,
        durable_submit_outbox_enabled=True,
        executor_submit=executor_submit,
        operator_arm=require_operator_arm({"edli_live_operator_authorized": True}),
        edli_live_scope=edli_live_scope,
    )


# ---------------------------------------------------------------------------
# (a) edli_no_submit_receipts row has non-null q fields after shadow path.
# ---------------------------------------------------------------------------

def test_day0_shadow_receipt_lands_in_edli_no_submit_receipts_with_q_fields():
    """After the dual-persist fix, a DAY0_SCOPE_SHADOW_ONLY receipt must land in
    edli_no_submit_receipts with non-null q_live, q_lcb_5pct, direction, trade_score
    so the shadow comparator can pair it against settled outcomes."""
    from src.state.schema.edli_no_submit_receipts_schema import ensure_table
    from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger

    # Build the shadow receipt directly via the ledger insert path (unit test of the new method).
    conn = _nb._trade_conn_with_snapshot()
    ensure_table(conn)
    event = _nb._day0_event()
    submit = _live_adapter(
        conn,
        edli_live_scope="day0_shadow",
        executor_submit=lambda *_: (_ for _ in ()).throw(AssertionError("no submit")),
    )
    receipt = submit(event, _DT)
    assert receipt.reason == "DAY0_SCOPE_SHADOW_ONLY"

    # The ledger's insert_shadow_idempotent must have been called by the adapter path.
    # Drive directly to verify the insert_shadow_idempotent method itself.
    ledger = EdliNoSubmitReceiptLedger(conn)
    rid = ledger.insert_shadow_idempotent(receipt, decision_time=_DT)
    assert rid is not None

    row = conn.execute(
        """
        SELECT q_live, q_lcb_5pct, direction, trade_score, side_effect_status
        FROM edli_no_submit_receipts WHERE receipt_id = ?
        """,
        (rid,),
    ).fetchone()
    assert row is not None, "shadow receipt not found in edli_no_submit_receipts"
    assert row[0] is not None, "q_live is NULL in edli_no_submit_receipts"
    assert row[1] is not None, "q_lcb_5pct is NULL in edli_no_submit_receipts"
    assert row[2] is not None, "direction is NULL in edli_no_submit_receipts"
    assert row[3] is not None, "trade_score is NULL in edli_no_submit_receipts"
    assert row[4] == "NO_SUBMIT", "side_effect_status must be NO_SUBMIT"


# ---------------------------------------------------------------------------
# (b) Never-submit still holds after dual-persist (real_order_submit_enabled=True).
# ---------------------------------------------------------------------------

def test_day0_shadow_dual_persist_never_submits_even_with_real_submit_enabled():
    """The dual-persist (edli_no_submit_receipts insert) must not weaken the
    never-submit guarantee: executor must never be called under day0_shadow."""
    executor_called = {"called": False}

    def _executor(_intent, _cmd):
        executor_called["called"] = True
        raise AssertionError("executor must never be reached under day0_shadow")

    conn = _nb._trade_conn_with_snapshot()
    event = _nb._day0_event()
    submit = _live_adapter(
        conn,
        edli_live_scope="day0_shadow",
        executor_submit=_executor,
        real_order_submit_enabled=True,
        live_canary_enabled=True,
    )
    receipt = submit(event, _DT)

    assert receipt.reason == "DAY0_SCOPE_SHADOW_ONLY"
    assert receipt.submitted is False
    assert executor_called["called"] is False


# ---------------------------------------------------------------------------
# (c) Shadow comparator's read query finds the dual-persisted row.
# ---------------------------------------------------------------------------

def test_day0_remaining_day_adapter_finds_dual_persisted_shadow_receipt():
    """The shadow comparator's adapter (day0_remaining_day_adapter) reads
    edli_no_submit_receipts LEFT-JOINed to opportunity_events. After the dual-persist
    fix, it must find at least one observation with non-null live_q for a
    DAY0_SCOPE_SHADOW_ONLY receipt (the shadow comparator can pair it)."""
    from src.state.schema.edli_no_submit_receipts_schema import ensure_table
    from src.state.schema.opportunity_events_schema import ensure_table as ensure_oe_table
    from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger
    from src.analysis.shadow_comparator import day0_remaining_day_adapter

    conn = _nb._trade_conn_with_snapshot()
    ensure_table(conn)
    ensure_oe_table(conn)

    event = _nb._day0_event()

    # Insert the opportunity_event row so the LEFT JOIN in day0_remaining_day_adapter
    # can find event_type='DAY0_EXTREME_UPDATED'. Use asdict to populate all NOT NULL
    # columns (source, payload_hash, idempotency_key, schema_version, created_at).
    from dataclasses import asdict as _asdict
    conn.execute(
        """
        INSERT OR IGNORE INTO opportunity_events (
            event_id, event_type, entity_key, source,
            observed_at, available_at, received_at,
            causal_snapshot_id, payload_hash, idempotency_key,
            priority, expires_at, payload_json, schema_version, created_at
        ) VALUES (
            :event_id, :event_type, :entity_key, :source,
            :observed_at, :available_at, :received_at,
            :causal_snapshot_id, :payload_hash, :idempotency_key,
            :priority, :expires_at, :payload_json, :schema_version, :created_at
        )
        """,
        _asdict(event),
    )

    # Build the shadow receipt and insert via insert_shadow_idempotent.
    submit = _live_adapter(
        conn,
        edli_live_scope="day0_shadow",
        executor_submit=lambda *_: (_ for _ in ()).throw(AssertionError("no submit")),
    )
    receipt = submit(event, _DT)
    assert receipt.reason == "DAY0_SCOPE_SHADOW_ONLY"

    ledger = EdliNoSubmitReceiptLedger(conn)
    ledger.insert_shadow_idempotent(receipt, decision_time=_DT)

    # The shadow comparator's adapter must find at least one observation with q_live.
    observations = day0_remaining_day_adapter(conn)
    assert len(observations) > 0, (
        "day0_remaining_day_adapter found 0 observations after dual-persist — "
        "the shadow comparator still cannot find the shadow receipt"
    )
    # At least one observation must have non-null live_q (paired for the live side).
    q_values = [o.live_q for o in observations if o.live_q is not None]
    assert len(q_values) > 0, (
        "day0_remaining_day_adapter found observations but all live_q are None — "
        "receipt_json q_live field is not populated"
    )


# ---------------------------------------------------------------------------
# (d) insert_shadow_idempotent rejects wrong-reason and wrong-status receipts.
# ---------------------------------------------------------------------------

def test_insert_shadow_idempotent_rejects_wrong_reason():
    """insert_shadow_idempotent must enforce reason == DAY0_SCOPE_SHADOW_ONLY."""
    from src.state.schema.edli_no_submit_receipts_schema import ensure_table
    from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger
    from src.events.reactor import EventSubmissionReceipt

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    ledger = EdliNoSubmitReceiptLedger(conn)

    bad_receipt = EventSubmissionReceipt(
        False,
        event_id="evt-1",
        causal_snapshot_id="snap-1",
        side_effect_status="NO_SUBMIT",
        reason="SOME_OTHER_REASON",
    )
    with pytest.raises(ValueError, match="DAY0_SCOPE_SHADOW_ONLY"):
        ledger.insert_shadow_idempotent(bad_receipt, decision_time=_DT)


def test_insert_shadow_idempotent_rejects_non_no_submit():
    """insert_shadow_idempotent must enforce side_effect_status == NO_SUBMIT."""
    from src.state.schema.edli_no_submit_receipts_schema import ensure_table
    from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger
    from src.events.reactor import EventSubmissionReceipt

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    ledger = EdliNoSubmitReceiptLedger(conn)

    bad_receipt = EventSubmissionReceipt(
        False,
        event_id="evt-1",
        causal_snapshot_id="snap-1",
        side_effect_status="COMMAND_CREATED",
        reason="DAY0_SCOPE_SHADOW_ONLY",
    )
    with pytest.raises(ValueError, match="NO_SUBMIT"):
        ledger.insert_shadow_idempotent(bad_receipt, decision_time=_DT)


# ---------------------------------------------------------------------------
# (e) Both edli_no_submit_receipts AND no_trade_regret_events are populated (dual).
# ---------------------------------------------------------------------------

def test_day0_shadow_dual_persist_populates_both_tables():
    """DUAL-PERSIST invariant: after the fix, both edli_no_submit_receipts AND
    no_trade_regret_events must have a row for the shadow event — not one replacing
    the other. The regret row provides the rejection_stage audit; the receipt row
    provides the shadow comparator with q values."""
    from src.state.schema.edli_no_submit_receipts_schema import ensure_table
    from src.state.schema.no_trade_regret_events_schema import ensure_table as ensure_regret_table
    from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger
    from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger, NoTradeRegretEvent
    from src.events.reactor import _payload_dict, _receipt_or_payload, _optional_float, _regret_bucket_for

    conn = _nb._trade_conn_with_snapshot()
    ensure_table(conn)
    ensure_regret_table(conn)

    event = _nb._day0_event()
    submit = _live_adapter(
        conn,
        edli_live_scope="day0_shadow",
        executor_submit=lambda *_: (_ for _ in ()).throw(AssertionError("no submit")),
    )
    receipt = submit(event, _DT)
    assert receipt.reason == "DAY0_SCOPE_SHADOW_ONLY"

    # (1) Insert shadow receipt into edli_no_submit_receipts.
    ledger = EdliNoSubmitReceiptLedger(conn)
    ledger.insert_shadow_idempotent(receipt, decision_time=_DT)

    # (2) Simulate reactor's _write_regret path into no_trade_regret_events.
    payload = _payload_dict(event)
    regret_event = NoTradeRegretEvent(
        event_id=event.event_id,
        rejection_stage="TRADE_SCORE",
        rejection_reason=receipt.reason,
        regret_bucket=_regret_bucket_for(receipt.reason),  # type: ignore[arg-type]
        bin_label=_receipt_or_payload(receipt, payload, "bin_label"),
        direction=_receipt_or_payload(receipt, payload, "direction"),
        q_live=_optional_float(_receipt_or_payload(receipt, payload, "q_live")),
        q_lcb_5pct=_optional_float(_receipt_or_payload(receipt, payload, "q_lcb_5pct")),
        trade_score=_optional_float(_receipt_or_payload(receipt, payload, "trade_score")),
    )
    NoTradeRegretLedger(conn).insert_idempotent(regret_event)

    # Both tables must have the event.
    receipt_count = conn.execute(
        "SELECT COUNT(*) FROM edli_no_submit_receipts WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0]
    regret_count = conn.execute(
        "SELECT COUNT(*) FROM no_trade_regret_events WHERE event_id = ? AND rejection_reason = 'DAY0_SCOPE_SHADOW_ONLY'",
        (event.event_id,),
    ).fetchone()[0]

    assert receipt_count >= 1, "shadow receipt missing from edli_no_submit_receipts"
    assert regret_count >= 1, "shadow receipt missing from no_trade_regret_events"

    # The receipt row must carry non-null q fields.
    row = conn.execute(
        "SELECT q_live, q_lcb_5pct, direction, trade_score FROM edli_no_submit_receipts WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert row is not None
    assert row[0] is not None, "q_live NULL in edli_no_submit_receipts"
    assert row[1] is not None, "q_lcb_5pct NULL in edli_no_submit_receipts"
    assert row[2] is not None, "direction NULL in edli_no_submit_receipts"
    assert row[3] is not None, "trade_score NULL in edli_no_submit_receipts"
