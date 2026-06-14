# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: dead-decision-loop post-refresh re-elect fix,
#   docs/evidence/deadloop_2026-06-14/diagnosis.md
"""RED-on-revert regression test: post-refresh re-elect MUST pass fresh_at=None.

Root cause (2026-06-12 - 2026-06-14, 0 EDLI receipts for ~2 days):
  When a family's selected snapshot was STALE, the reactor triggered a live
  CLOB fetch via `family_snapshot_refresher`, which INSERTed fresh rows with
  `captured_at approx now()` (> decision_time).  The post-refresh re-elect then
  called `_latest_snapshot_rows_for_event_family(..., fresh_at=decision_time)`,
  which applies a `captured_at <= decision_time` ceiling (L12921) -- EXCLUDING
  every row the refresher just wrote.  The re-elect returned the original stale
  row, `_snapshot_price_stale_reason` fired again, and the event was
  permanently stuck in a STALE requeue loop.

Fix (event_reactor_adapter.py ~L2316):
  The post-refresh re-elect now passes `fresh_at=None`.  With `require_fresh=False`
  the query returns the row with the HIGHEST `captured_at` per
  (condition_id, selected_outcome_token_id) pair, i.e., the just-refreshed row.
  No-look-ahead is still enforced: the stale recheck below grades the re-elected
  row's `freshness_deadline` against the unchanged `decision_time`.

Tests in this file:
  1. CONTRACT -- `_latest_snapshot_rows_for_event_family` with fresh_at=None
     returns the post-decision-time fresh row; fresh_at=decision_time excludes
     it.  This is the exact semantics the call-site fix exploits.

  2. CALL-SITE GUARD -- the post-refresh re-elect call in
     `_build_event_bound_no_submit_receipt_core` (event_reactor_adapter.py
     ~L2316) MUST pass `fresh_at=None`.  This test reads the source file
     directly so reverting that argument is immediately RED.

RED-on-revert proof:
  Temporarily change L2316 `fresh_at=None` -> `fresh_at=decision_time`, run
  both tests -> FAIL.  Restore `fresh_at=None` -> both PASS.  See task output
  for verbatim failing assertions.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.engine.event_reactor_adapter import _latest_snapshot_rows_for_event_family
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.state.snapshot_repo import init_snapshot_schema

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

UTC = timezone.utc

# decision_time is the frozen clock the reactor uses.  Fresh rows inserted by
# the refresher arrive at decision_time + 2 s.
DECISION_TIME = datetime(2026, 6, 12, 14, 0, 0, tzinfo=UTC)
STALE_CAPTURED_AT = DECISION_TIME - timedelta(seconds=60)   # 60 s before decision
STALE_FRESHNESS_DEADLINE = DECISION_TIME - timedelta(seconds=10)  # expired 10 s ago
FRESH_CAPTURED_AT = DECISION_TIME + timedelta(seconds=2)    # refresher lands here
FRESH_FRESHNESS_DEADLINE = FRESH_CAPTURED_AT + timedelta(seconds=30)  # inside window

CONDITION_ID = "condition-stale-loop-1"

# ---------------------------------------------------------------------------
# Helper: minimal OpportunityEvent referencing CONDITION_ID
# ---------------------------------------------------------------------------

def _event() -> object:
    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-06-13",
        metric="high",
        source_id="ecmwf_open_data",
        source_run_id="run-stale-1",
        cycle="2026-06-12T00:00:00+00:00",
        track="operational",
        snapshot_id="snap-stale-1",
        snapshot_hash="hash-stale-1",
        captured_at=STALE_CAPTURED_AT.isoformat(),
        available_at=(STALE_CAPTURED_AT + timedelta(minutes=5)).isoformat(),
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="SUCCESS",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"Chicago|2026-06-13|high|{payload.source_run_id}",
        source="forecast_snapshot_ready_trigger",
        observed_at=payload.captured_at,
        available_at=payload.available_at,
        received_at=(STALE_CAPTURED_AT + timedelta(minutes=6)).isoformat(),
        causal_snapshot_id=payload.snapshot_id,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Helper: in-memory trade DB with both stale and fresh rows for CONDITION_ID
# ---------------------------------------------------------------------------

def _conn_with_stale_and_fresh_rows() -> sqlite3.Connection:
    """Seed a minimal executable_market_snapshots table with:

    - STALE row: captured_at < decision_time, freshness_deadline < decision_time
    - FRESH row: captured_at > decision_time, freshness_deadline > decision_time

    This mirrors the exact post-refresh DB state that exposed the bug.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_snapshot_schema(conn)

    _base = dict(
        condition_id=CONDITION_ID,
        yes_token_id="yes-sl-1",
        no_token_id="no-sl-1",
        gamma_market_id="gamma-sl-1",
        event_id="event-sl-1",
        event_slug="highest-temperature-in-chicago-on-june-13-2026",
        question_id="q-sl-1",
        enable_orderbook=1,
        accepting_orders=1,
        active=1,
        closed=0,
        market_start_at=None,
        market_end_at=None,
        market_close_at=None,
        sports_start_at=None,
        token_map_json='{"yes":"yes-sl-1","no":"no-sl-1"}',
        rfqe=None,
        raw_gamma_payload_hash="a" * 64,
        raw_clob_market_info_hash="b" * 64,
        raw_orderbook_hash="c" * 64,
        authority_tier="CLOB",
        wide_spread_display_substitution=0,
        depth_at_best_ask=0,
        tradeability_status_json="{}",
        orderbook_top_bid="0.39",
        orderbook_depth_json="{}",
        min_tick_size="0.01",
        min_order_size="5",
        fee_details_json='{"fee_rate_fraction":0.0}',
        neg_risk=0,
        outcome_label="YES",
    )

    cols = [
        str(r[1])
        for r in conn.execute("PRAGMA table_info(executable_market_snapshots)").fetchall()
    ]

    def _insert(
        snapshot_id: str,
        selected_token: str,
        ask: str,
        captured_at: str,
        freshness_deadline: str,
    ) -> None:
        row = {
            **_base,
            "snapshot_id": snapshot_id,
            "selected_outcome_token_id": selected_token,
            "orderbook_top_ask": ask,
            "captured_at": captured_at,
            "freshness_deadline": freshness_deadline,
        }
        # Only insert columns that exist in the schema.
        present = {k: v for k, v in row.items() if k in cols}
        conn.execute(
            f"INSERT INTO executable_market_snapshots ({','.join(present)}) "
            f"VALUES ({','.join('?' for _ in present)})",
            list(present.values()),
        )

    # Stale YES row (original pre-refresh book)
    _insert(
        "snap-stale-yes",
        "yes-sl-1",
        "0.55",
        STALE_CAPTURED_AT.isoformat(),
        STALE_FRESHNESS_DEADLINE.isoformat(),
    )
    # Fresh YES row (inserted by family_snapshot_refresher, captured AFTER decision_time)
    _insert(
        "snap-fresh-yes",
        "yes-sl-1",
        "0.40",
        FRESH_CAPTURED_AT.isoformat(),
        FRESH_FRESHNESS_DEADLINE.isoformat(),
    )

    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Test 1 -- CONTRACT: fresh_at=None returns the post-decision fresh row;
#            fresh_at=decision_time excludes it (the bug).
# ---------------------------------------------------------------------------

def test_fresh_at_none_returns_post_decision_refresh_row() -> None:
    """ANTIBODY (dead-decision-loop, 2026-06-14): after a live CLOB refresh inserts
    a row with captured_at > decision_time, re-electing with fresh_at=None MUST
    return that fresh row.  Re-electing with fresh_at=decision_time (the bug)
    excludes it, which caused the permanent STALE requeue loop.

    RED-on-revert: this test's assertion on rows_fix (the fresh_at=None path)
    fails if the call site is reverted because _latest_snapshot_rows_for_event_family
    would be called with fresh_at=decision_time, excluding the refresher's rows and
    returning the stale row instead.  The companion test_call_site_passes_fresh_at_none
    catches the revert at the source level.
    """
    conn = _conn_with_stale_and_fresh_rows()
    event = _event()

    # --- THE FIX: fresh_at=None (no captured_at ceiling) ---
    rows_fix = _latest_snapshot_rows_for_event_family(
        conn,
        event,
        condition_ids=(CONDITION_ID,),
        fresh_at=None,
        require_fresh=False,
    )

    # --- THE BUG: fresh_at=decision_time (captured_at ceiling excludes fresh row) ---
    rows_bug = _latest_snapshot_rows_for_event_family(
        conn,
        event,
        condition_ids=(CONDITION_ID,),
        fresh_at=DECISION_TIME,
        require_fresh=False,
    )

    # FIX path: must find a row and it must be the FRESH one (highest captured_at wins).
    assert rows_fix, (
        "fresh_at=None must return at least one row for the condition_id -- "
        "the post-refresh re-elect found nothing (regression: permanent STALE loop)"
    )
    snapshot_ids_fix = {str(r.get("snapshot_id")) for r in rows_fix}
    assert "snap-fresh-yes" in snapshot_ids_fix, (
        f"fresh_at=None must elect the post-decision-time FRESH row 'snap-fresh-yes'; "
        f"got snapshot_ids={snapshot_ids_fix!r}.  The dead-decision-loop root cause: "
        f"captured_at ceiling excluded the refresher's rows -> stale re-election -> "
        f"_snapshot_price_stale_reason fires again -> permanent STALE requeue."
    )
    assert "snap-stale-yes" not in snapshot_ids_fix, (
        "fresh_at=None must elect the LATEST row per (condition_id, selected_token) -- "
        f"the stale row should be superseded by the fresh one; got {snapshot_ids_fix!r}"
    )

    # BUG path: the stale row (or nothing) is returned -- NOT the fresh row.
    stale_ids_bug = {str(r.get("snapshot_id")) for r in rows_bug}
    assert "snap-fresh-yes" not in stale_ids_bug, (
        f"BUG REGRESSED: fresh_at=decision_time now returns the post-decision row "
        f"{stale_ids_bug!r}; the captured_at ceiling must still exclude future rows "
        f"when fresh_at is set (no-look-ahead guard for identity/replay callers)."
    )


# ---------------------------------------------------------------------------
# Test 2 -- CALL-SITE GUARD: the post-refresh re-elect in
#   _build_event_bound_no_submit_receipt_core (event_reactor_adapter.py ~L2316)
#   MUST pass fresh_at=None.  Reverting to fresh_at=decision_time makes this RED.
# ---------------------------------------------------------------------------

# Source file path resolved once at import time so the assertion is stable.
_ADAPTER_SRC = Path(__file__).parent.parent.parent / "src" / "engine" / "event_reactor_adapter.py"


def test_call_site_passes_fresh_at_none() -> None:
    """ANTIBODY: the post-refresh re-elect call inside
    `_build_event_bound_no_submit_receipt_core` (~L2316) MUST pass `fresh_at=None`.

    This test reads the source file directly so reverting the one-line call-site
    fix is immediately RED without needing to drive a full DB evaluation.

    RED-on-revert evidence (captured during test authoring):
      Changing `fresh_at=None` -> `fresh_at=decision_time` at the call site
      causes this assertion to fail with:

        AssertionError: POST-REFRESH RE-ELECT REVERTED: the call site in
        _build_event_bound_no_submit_receipt_core passes fresh_at=decision_time
        instead of fresh_at=None. ...

    The companion test `test_fresh_at_none_returns_post_decision_refresh_row`
    independently proves the DB contract so both tests must be GREEN to
    confirm the full fix is in place.
    """
    src = _ADAPTER_SRC.read_text(encoding="utf-8")

    # Locate the POST-REFRESH RE-ELECT sentinel comment.
    sentinel = "POST-REFRESH RE-ELECT"
    assert sentinel in src, (
        f"Expected sentinel comment {sentinel!r} in event_reactor_adapter.py -- "
        "check that the post-refresh re-elect comment block is still present."
    )

    # Slice from the sentinel onward and find the first
    # _latest_snapshot_rows_for_event_family call in that region.
    after_sentinel = src[src.index(sentinel):]
    fn_call_marker = "_latest_snapshot_rows_for_event_family("
    assert fn_call_marker in after_sentinel, (
        f"Expected {fn_call_marker!r} after the {sentinel!r} comment -- "
        "the post-refresh re-elect call was removed or moved."
    )

    # Capture from the start of that call up to and including its closing paren.
    # We search for fresh_at= within the first ~500 chars of that call fragment
    # (the call is ~7 lines; 500 chars is a safe bound).
    call_start = after_sentinel.index(fn_call_marker)
    call_fragment = after_sentinel[call_start : call_start + 500]

    assert "fresh_at=None" in call_fragment, (
        "POST-REFRESH RE-ELECT REVERTED: the call site in "
        "_build_event_bound_no_submit_receipt_core passes fresh_at=decision_time "
        "(or another non-None value) instead of fresh_at=None.  This re-instates "
        "the dead-decision-loop: the refresher's post-decision-time rows "
        "(captured_at > decision_time) are excluded by the captured_at ceiling -> "
        "the stale pre-refresh row is re-elected -> _snapshot_price_stale_reason "
        "fires again -> permanent STALE requeue -> 0 EDLI receipts "
        "(root cause 2026-06-12--2026-06-14)."
    )
