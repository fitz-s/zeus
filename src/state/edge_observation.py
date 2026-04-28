"""EDGE_OBSERVATION packet — BATCH 1: K1-compliant realized-edge projection.

Created: 2026-04-28
Last reused/audited: 2026-04-28
Authority basis: round3_verdict.md §1 #2 (FIRST edge packet) + ULTIMATE_PLAN.md
L297-301 (alpha-decay tracker per strategy_key, weekly drift assertion). The
follow-up packet explicitly deferred at src/state/strategy_tracker.py:24-28.

K1 contract (mirrors strategy_tracker.py:13-37):
  - Read-only projection. NO write path. NO JSON persistence.
  - Reads canonical event log via query_authoritative_settlement_rows
    (db.py:3429), which dedupes by trade_id and normalizes via
    _normalize_position_settlement_event (db.py:3275).
  - Skips rows with metric_ready=False (i.e., required fields like outcome
    or p_posterior actually missing). Rows missing only decision_snapshot_id
    have metric_ready=True (they are unsuitable for LEARNING / Platt re-fit
    but VALID for edge MEASUREMENT, which is what this module computes).
    See db.py:3345 metric_ready vs db.py:3344 learning_snapshot_ready.
  - History note: the deprecated JSON tracker drifted from canonical event
    log → produced phantom PnL +$210.68 vs -$13.03 actual (strategy_tracker
    L8). This module reads the canonical surface directly with NO parallel
    cache, so the same drift is structurally impossible.

Realized edge formula (per AGENTS.md L114-126 + boot §6 #2):
  edge_realized = mean(outcome_i - p_posterior_i) over rows where
                  strategy == strategy_key AND
                  settled_at falls in [end_date - window_days, end_date) AND
                  metric_ready is True AND
                  outcome and p_posterior are both not None.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from src.state.db import query_authoritative_settlement_rows

# Mirrors src/state/strategy_tracker.py:49 STRATEGIES enum (and the schema
# CHECK constraint at architecture/2026_04_02_architecture_kernel.sql:53-58).
STRATEGY_KEYS: list[str] = ["settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"]

# Sample-quality boundaries per dispatch §"BATCH 1" + boot §2.
SAMPLE_QUALITY_BOUNDARIES: dict[str, int] = {
    "insufficient": 10,   # < 10 trades
    "low": 30,            # 10 <= n < 30
    "adequate": 100,      # 30 <= n < 100
    # high: n >= 100
}


def _classify_sample_quality(n_trades: int) -> str:
    """Classify trade count into sample-quality tier."""
    if n_trades < SAMPLE_QUALITY_BOUNDARIES["insufficient"]:
        return "insufficient"
    if n_trades < SAMPLE_QUALITY_BOUNDARIES["low"]:
        return "low"
    if n_trades < SAMPLE_QUALITY_BOUNDARIES["adequate"]:
        return "adequate"
    return "high"


def _empty_strategy_record(strategy_key: str, window_start: str, window_end: str) -> dict[str, Any]:
    """Default record for a strategy with no trades in the window."""
    return {
        "edge_realized": None,
        "n_trades": 0,
        "n_wins": 0,
        "win_rate": None,
        "sample_quality": "insufficient",
        "window_start": window_start,
        "window_end": window_end,
    }


def _resolve_window(window_days: int, end_date: str | None) -> tuple[str, str]:
    """Return (window_start, window_end) as ISO YYYY-MM-DD strings (UTC).

    end_date is INCLUSIVE day; the window is [end - window_days, end] in
    calendar-day terms. The actual SQL filter is half-open via not_before.
    """
    if window_days <= 0:
        raise ValueError(f"window_days must be positive; got {window_days}")
    if end_date is None:
        end = datetime.now(timezone.utc).date()
    else:
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    start = end - timedelta(days=window_days)
    return start.isoformat(), end.isoformat()


def compute_realized_edge_per_strategy(
    conn: sqlite3.Connection,
    window_days: int = 7,
    end_date: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Compute realized edge per strategy_key over a time window.

    K1-compliant read-only projection. Reads canonical SETTLED events via
    query_authoritative_settlement_rows (which dedupes by position_id and
    normalizes via _normalize_position_settlement_event). Skips rows with
    metric_ready=False (i.e., required fields like outcome or p_posterior
    actually missing — see db.py:3345 vs is_degraded). Rows missing only
    decision_snapshot_id have metric_ready=True and ARE included; they are
    valid for edge MEASUREMENT even though not usable for Platt re-fit /
    learning. Per critic-harness BATCH 1 review (LOW-REVISE-EO-1): this
    docstring previously said "is_degraded=True" which conflicted with the
    actual implementation; metric_ready is the correct measurement-vs-
    learning split. See module docstring §K1 contract for full rationale.

    Args:
        conn: open sqlite3 connection to a Zeus state DB
        window_days: window length in calendar days (default 7 = weekly)
        end_date: ISO YYYY-MM-DD inclusive end day; defaults to today UTC

    Returns:
        dict keyed by the 4 STRATEGY_KEYS. Every key is always present.
        Each value is a dict with edge_realized, n_trades, n_wins, win_rate,
        sample_quality, window_start, window_end.
    """
    window_start, window_end = _resolve_window(window_days, end_date)

    # not_before is the SQL filter; query returns rows with occurred_at >= window_start.
    # We then post-filter on settled_at <= window_end (inclusive day end).
    # limit=None per K1 read-completeness; query already dedupes via ROW_NUMBER().
    rows = query_authoritative_settlement_rows(
        conn,
        limit=None,
        not_before=window_start,
    )

    # Initialize per-strategy accumulators.
    per_strategy: dict[str, dict[str, Any]] = {
        sk: _empty_strategy_record(sk, window_start, window_end)
        for sk in STRATEGY_KEYS
    }
    edge_sums: dict[str, float] = {sk: 0.0 for sk in STRATEGY_KEYS}

    for row in rows:
        # Per K0_frozen_kernel: metric_ready=False (required fields like
        # outcome or p_posterior missing) means the row cannot be measured.
        # Rows with only decision_snapshot_id missing still have
        # metric_ready=True (db.py:3345); they are valid for edge measurement
        # even though they cannot be used for Platt re-fit / learning.
        if not row.get("metric_ready"):
            continue
        strategy = row.get("strategy")
        if strategy not in per_strategy:
            # Unknown strategy_key (e.g., legacy data with strategy="" or some other
            # tag). Quarantine: do not include in any of the 4 buckets. Per
            # AGENTS.md §"strategy families" — strategy_key is sole governance ID;
            # only the 4 known families exist on current law.
            continue
        outcome = row.get("outcome")
        p_post = row.get("p_posterior")
        # Need both outcome (0/1) and p_posterior (probability) to compute edge.
        if outcome is None or p_post is None:
            continue
        # Window-end inclusive filter (settled_at is "YYYY-MM-DD..." or full ISO).
        settled_at = row.get("settled_at") or ""
        if settled_at[:10] > window_end:
            continue

        rec = per_strategy[strategy]
        rec["n_trades"] += 1
        edge_sums[strategy] += float(outcome) - float(p_post)
        if outcome == 1:
            rec["n_wins"] += 1

    # Finalize: compute means + win_rate + sample_quality.
    for sk, rec in per_strategy.items():
        n = rec["n_trades"]
        if n > 0:
            rec["edge_realized"] = edge_sums[sk] / n
            rec["win_rate"] = rec["n_wins"] / n
        rec["sample_quality"] = _classify_sample_quality(n)

    return per_strategy
