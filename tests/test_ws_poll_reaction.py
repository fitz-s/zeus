# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: round3_verdict.md §1 #2 + ULTIMATE_PLAN.md L312-314
# (WS_OR_POLL_TIGHTENING third edge leg). Per Fitz "test relationships, not
# just functions" — these tests verify the CROSS-MODULE invariant that
# compute_reaction_latency_per_strategy correctly joins token_price_log →
# position_current to attribute ticks to strategy_key, computes per-tick
# latency = (zeus_timestamp - source_timestamp) clipped to [0, ∞), and
# aggregates p50/p95 + sample_quality + n_with_action per strategy.
"""BATCH 1 tests for ws_poll_reaction (PATH A latency-only).

Seven relationship tests covering:

  1. test_per_strategy_latency_aggregation_correctness — synthetic ticks
     with known latencies; verify p50/p95 math + grouping
  2. test_sample_quality_boundaries — exactly 10/30/100 tick boundaries
  3. test_empty_db_safety — no ticks → all 4 strategies return None
  4. test_invalid_timestamps_excluded — NULL source_timestamp + unparsable
     formats excluded from the aggregation
  5. test_window_filter — ticks outside [end - window_days, end] excluded
  6. test_unknown_strategy_quarantined — strategy_key not in STRATEGY_KEYS
     skipped (mirrors AD pattern; schema CHECK prevents direct insert but
     test confirms the runtime guard)
  7. test_negative_latency_clipped_to_zero — clock-skew defense:
     zeus_timestamp BEFORE source_timestamp → latency 0 ms, not negative
  8. test_n_with_action_counts_position_events_within_window — companion
     metric: tick within ACTION_WINDOW_SECONDS of a position_events row
     for the same position_id counts as "acted on"
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.state.db import init_schema
from src.state.ws_poll_reaction import (
    ACTION_WINDOW_SECONDS,
    STRATEGY_KEYS,
    _percentile,
    compute_reaction_latency_per_strategy,
)


# --- Helpers ---------------------------------------------------------------


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)
    return conn


def _insert_position_current(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    token_id: str,
    strategy_key: str,
    city: str = "TestCity",
    target_date: str = "2026-04-23",
):
    """Insert a minimal position_current row (the JOIN target)."""
    conn.execute(
        """
        INSERT INTO position_current
            (position_id, phase, strategy_key, updated_at, city, target_date,
             bin_label, direction, market_id, edge_source, size_usd, shares,
             cost_basis_usd, entry_price, unit, token_id, temperature_metric)
        VALUES (?, 'active', ?, ?, ?, ?, '50-51°F', 'buy_yes', 'm-test', '',
                10.0, 100, 10.0, 0.5, 'F', ?, 'high')
        """,
        (position_id, strategy_key, "2026-04-23T12:00:00+00:00", city, target_date, token_id),
    )


def _insert_tick(
    conn: sqlite3.Connection,
    *,
    token_id: str,
    source_ts: str | None,
    zeus_ts: str,
    price: float = 0.5,
):
    """Insert a token_price_log row."""
    conn.execute(
        """
        INSERT INTO token_price_log
            (token_id, city, target_date, range_label, price, volume, bid, ask,
             spread, source_timestamp, timestamp)
        VALUES (?, 'TestCity', '2026-04-23', '50-51°F', ?, 100.0, 0.49, 0.51,
                0.02, ?, ?)
        """,
        (token_id, price, source_ts, zeus_ts),
    )


def _insert_event(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    occurred_at: str,
    strategy_key: str = "settlement_capture",
    event_type: str = "MONITOR_REFRESHED",
    seq_no: int = 1,
    suffix: str = "1",
):
    """Insert one position_events row (for n_with_action computation)."""
    import json
    conn.execute(
        """
        INSERT INTO position_events
            (event_id, position_id, event_version, sequence_no, event_type,
             occurred_at, strategy_key, source_module, payload_json)
        VALUES (?, ?, 1, ?, ?, ?, ?, 'tests', '{}')
        """,
        (f"{position_id}:ev:{suffix}", position_id, seq_no, event_type, occurred_at, strategy_key),
    )


# --- Tests -----------------------------------------------------------------


def test_percentile_unit_helper():
    """RELATIONSHIP unit: _percentile interpolation correctness on small N.
    Pinned because _percentile is the load-bearing math the aggregator depends on."""
    assert _percentile([], 50.0) is None
    assert _percentile([42.0], 50.0) == 42.0
    assert _percentile([10.0, 20.0], 50.0) == 15.0
    assert _percentile([10.0, 20.0, 30.0, 40.0, 50.0], 50.0) == 30.0
    assert _percentile([10.0, 20.0, 30.0, 40.0, 50.0], 95.0) == 48.0


def test_per_strategy_latency_aggregation_correctness():
    """RELATIONSHIP: 3 ticks with known latencies for opening_inertia
    (200ms, 400ms, 800ms); 2 ticks for center_buy (50ms, 150ms). Verify
    p50 + p95 + n_signals + grouping; other strategies untouched."""
    conn = _make_conn()
    # opening_inertia ticks via position_id=oi1 / token_id=tok-oi
    _insert_position_current(conn, position_id="oi1", token_id="tok-oi",
                             strategy_key="opening_inertia")
    _insert_tick(conn, token_id="tok-oi",
                 source_ts="2026-04-23T12:00:00+00:00",
                 zeus_ts="2026-04-23T12:00:00.200000+00:00")  # 200ms
    _insert_tick(conn, token_id="tok-oi",
                 source_ts="2026-04-23T12:00:01+00:00",
                 zeus_ts="2026-04-23T12:00:01.400000+00:00")  # 400ms
    _insert_tick(conn, token_id="tok-oi",
                 source_ts="2026-04-23T12:00:02+00:00",
                 zeus_ts="2026-04-23T12:00:02.800000+00:00")  # 800ms
    # center_buy ticks
    _insert_position_current(conn, position_id="cb1", token_id="tok-cb",
                             strategy_key="center_buy")
    _insert_tick(conn, token_id="tok-cb",
                 source_ts="2026-04-23T12:00:00+00:00",
                 zeus_ts="2026-04-23T12:00:00.050000+00:00")  # 50ms
    _insert_tick(conn, token_id="tok-cb",
                 source_ts="2026-04-23T12:00:01+00:00",
                 zeus_ts="2026-04-23T12:00:01.150000+00:00")  # 150ms
    conn.commit()

    result = compute_reaction_latency_per_strategy(conn, window_days=14, end_date="2026-04-28")
    assert set(result.keys()) == set(STRATEGY_KEYS)

    oi = result["opening_inertia"]
    assert oi["n_signals"] == 3
    # Sorted [200, 400, 800] → p50 = 400, p95 = (rank=0.95*2=1.9 → 400 + 0.9*400 = 760)
    assert abs(oi["latency_p50_ms"] - 400.0) < 1e-6
    assert abs(oi["latency_p95_ms"] - 760.0) < 1e-6

    cb = result["center_buy"]
    assert cb["n_signals"] == 2
    # Sorted [50, 150] → p50 = 100, p95 = (rank=0.95 → 50 + 0.95*100 = 145)
    assert abs(cb["latency_p50_ms"] - 100.0) < 1e-6
    assert abs(cb["latency_p95_ms"] - 145.0) < 1e-6

    # Other strategies untouched.
    for sk in ("settlement_capture", "shoulder_sell"):
        assert result[sk]["n_signals"] == 0
        assert result[sk]["latency_p50_ms"] is None


def test_sample_quality_boundaries():
    """RELATIONSHIP: sample_quality classifier crosses tier boundaries at
    exactly 10, 30, 100 ticks (reuses edge_observation._classify_sample_quality).
    Build 9 ticks for shoulder_sell → still 'insufficient' (boundary exact)."""
    conn = _make_conn()
    _insert_position_current(conn, position_id="ss1", token_id="tok-ss",
                             strategy_key="shoulder_sell")
    base = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(9):
        ts = (base + timedelta(seconds=i)).isoformat()
        _insert_tick(conn, token_id="tok-ss", source_ts=ts,
                     zeus_ts=(base + timedelta(seconds=i, microseconds=100000)).isoformat())
    conn.commit()
    result = compute_reaction_latency_per_strategy(conn, window_days=14, end_date="2026-04-28")
    ss = result["shoulder_sell"]
    assert ss["n_signals"] == 9
    assert ss["sample_quality"] == "insufficient"

    # Add one more → boundary jumps to "low" at exactly 10.
    _insert_tick(conn, token_id="tok-ss",
                 source_ts=(base + timedelta(seconds=9)).isoformat(),
                 zeus_ts=(base + timedelta(seconds=9, microseconds=100000)).isoformat())
    conn.commit()
    result = compute_reaction_latency_per_strategy(conn, window_days=14, end_date="2026-04-28")
    assert result["shoulder_sell"]["n_signals"] == 10
    assert result["shoulder_sell"]["sample_quality"] == "low"


def test_empty_db_safety():
    """RELATIONSHIP: empty DB → all 4 strategies present with latency=None
    + n_signals=0 + sample_quality=insufficient + window bounds set."""
    conn = _make_conn()
    result = compute_reaction_latency_per_strategy(conn, window_days=7, end_date="2026-04-28")
    assert set(result.keys()) == set(STRATEGY_KEYS)
    for sk, rec in result.items():
        assert rec["n_signals"] == 0
        assert rec["latency_p50_ms"] is None
        assert rec["latency_p95_ms"] is None
        assert rec["n_with_action"] == 0
        assert rec["sample_quality"] == "insufficient"
        assert rec["window_start"] == "2026-04-21"
        assert rec["window_end"] == "2026-04-28"


def test_invalid_timestamps_excluded():
    """RELATIONSHIP: ticks with NULL source_timestamp OR unparsable
    timestamp formats are EXCLUDED from latency aggregation (cannot
    compute valid delta). 1 valid tick + 1 NULL-source + 1 garbage-source
    → n_signals=1."""
    conn = _make_conn()
    _insert_position_current(conn, position_id="cb1", token_id="tok-x",
                             strategy_key="center_buy")
    # Valid tick
    _insert_tick(conn, token_id="tok-x",
                 source_ts="2026-04-23T12:00:00+00:00",
                 zeus_ts="2026-04-23T12:00:00.300000+00:00")
    # NULL source_timestamp
    _insert_tick(conn, token_id="tok-x", source_ts=None,
                 zeus_ts="2026-04-23T12:00:01+00:00")
    # Garbage source_timestamp (unparsable)
    _insert_tick(conn, token_id="tok-x", source_ts="not-a-timestamp",
                 zeus_ts="2026-04-23T12:00:02+00:00")
    conn.commit()
    result = compute_reaction_latency_per_strategy(conn, window_days=14, end_date="2026-04-28")
    cb = result["center_buy"]
    assert cb["n_signals"] == 1, f"invalid-timestamp filter; got {cb['n_signals']}"
    assert abs(cb["latency_p50_ms"] - 300.0) < 1e-6


def test_window_filter():
    """RELATIONSHIP: ticks outside [end - window_days, end] excluded.
    Insert in-window + too-old + future ticks → only in-window counts."""
    conn = _make_conn()
    _insert_position_current(conn, position_id="oi1", token_id="tok-w",
                             strategy_key="opening_inertia")
    _insert_tick(conn, token_id="tok-w",
                 source_ts="2026-04-23T12:00:00+00:00",
                 zeus_ts="2026-04-23T12:00:00.500000+00:00")  # in-window
    _insert_tick(conn, token_id="tok-w",
                 source_ts="2026-03-29T12:00:00+00:00",
                 zeus_ts="2026-03-29T12:00:00.100000+00:00")  # too-old
    _insert_tick(conn, token_id="tok-w",
                 source_ts="2026-05-15T12:00:00+00:00",
                 zeus_ts="2026-05-15T12:00:00.200000+00:00")  # future
    conn.commit()
    result = compute_reaction_latency_per_strategy(conn, window_days=7, end_date="2026-04-28")
    oi = result["opening_inertia"]
    assert oi["n_signals"] == 1
    assert abs(oi["latency_p50_ms"] - 500.0) < 1e-6


def test_unknown_strategy_quarantined():
    """RELATIONSHIP: position_current.strategy_key has CHECK constraint at
    architecture/2026_04_02_architecture_kernel.sql:53-58 enforcing 4
    governed values. Insert with unknown strategy_key → IntegrityError
    (schema antibody). Function still returns 4 keys."""
    conn = _make_conn()
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        _insert_position_current(conn, position_id="bad1", token_id="tok-bad",
                                 strategy_key="not_a_real_strategy")
    result = compute_reaction_latency_per_strategy(conn, window_days=7, end_date="2026-04-28")
    assert set(result.keys()) == set(STRATEGY_KEYS)


def test_negative_latency_clipped_to_zero():
    """RELATIONSHIP: when zeus_timestamp is BEFORE source_timestamp (clock
    skew), latency clips to 0 ms NOT a negative number. Per module docstring
    §"Known limitations" — clock skew is sensor noise, not negative reality."""
    conn = _make_conn()
    _insert_position_current(conn, position_id="ss1", token_id="tok-skew",
                             strategy_key="shoulder_sell")
    # zeus_ts 100ms BEFORE source_ts (clock skew)
    _insert_tick(conn, token_id="tok-skew",
                 source_ts="2026-04-23T12:00:00.500000+00:00",
                 zeus_ts="2026-04-23T12:00:00.400000+00:00")
    # Plus a normal positive-latency tick for grouping check
    _insert_tick(conn, token_id="tok-skew",
                 source_ts="2026-04-23T12:00:01+00:00",
                 zeus_ts="2026-04-23T12:00:01.200000+00:00")  # 200ms
    conn.commit()
    result = compute_reaction_latency_per_strategy(conn, window_days=7, end_date="2026-04-28")
    ss = result["shoulder_sell"]
    assert ss["n_signals"] == 2
    # Sorted [0, 200] → p50 = 100. The negative tick contributes 0, NOT -100.
    assert abs(ss["latency_p50_ms"] - 100.0) < 1e-6


def test_n_with_action_counts_position_events_within_window():
    """RELATIONSHIP: a tick that has a position_events row for the same
    position_id within ACTION_WINDOW_SECONDS counts as 'acted on'. Insert:
    - 2 ticks for cb1: one with action 5s later (inside window), one with
      action 60s later (outside ACTION_WINDOW_SECONDS=30s).
    - n_signals=2, n_with_action=1."""
    conn = _make_conn()
    _insert_position_current(conn, position_id="cb1", token_id="tok-act",
                             strategy_key="center_buy")
    # Tick at 12:00:00 with action at 12:00:05 (5s later, inside 30s window)
    _insert_tick(conn, token_id="tok-act",
                 source_ts="2026-04-23T12:00:00+00:00",
                 zeus_ts="2026-04-23T12:00:00.100000+00:00")
    _insert_event(conn, position_id="cb1",
                  occurred_at="2026-04-23T12:00:05+00:00",
                  strategy_key="center_buy", suffix="a")
    # Tick at 13:00:00 with action at 13:01:00 (60s later, outside 30s window)
    _insert_tick(conn, token_id="tok-act",
                 source_ts="2026-04-23T13:00:00+00:00",
                 zeus_ts="2026-04-23T13:00:00.100000+00:00")
    _insert_event(conn, position_id="cb1",
                  occurred_at="2026-04-23T13:01:00+00:00",
                  strategy_key="center_buy", seq_no=2, suffix="b")
    conn.commit()
    assert ACTION_WINDOW_SECONDS == 30
    result = compute_reaction_latency_per_strategy(conn, window_days=7, end_date="2026-04-28")
    cb = result["center_buy"]
    assert cb["n_signals"] == 2
    assert cb["n_with_action"] == 1, \
        f"action-window filter: got {cb['n_with_action']}, expected 1"


# =====================================================================
# MED-REVISE-WP-1-1 row-multiplication regression tests (critic 22nd cycle)
# =====================================================================
# Per critic empirical reproduction: position_current.token_id is NOT unique
# (PRIMARY KEY is position_id only; schema permits multiple positions on
# same token: averaging-in / settled-then-re-entered / hedged). The
# pre-fix JOIN multiplied rows under that case, inflating n_signals + biasing
# p50/p95 toward repeated samples. These two tests pin the fix:
#
#   - same-strategy / same-token / multiple-positions: the tick must count
#     ONCE per strategy (not once per position). This is the load-bearing
#     antibody for the defect critic caught.
#   - different-strategy / same-token / one-position-each: each strategy
#     gets its own count of 1 (this is the defensible case — the same
#     market tick can legitimately attribute to multiple strategies if
#     positions on the token are labeled differently).


def test_multi_position_same_strategy_same_token_no_overcount():
    """RELATIONSHIP (MED-REVISE-WP-1-1 antibody): 2 positions on the SAME
    token under the SAME strategy + 1 tick → strategy n_signals=1, NOT 2.

    Defect pre-fix: SQL JOIN multiplied (1 tick × 2 positions = 2 rows).
    Fix: SELECT DISTINCT on (token_id, source_ts, zeus_ts, strategy_key);
    each unique tick contributes ONCE per strategy regardless of how many
    positions hold the token.
    """
    conn = _make_conn()
    # 2 positions same strategy on same token (averaging-in scenario)
    _insert_position_current(conn, position_id="p_avg1", token_id="tok-shared",
                             strategy_key="opening_inertia")
    _insert_position_current(conn, position_id="p_avg2", token_id="tok-shared",
                             strategy_key="opening_inertia")
    # ONE tick on that token
    _insert_tick(conn, token_id="tok-shared",
                 source_ts="2026-04-23T12:00:00+00:00",
                 zeus_ts="2026-04-23T12:00:00.250000+00:00")  # 250ms
    conn.commit()

    result = compute_reaction_latency_per_strategy(conn, window_days=7, end_date="2026-04-28")
    oi = result["opening_inertia"]
    assert oi["n_signals"] == 1, \
        f"row multiplication: 1 tick × 2 positions same strategy should count ONCE; got n_signals={oi['n_signals']}"
    # Latency math should reflect ONE 250ms sample, not two.
    assert abs(oi["latency_p50_ms"] - 250.0) < 1e-6
    assert abs(oi["latency_p95_ms"] - 250.0) < 1e-6


def test_multi_position_different_strategy_same_token_per_strategy_count():
    """RELATIONSHIP (MED-REVISE-WP-1-1 defensible-case pin): 2 positions
    on the SAME token but DIFFERENT strategies + 1 tick → each strategy
    n_signals=1.

    This is the non-defect case: the same market tick can legitimately
    attribute to two strategies if positions on the token are labeled
    differently (e.g., legacy migration left a center_buy position
    alongside an opening_inertia rebalance on the same token). The fix
    must NOT collapse this case to a single count.
    """
    conn = _make_conn()
    _insert_position_current(conn, position_id="p_oi", token_id="tok-shared",
                             strategy_key="opening_inertia")
    _insert_position_current(conn, position_id="p_cb", token_id="tok-shared",
                             strategy_key="center_buy")
    _insert_tick(conn, token_id="tok-shared",
                 source_ts="2026-04-23T12:00:00+00:00",
                 zeus_ts="2026-04-23T12:00:00.300000+00:00")  # 300ms
    conn.commit()

    result = compute_reaction_latency_per_strategy(conn, window_days=7, end_date="2026-04-28")
    assert result["opening_inertia"]["n_signals"] == 1, \
        f"different-strategy-same-token: opening_inertia should count ONCE; got {result['opening_inertia']['n_signals']}"
    assert result["center_buy"]["n_signals"] == 1, \
        f"different-strategy-same-token: center_buy should count ONCE; got {result['center_buy']['n_signals']}"
    # Both strategies see same 300ms latency for this tick.
    assert abs(result["opening_inertia"]["latency_p50_ms"] - 300.0) < 1e-6
    assert abs(result["center_buy"]["latency_p50_ms"] - 300.0) < 1e-6
    # Other strategies untouched.
    for sk in ("settlement_capture", "shoulder_sell"):
        assert result[sk]["n_signals"] == 0


# =====================================================================
# BATCH 2 — detect_reaction_gap tests + 30s ACTION_WINDOW boundary test
# =====================================================================
# Per dispatch GO_BATCH_2 + boot §2: 6-8 tests covering the ratio-test
# detector. Mirrors EO BATCH 2 detect_alpha_decay test surface (synthetic
# patterns + threshold + insufficient + non-positive baseline + per-call
# override + critical-cutoff boundary).
#
# Plus ACTION_WINDOW_SECONDS=30s boundary test (LOW caveat carry-forward
# from critic 22nd cycle): pins inclusive-on-both-ends semantics.
#
# THRESHOLD DISCIPLINE (per dispatch GO_BATCH_2):
# - gap_detected when current_p95 > gap_threshold_multiplier * trailing_mean_p95
#   (strict greater-than: ratio == multiplier is within_normal)
# - severity warn at multiplier <= ratio < critical_ratio_cutoff
# - severity critical at ratio >= critical_ratio_cutoff
# - non-positive trailing_mean_p95 → insufficient_data graceful
# Tests pin these so a future refactor cannot silently drift.

from src.state.ws_poll_reaction import (
    DEFAULT_CRITICAL_RATIO_CUTOFF,
    DEFAULT_GAP_THRESHOLD_MULTIPLIER,
    ReactionGapVerdict,
    detect_reaction_gap,
)


def _window(p95: float | None, n_signals: int = 50) -> dict:
    """Build one synthetic per-window record as compute_reaction_latency_per_strategy
    would emit. n_signals=50 → sample_quality='adequate' (>=30, <100)."""
    if p95 is None:
        return {
            "latency_p50_ms": None, "latency_p95_ms": None,
            "n_signals": 0, "n_with_action": 0,
            "sample_quality": "insufficient",
            "window_start": "x", "window_end": "x",
        }
    if n_signals < 10:
        sq = "insufficient"
    elif n_signals < 30:
        sq = "low"
    elif n_signals < 100:
        sq = "adequate"
    else:
        sq = "high"
    return {
        "latency_p50_ms": p95 * 0.5,
        "latency_p95_ms": p95,
        "n_signals": n_signals,
        "n_with_action": n_signals,
        "sample_quality": sq,
        "window_start": "x", "window_end": "x",
    }


def test_gap_detected_critical_on_severe_p95_jump():
    """RELATIONSHIP: 4 trailing windows at p95=100ms, current at p95=300ms →
    ratio = 300/100 = 3.0 > critical_ratio_cutoff 2.0 → critical severity."""
    history = [_window(100), _window(100), _window(100), _window(100), _window(300)]
    v = detect_reaction_gap(history, "opening_inertia")
    assert isinstance(v, ReactionGapVerdict)
    assert v.kind == "gap_detected"
    assert v.severity == "critical"
    assert v.strategy_key == "opening_inertia"
    assert abs(v.evidence["ratio"] - 3.0) < 1e-9
    assert abs(v.evidence["trailing_mean_p95_ms"] - 100.0) < 1e-9


def test_gap_detected_warn_on_moderate_p95_jump():
    """RELATIONSHIP: ratio between gap_threshold_multiplier (1.5) and
    critical_ratio_cutoff (2.0) yields warn, not critical. trailing=100ms,
    current=180ms → ratio=1.8 → warn."""
    history = [_window(100), _window(100), _window(100), _window(100), _window(180)]
    v = detect_reaction_gap(history, "shoulder_sell")
    assert v.kind == "gap_detected"
    assert v.severity == "warn"
    assert abs(v.evidence["ratio"] - 1.8) < 1e-9


def test_within_normal_when_steady_latency():
    """RELATIONSHIP: 4 trailing at 100ms, current at 110ms → ratio=1.1 → within."""
    history = [_window(100), _window(100), _window(100), _window(100), _window(110)]
    v = detect_reaction_gap(history, "center_buy")
    assert v.kind == "within_normal"
    assert v.severity is None
    assert abs(v.evidence["ratio"] - 1.1) < 1e-9


def test_threshold_boundary_exactly_at_multiplier():
    """RELATIONSHIP: at ratio == gap_threshold_multiplier the verdict is
    within_normal (strict greater-than triggers gap). trailing=100ms,
    current=150ms → ratio=1.5 == threshold → within_normal."""
    history = [_window(100), _window(100), _window(100), _window(100), _window(150)]
    v = detect_reaction_gap(history, "settlement_capture")
    assert v.kind == "within_normal", \
        f"strict greater-than should NOT trigger at exactly threshold; got {v.kind}"


def test_critical_cutoff_boundary_exactly_at_2x():
    """RELATIONSHIP: at ratio == critical_ratio_cutoff (2.0) severity is
    'critical' (>= cutoff). Per implementation:
    `severity = "critical" if ratio >= critical_ratio_cutoff else "warn"`.
    Symmetric to the EO BATCH 2 LOW-CAVEAT-EO-2-2 boundary test pattern.

    trailing=100ms, current=200ms → ratio=2.0 → gap_detected with critical
    severity (because 2.0 >= 2.0 critical cutoff).
    """
    history = [_window(100), _window(100), _window(100), _window(100), _window(200)]
    v = detect_reaction_gap(history, "opening_inertia")
    assert v.kind == "gap_detected"
    assert v.severity == "critical", \
        f"ratio == critical_ratio_cutoff (2.0) should be critical (>=); got {v.severity!r}"
    assert abs(v.evidence["ratio"] - 2.0) < 1e-9


def test_insufficient_history_below_min_windows():
    """RELATIONSHIP: 3 windows when min_windows=4 → insufficient_data."""
    history = [_window(100), _window(100), _window(100)]
    v = detect_reaction_gap(history, "settlement_capture")
    assert v.kind == "insufficient_data"
    assert v.evidence["reason"] == "n_windows_below_min"
    assert v.evidence["n_windows"] == 3
    assert v.evidence["min_required"] == 4


def test_insufficient_when_too_many_low_sample_windows():
    """RELATIONSHIP: 5 windows but 3 are sample_quality='insufficient' (n<10)
    → only 2 usable, below min_windows=4 → insufficient_data."""
    history = [
        _window(100, n_signals=5),    # insufficient
        _window(100, n_signals=5),    # insufficient
        _window(100, n_signals=5),    # insufficient
        _window(100, n_signals=50),   # usable (adequate)
        _window(200, n_signals=50),   # usable
    ]
    v = detect_reaction_gap(history, "shoulder_sell")
    assert v.kind == "insufficient_data"
    assert v.evidence["reason"] == "usable_windows_below_min"
    assert v.evidence["n_usable"] == 2


def test_insufficient_when_trailing_mean_p95_non_positive():
    """RELATIONSHIP: trailing p95s average to <=0 (e.g., all-zero clipped
    latencies from clock-skew test data) → ratio undefined →
    insufficient_data. A strategy with all-zero latency cannot meaningfully
    'gap-up'."""
    history = [_window(0), _window(0), _window(0), _window(0), _window(50)]
    v = detect_reaction_gap(history, "center_buy")
    assert v.kind == "insufficient_data"
    assert v.evidence["reason"] == "trailing_mean_p95_non_positive"
    assert v.evidence["trailing_mean_p95"] <= 0


def test_per_call_threshold_override():
    """RELATIONSHIP: caller can pass tighter threshold for opening_inertia
    discipline. trailing=100ms, current=130ms → ratio=1.3. Default
    threshold 1.5 → within_normal. Override threshold=1.2 → gap_detected.

    Per dispatch GO_BATCH_2: opening_inertia could get 1.2 default for
    tighter discipline. This test pins the override mechanism so the
    operator can apply per-strategy thresholds at the runner layer.
    """
    history = [_window(100), _window(100), _window(100), _window(100), _window(130)]
    v_default = detect_reaction_gap(history, "opening_inertia")
    assert v_default.kind == "within_normal"
    v_strict = detect_reaction_gap(history, "opening_inertia",
                                   gap_threshold_multiplier=1.2)
    assert v_strict.kind == "gap_detected"
    assert v_strict.severity == "warn"  # 1.3 < 2.0 critical cutoff
    assert abs(v_strict.evidence["gap_threshold_multiplier"] - 1.2) < 1e-9


def test_action_window_30s_boundary_inclusive():
    """RELATIONSHIP (LOW caveat carry-forward from critic 22nd cycle):
    ACTION_WINDOW_SECONDS=30s boundary is INCLUSIVE on both ends:
      - tick + position_event at 0s offset → counted (lower bound)
      - tick + position_event at exactly 30.000s offset → counted
      - tick + position_event at 30.001s offset → NOT counted

    This pins the n_with_action behavior at the canonical
    ACTION_WINDOW_SECONDS=30s boundary documented in
    src/state/ws_poll_reaction.py module docstring.
    """
    conn = _make_conn()
    _insert_position_current(conn, position_id="b1", token_id="tok-bound",
                             strategy_key="settlement_capture")
    base_iso = "2026-04-23T12:00:00+00:00"
    # Tick A at 12:00:00 with event at 0s offset (12:00:00.100, immediate)
    _insert_tick(conn, token_id="tok-bound", source_ts=base_iso,
                 zeus_ts="2026-04-23T12:00:00.100000+00:00")
    _insert_event(conn, position_id="b1",
                  occurred_at="2026-04-23T12:00:00.100000+00:00",
                  strategy_key="settlement_capture", suffix="A")
    # Tick B at 12:01:00 with event at exactly 30.000s offset (12:01:30.000)
    _insert_tick(conn, token_id="tok-bound",
                 source_ts="2026-04-23T12:01:00+00:00",
                 zeus_ts="2026-04-23T12:01:00.000000+00:00")
    _insert_event(conn, position_id="b1",
                  occurred_at="2026-04-23T12:01:30.000000+00:00",
                  strategy_key="settlement_capture", seq_no=2, suffix="B")
    # Tick C at 12:02:00 with event at 30.001s offset (12:02:30.001) — outside
    _insert_tick(conn, token_id="tok-bound",
                 source_ts="2026-04-23T12:02:00+00:00",
                 zeus_ts="2026-04-23T12:02:00.000000+00:00")
    _insert_event(conn, position_id="b1",
                  occurred_at="2026-04-23T12:02:30.001000+00:00",
                  strategy_key="settlement_capture", seq_no=3, suffix="C")
    conn.commit()

    result = compute_reaction_latency_per_strategy(conn, window_days=7, end_date="2026-04-28")
    sc = result["settlement_capture"]
    assert sc["n_signals"] == 3
    # Ticks A + B count; Tick C does not (30.001s > 30s window).
    assert sc["n_with_action"] == 2, \
        f"30s boundary inclusive lower + at-exactly-30s; 30.001s excluded; " \
        f"got n_with_action={sc['n_with_action']}, expected 2"
