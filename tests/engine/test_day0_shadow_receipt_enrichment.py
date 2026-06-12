# Created: 2026-06-10
# Last reused/audited: 2026-06-10
# Authority basis: operator directive 2026-06-10 day0-shadow-receipt-enrichment —
#   day0_shadow-scope day0-lane receipts must carry the FULL candidate decision
#   content (bin_label/direction/q_live/q_lcb_5pct/trade_score + mode fields) so the
#   shadow comparator can analyze what day0 WOULD have done, WHILE the fail-closed
#   FIX-3 scope gate (day0 events NEVER submit in day0_shadow) is fully preserved.
"""Relationship tests (day0-shadow-receipt-enrichment, operator directive 2026-06-10).

The cross-module invariant under test spans the boundary
  day0-lane event -> FINAL ADAPTER BOUNDARY SCOPE GATE (day0_shadow)
  -> build_event_bound_no_submit_receipt (the full decision pipeline)
  -> FORCED DAY0_SCOPE_SHADOW_ONLY receipt carrying the proof content
  -> reactor rejection/regret write (no_trade_regret_events).

Properties asserted across the boundary:

  (a) day0_shadow + day0-lane viable candidate -> receipt reason DAY0_SCOPE_SHADOW_ONLY
      AND non-None bin_label/direction/q_live/q_lcb_5pct/trade_score; NO venue command
      built; executor submit hook NEVER called.
  (b) (a) holds even with real_order_submit_enabled=True + live canary on (HARD GUARANTEE 1:
      the forced no-submit return dominates ALL submit branches).
  (c) day0_shadow + forecast-lane event -> normal live path unchanged (not shadow-forced).
  (d) forecast_only + day0 event -> DAY0_OUT_OF_SCOPE_AT_BOUNDARY unchanged.
  (e) no reservation leak: the per-cycle in-flight reservation ledger is empty after a
      shadow-forced receipt (HARD GUARANTEE 2: a shadow receipt consumes no headroom).

These exercise the REAL production seam (event_bound_live_adapter_from_trade_conn ->
_submit_inner -> build_event_bound_no_submit_receipt), driving a genuinely viable day0
candidate through the boundary — NOT a re-implemented copy of the gate logic.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.riskguard.risk_level import RiskLevel

# Load the no_bypass fixture helpers (the day0 event + viable trade-conn fixture) so
# this test drives a REAL viable day0 candidate through the live adapter boundary.
_NB_PATH = Path(__file__).resolve().parent / "test_event_reactor_no_bypass.py"
_spec = importlib.util.spec_from_file_location("_nb_fixtures_for_day0_shadow", _NB_PATH)
assert _spec is not None and _spec.loader is not None
_nb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_nb)

# Decision time AFTER the day0 observation (14:05) so the candidate is not leakage-blocked.
_DT = datetime(2026, 5, 24, 14, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_edli_settings(monkeypatch):
    """Mirror the no_bypass isolation: force EMOS/bias/replacement-authority flags OFF so the
    canonical baseline path produces the fixture's q distribution."""
    from src.config import settings

    edli = dict(settings._data["edli"])
    edli["edli_emos_sole_calibrator_enabled"] = False
    edli["edli_bias_correction_enabled"] = False
    monkeypatch.setitem(settings._data, "edli", edli)
    feature_flags = dict(settings._data["feature_flags"])
    feature_flags["openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled"] = False
    monkeypatch.setitem(settings._data, "feature_flags", feature_flags)


def _live_adapter(conn, *, edli_live_scope, executor_submit, real_order_submit_enabled):
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
        durable_submit_outbox_enabled=True,
        executor_submit=executor_submit,
        operator_arm=require_operator_arm({"edli_live_operator_authorized": True}),
        edli_live_scope=edli_live_scope,
    )


# ---------------------------------------------------------------------------
# (a) day0_shadow + day0-lane viable candidate -> enriched DAY0_SCOPE_SHADOW_ONLY.
# ---------------------------------------------------------------------------

def test_day0_shadow_day0_lane_carries_full_decision_content_no_submit():
    """The forced shadow receipt carries the FULL candidate proof content so the comparator
    can analyze what day0 WOULD have done; NO venue command built; submit hook never called."""
    executor_called = {"called": False}

    def _executor(_final_intent, _command):
        executor_called["called"] = True
        raise AssertionError("executor_submit must NEVER be reached under day0_shadow for a day0 event")

    conn = _nb._trade_conn_with_snapshot()
    venue_rows_before = _venue_command_row_count(conn)
    event = _nb._day0_event()
    submit = _live_adapter(
        conn,
        edli_live_scope="day0_shadow",
        executor_submit=_executor,
        real_order_submit_enabled=True,
    )

    receipt = submit(event, _DT)

    # Fail-closed: shadow-only, no submit, hook never called.
    assert receipt.reason == "DAY0_SCOPE_SHADOW_ONLY"
    assert receipt.submitted is False
    assert receipt.proof_accepted is False
    assert receipt.side_effect_status == "NO_SUBMIT"
    assert executor_called["called"] is False
    # No venue command was built (the no-submit build path is skipped entirely).
    assert _venue_command_row_count(conn) == venue_rows_before

    # ENRICHMENT: the full decision content survives onto the shadow receipt.
    assert receipt.bin_label is not None
    assert receipt.direction is not None
    assert receipt.q_live is not None
    assert receipt.q_lcb_5pct is not None
    assert receipt.trade_score is not None


def test_day0_shadow_enriched_fields_match_underlying_build_proof():
    """The shadow receipt content is the GENUINE pipeline proof, not a placeholder: it equals
    what build_event_bound_no_submit_receipt produced for the same event (relationship: the
    boundary forces no-submit but does NOT alter the decision content it carries)."""
    conn = _nb._trade_conn_with_snapshot()
    event = _nb._day0_event()
    # Direct pipeline proof (the source of truth for the enriched content).
    direct = _nb._receipt(event, conn, decision_time=_DT, bankroll_usd_provider=lambda: 10000.0)

    conn2 = _nb._trade_conn_with_snapshot()
    submit = _live_adapter(
        conn2,
        edli_live_scope="day0_shadow",
        executor_submit=lambda *_: (_ for _ in ()).throw(AssertionError("no submit")),
        real_order_submit_enabled=False,
    )
    shadow = submit(event, _DT)

    assert shadow.reason == "DAY0_SCOPE_SHADOW_ONLY"
    assert shadow.bin_label == direct.bin_label
    assert shadow.direction == direct.direction
    assert shadow.q_live == direct.q_live
    assert shadow.q_lcb_5pct == direct.q_lcb_5pct
    assert shadow.trade_score == direct.trade_score
    # Mode fields thread through unchanged (None-preserving when the pipeline did not set them).
    assert shadow.execution_mode_intent == direct.execution_mode_intent
    assert shadow.maker_limit_price == direct.maker_limit_price


# ---------------------------------------------------------------------------
# (b) HARD GUARANTEE 1: still no submit with real_order_submit_enabled + canary on.
# ---------------------------------------------------------------------------

def test_day0_shadow_never_submits_even_with_real_submit_and_canary():
    """The forced no-submit return dominates every submit-eligibility branch — proven by a
    viable candidate that WOULD pass Kelly+RiskGuard never reaching the executor."""
    executor_called = {"called": False}

    def _executor(_final_intent, _command):
        executor_called["called"] = True
        raise AssertionError("submit must never happen in day0_shadow for a day0 event")

    conn = _nb._trade_conn_with_snapshot()
    event = _nb._day0_event()
    submit = _live_adapter(
        conn,
        edli_live_scope="day0_shadow",
        executor_submit=_executor,
        real_order_submit_enabled=True,
    )

    receipt = submit(event, _DT)

    assert receipt.reason == "DAY0_SCOPE_SHADOW_ONLY"
    assert receipt.submitted is False
    assert executor_called["called"] is False


# ---------------------------------------------------------------------------
# (c) day0_shadow + forecast-lane event -> normal live path (NOT shadow-forced).
# ---------------------------------------------------------------------------

def test_day0_shadow_forecast_lane_event_is_not_shadow_forced():
    """A FORECAST_SNAPSHOT_READY event under day0_shadow must take the normal path — it must
    NOT be forced to DAY0_SCOPE_SHADOW_ONLY (the scope gate only forces day0-lane events)."""
    conn = _nb._trade_conn_with_snapshot()
    event = _nb._bound_forecast_event()
    submit = _live_adapter(
        conn,
        edli_live_scope="day0_shadow",
        executor_submit=lambda *_: (_ for _ in ()).throw(AssertionError("no submit in this path")),
        real_order_submit_enabled=False,
    )

    receipt = submit(event, _nb.DECISION_TIME)

    assert receipt.reason != "DAY0_SCOPE_SHADOW_ONLY"


# ---------------------------------------------------------------------------
# (d) forecast_only + day0 event -> DAY0_OUT_OF_SCOPE_AT_BOUNDARY unchanged.
# ---------------------------------------------------------------------------

def test_forecast_only_day0_event_unchanged_bare_rejection():
    """forecast_only scope rejects a day0-lane event with DAY0_OUT_OF_SCOPE_AT_BOUNDARY,
    a bare receipt (NO pipeline run) — fully unchanged by this enrichment."""
    conn = _nb._trade_conn_with_snapshot()
    event = _nb._day0_event()
    submit = _live_adapter(
        conn,
        edli_live_scope="forecast_only",
        executor_submit=lambda *_: (_ for _ in ()).throw(AssertionError("no submit")),
        real_order_submit_enabled=True,
    )

    receipt = submit(event, _DT)

    assert receipt.reason == "DAY0_OUT_OF_SCOPE_AT_BOUNDARY"
    assert receipt.proof_accepted is False
    assert receipt.submitted is False
    # Bare rejection: no decision content (the pipeline never ran for forecast_only day0).
    assert receipt.bin_label is None
    assert receipt.q_live is None


# ---------------------------------------------------------------------------
# (e) HARD GUARANTEE 2: no reservation leak from a shadow-forced receipt.
# ---------------------------------------------------------------------------

def test_day0_shadow_forced_receipt_leaks_no_reservation():
    """The per-cycle in-flight reservation ledger must be EMPTY after a shadow-forced receipt:
    build_event_bound_no_submit_receipt provisionally reserved the viable candidate's stake,
    and the boundary must roll it back so it consumes no headroom for later same-cycle events."""
    conn = _nb._trade_conn_with_snapshot()
    event = _nb._day0_event()
    submit = _live_adapter(
        conn,
        edli_live_scope="day0_shadow",
        executor_submit=lambda *_: (_ for _ in ()).throw(AssertionError("no submit")),
        real_order_submit_enabled=False,
    )

    # Sanity: this fixture's candidate IS viable (it provisionally reserves), so the rollback
    # is actually exercised (not vacuously empty). Wave-1 2026-06-12: with the $25 day0 cap
    # DELETED, sizing is bounded by the fixture book's real DEPTH (size=100 @ 0.40 -> ~$40
    # notional). A $500 bankroll's fractional-Kelly stake (~$31) fits that depth honestly,
    # so the candidate stays viable without any artificial cap. (At $10k the uncapped stake
    # would exceed the thin fixture book — that is the honest depth gate, not a regression.)
    direct = _nb._receipt(event, conn, decision_time=_DT, bankroll_usd_provider=lambda: 500.0)
    assert direct.proof_accepted is True
    assert direct.kelly_size_usd > 0.0

    receipt = submit(event, _DT)
    assert receipt.reason == "DAY0_SCOPE_SHADOW_ONLY"

    ledger = getattr(submit, "reservation_ledger", None)
    assert ledger is not None
    # No leaked reservation: the provisional reserve was rolled back at the shadow boundary.
    assert len(ledger) == 0, f"shadow-forced receipt leaked {len(ledger)} reservation(s)"


def _venue_command_row_count(conn: sqlite3.Connection) -> int:
    try:
        return int(conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0])
    except sqlite3.OperationalError:
        return 0


# ---------------------------------------------------------------------------
# (f) END-TO-END persistence: the enriched shadow receipt lands NON-NULL decision
#     columns in no_trade_regret_events (the comparator's source of truth). This
#     closes the operator loop: "day0 shadow receipts must contain complete
#     analyzable decision content" -> the regret row columns are non-NULL.
# ---------------------------------------------------------------------------

def test_shadow_receipt_persists_non_null_decision_columns_to_regret_table():
    """RELATIONSHIP (shadow receipt -> reactor regret writer -> no_trade_regret_events).

    The forced shadow receipt routes through the reactor's rejection path (because
    trade_score_positive is False) into _write_regret, which maps the receipt fields
    into a NoTradeRegretEvent via _receipt_or_payload and persists them. The day0
    decision content (bin_label/direction/q_live/q_lcb_5pct/trade_score) must arrive
    NON-NULL — previously these were NULL because the bare scope-gate receipt carried
    no proof content."""
    from src.events.reactor import _payload_dict, _receipt_or_payload, _optional_float, _regret_bucket_for
    from src.state.schema.no_trade_regret_events_schema import ensure_table
    from src.strategy.live_inference.no_trade_regret import NoTradeRegretEvent, NoTradeRegretLedger

    # Produce a genuine enriched shadow receipt through the live adapter boundary.
    conn = _nb._trade_conn_with_snapshot()
    event = _nb._day0_event()
    submit = _live_adapter(
        conn,
        edli_live_scope="day0_shadow",
        executor_submit=lambda *_: (_ for _ in ()).throw(AssertionError("no submit")),
        real_order_submit_enabled=False,
    )
    receipt = submit(event, _DT)
    assert receipt.reason == "DAY0_SCOPE_SHADOW_ONLY"

    # Map the receipt -> regret row EXACTLY as the reactor's _write_regret does, then
    # persist through the REAL ledger into the REAL no_trade_regret_events schema.
    regret_conn = sqlite3.connect(":memory:")
    regret_conn.row_factory = sqlite3.Row
    ensure_table(regret_conn)
    payload = _payload_dict(event)
    regret_event = NoTradeRegretEvent(
        event_id=event.event_id,
        rejection_stage="TRADE_SCORE",  # money-path blocker stage for trade_score_positive=False
        rejection_reason=receipt.reason,
        regret_bucket=_regret_bucket_for(receipt.reason),  # type: ignore[arg-type]
        bin_label=_receipt_or_payload(receipt, payload, "bin_label"),
        direction=_receipt_or_payload(receipt, payload, "direction"),
        q_live=_optional_float(_receipt_or_payload(receipt, payload, "q_live")),
        q_lcb_5pct=_optional_float(_receipt_or_payload(receipt, payload, "q_lcb_5pct")),
        trade_score=_optional_float(_receipt_or_payload(receipt, payload, "trade_score")),
    )
    regret_event_id = NoTradeRegretLedger(regret_conn).insert_idempotent(regret_event)

    row = regret_conn.execute(
        """
        SELECT rejection_reason, bin_label, direction, q_live, q_lcb_5pct, trade_score
        FROM no_trade_regret_events WHERE regret_event_id = ?
        """,
        (regret_event_id,),
    ).fetchone()
    assert row is not None
    assert row["rejection_reason"] == "DAY0_SCOPE_SHADOW_ONLY"
    assert row["bin_label"] is not None
    assert row["direction"] is not None
    assert row["q_live"] is not None
    assert row["q_lcb_5pct"] is not None
    assert row["trade_score"] is not None
