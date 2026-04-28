"""WS_OR_POLL_TIGHTENING packet — BATCH 1: K1-compliant reaction-latency projection.

Created: 2026-04-28
Last reused/audited: 2026-04-28
Authority basis: round3_verdict.md §1 #2 (R3 §3 weeks 5-12 third edge leg) +
ULTIMATE_PLAN.md L312-314 ("reactive WS lets Zeus respond faster than
competitors during opening-inertia and shoulder-bin entry windows").
ATTRIBUTION_DRIFT packet's measurement-substrate-first pattern repeated.

K1 contract (mirrors src/state/edge_observation.py + src/state/attribution_drift.py):
  - Read-only projection. NO write path. NO JSON persistence. NO caches.
  - Reads canonical surfaces directly: token_price_log (price ticks with
    source_timestamp + Zeus persist timestamp; latency = delta) JOIN
    position_current (city/target_date/strategy_key attribution).
  - Imports consolidated to top of file per Tier 2 Phase 4 LOW-CAVEAT-EO-2-1
    (cited by name above; mid-file imports with noqa are an anti-pattern).

KNOWN LIMITATIONS (per BATCH 1 boot §1 + GO_BATCH_1 PATH A operator decision):

  PATH A "latency-only" was chosen (PATH B heuristic-WS-vs-poll inference
  EXPLICITLY REJECTED per methodology §5.Z2 default-deny on
  heuristic-without-grounding; PATH C extending the writer is deferred to a
  future "WS_PROVENANCE_INSTRUMENTATION" packet that operator will
  separately authorize).

  - The detector measures END-TO-END LATENCY (Zeus persist time minus venue
    source time) but CANNOT ATTRIBUTE individual ticks to WebSocket vs
    REST poll because token_price_log lacks an `update_source` column.
  - `ws_share` and `poll_share` are NOT in the return shape. A future
    WS_PROVENANCE_INSTRUMENTATION packet that adds the upstream tag would
    unlock those fields.
  - Negative latencies (Zeus persist time BEFORE venue source time) are
    clipped to 0 ms (clock-skew defense — neither timestamp is canonical
    time; small negatives are sensor noise, large negatives indicate
    misconfigured upstream).
  - Rows with NULL source_timestamp or unparsable timestamps are excluded
    (they cannot contribute a valid latency).

Latency formula (per AGENTS.md L114-126 + boot §6 #2 PATH A):
  latency_ms = (zeus_timestamp_ms - source_timestamp_ms) per tick, clipped
               to [0, ∞). p50 + p95 reported per strategy_key over the
               window. Aggregation grouped by strategy_key via the
               token_price_log → position_current JOIN.

n_with_action: count of (strategy_key, target_date) tuples in the window
where Zeus emitted a position_events row (any event_type) within
ACTION_WINDOW_SECONDS (30s, per boot §6 #4 default) of the price tick.
This measures "did Zeus react to this signal at all" — a latency-decoupled
companion metric.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.state.edge_observation import STRATEGY_KEYS, _classify_sample_quality

# Action-window in seconds for n_with_action computation (boot §6 #4 default).
# A price tick that triggered a Zeus action within this window counts as "acted on".
ACTION_WINDOW_SECONDS: int = 30


def _empty_strategy_latency_record(strategy_key: str, window_start: str, window_end: str) -> dict[str, Any]:
    return {
        "latency_p50_ms": None,
        "latency_p95_ms": None,
        "n_signals": 0,
        "n_with_action": 0,
        "sample_quality": "insufficient",
        "window_start": window_start,
        "window_end": window_end,
    }


def _parse_iso_to_ms(ts: str | None) -> int | None:
    """Parse an ISO-8601 timestamp to ms-since-epoch. Returns None on failure
    or unparsable input. Defensive against zoneless / fractional / Z-suffix
    variants which appear across token_price_log writers."""
    if not ts:
        return None
    s = ts.strip()
    if not s:
        return None
    # Normalize trailing Z to +00:00 for fromisoformat compatibility.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    # Treat zoneless as UTC (defensive — token_price_log writers are mixed).
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    """Compute percentile (0..100) on a pre-sorted list. Linear-interpolation
    over the rank position. Returns None on empty input."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo_idx = int(rank)
    hi_idx = min(lo_idx + 1, len(sorted_values) - 1)
    frac = rank - lo_idx
    return float(sorted_values[lo_idx] + frac * (sorted_values[hi_idx] - sorted_values[lo_idx]))


def _resolve_window(window_days: int, end_date: str | None) -> tuple[str, str, datetime, datetime]:
    """Return (window_start_iso, window_end_iso, window_start_dt, window_end_dt)."""
    if window_days <= 0:
        raise ValueError(f"window_days must be positive; got {window_days}")
    if end_date is None:
        end = datetime.now(timezone.utc).date()
    else:
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    start = end - timedelta(days=window_days)
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc)
    return start.isoformat(), end.isoformat(), start_dt, end_dt


def compute_reaction_latency_per_strategy(
    conn: sqlite3.Connection,
    window_days: int = 7,
    end_date: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Compute per-strategy reaction-latency aggregation over a time window.

    K1-compliant read-only. JOIN token_price_log → position_current to
    attribute each price tick to a strategy_key, then compute per-tick
    latency and aggregate per strategy.

    Args:
        conn: open sqlite3 connection to a Zeus state DB
        window_days: window length in calendar days (default 7 = weekly)
        end_date: ISO YYYY-MM-DD inclusive end day; defaults to today UTC

    Returns:
        dict keyed by the 4 STRATEGY_KEYS (always all present). Each value:
        {
            latency_p50_ms: float | None,
            latency_p95_ms: float | None,
            n_signals: int,           # ticks with valid latency in window
            n_with_action: int,       # subset where Zeus acted within
                                      # ACTION_WINDOW_SECONDS
            sample_quality: 'insufficient' | 'low' | 'adequate' | 'high',
            window_start, window_end,
        }
    """
    window_start, window_end, window_start_dt, window_end_dt = _resolve_window(window_days, end_date)
    window_start_ms = int(window_start_dt.timestamp() * 1000)
    window_end_ms = int(window_end_dt.timestamp() * 1000)

    per_strategy: dict[str, dict[str, Any]] = {
        sk: _empty_strategy_latency_record(sk, window_start, window_end)
        for sk in STRATEGY_KEYS
    }
    latencies_by_strategy: dict[str, list[float]] = {sk: [] for sk in STRATEGY_KEYS}
    # Also collect (strategy, tick_ts_ms, position_id) so we can compute
    # n_with_action via a second pass against position_events.
    ticks_by_strategy: dict[str, list[tuple[int, str]]] = {sk: [] for sk in STRATEGY_KEYS}

    # Pull ticks within window. token_price_log has token_id; position_current
    # ALSO has token_id (architecture/2026_04_02_architecture_kernel.sql:123).
    # Strategy_key is on position_current.
    cur = conn.execute("""
        SELECT
            tpl.token_id,
            tpl.source_timestamp,
            tpl.timestamp AS zeus_timestamp,
            pc.strategy_key,
            pc.position_id
        FROM token_price_log tpl
        JOIN position_current pc ON pc.token_id = tpl.token_id
        WHERE tpl.timestamp IS NOT NULL
          AND pc.strategy_key IS NOT NULL
    """)
    for row in cur.fetchall():
        # sqlite3.Row supports both index and key access; tolerate both.
        token_id = row[0] if not hasattr(row, "keys") else row["token_id"]
        source_ts = row[1] if not hasattr(row, "keys") else row["source_timestamp"]
        zeus_ts = row[2] if not hasattr(row, "keys") else row["zeus_timestamp"]
        strategy_key = row[3] if not hasattr(row, "keys") else row["strategy_key"]
        position_id = row[4] if not hasattr(row, "keys") else row["position_id"]

        if strategy_key not in per_strategy:
            # Unknown strategy_key (legacy data) — quarantine; per AGENTS.md
            # §"strategy families" only the 4 governed keys exist.
            continue
        zeus_ms = _parse_iso_to_ms(zeus_ts)
        source_ms = _parse_iso_to_ms(source_ts)
        if zeus_ms is None or source_ms is None:
            # Cannot compute latency without both timestamps.
            continue
        if zeus_ms < window_start_ms or zeus_ms > window_end_ms:
            continue
        # Clip negative latencies to 0 (clock-skew defense per module docstring).
        latency_ms = max(0.0, float(zeus_ms - source_ms))
        latencies_by_strategy[strategy_key].append(latency_ms)
        ticks_by_strategy[strategy_key].append((zeus_ms, str(position_id)))

    # n_with_action: count ticks where a position_events row exists for
    # the same position_id within ACTION_WINDOW_SECONDS after the tick.
    action_window_ms = ACTION_WINDOW_SECONDS * 1000
    for sk, ticks in ticks_by_strategy.items():
        if not ticks:
            continue
        # Get all position_events.occurred_at for the position_ids of interest,
        # in window. Build a set of (position_id, occurred_at_ms) tuples.
        position_ids = sorted({pid for _, pid in ticks})
        if not position_ids:
            continue
        placeholders = ",".join("?" for _ in position_ids)
        ev_cur = conn.execute(
            f"SELECT position_id, occurred_at FROM position_events "
            f"WHERE position_id IN ({placeholders})",
            position_ids,
        )
        events_by_pid: dict[str, list[int]] = {}
        for ev in ev_cur.fetchall():
            pid = ev[0] if not hasattr(ev, "keys") else ev["position_id"]
            occ_ts = ev[1] if not hasattr(ev, "keys") else ev["occurred_at"]
            occ_ms = _parse_iso_to_ms(occ_ts)
            if occ_ms is None:
                continue
            events_by_pid.setdefault(str(pid), []).append(occ_ms)
        n_acted = 0
        for tick_ms, pid in ticks:
            ev_times = events_by_pid.get(pid, [])
            # Action: any position_events row at occurred_at in [tick_ms,
            # tick_ms + action_window_ms]
            if any(tick_ms <= ev_ms <= tick_ms + action_window_ms for ev_ms in ev_times):
                n_acted += 1
        per_strategy[sk]["n_with_action"] = n_acted

    # Finalize per-strategy stats.
    for sk, rec in per_strategy.items():
        latencies = latencies_by_strategy[sk]
        n = len(latencies)
        rec["n_signals"] = n
        if n > 0:
            sorted_latencies = sorted(latencies)
            rec["latency_p50_ms"] = _percentile(sorted_latencies, 50.0)
            rec["latency_p95_ms"] = _percentile(sorted_latencies, 95.0)
        rec["sample_quality"] = _classify_sample_quality(n)

    return per_strategy
