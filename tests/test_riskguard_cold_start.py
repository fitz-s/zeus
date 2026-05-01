# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: operator directive 2026-05-01 — "去掉empty outcome fact被
#   block，这本来就不是一个合理的配置，只有这种冷启动状态才会出现没有outcome
#   fact" + architect agent (aef59fd) confirmation that the deadlock is in
#   `_trailing_loss_snapshot` / risk_state lookup, not outcome_fact.
"""Antibody: riskguard must not flag fresh deploys as DATA_DEGRADED.

A fresh deploy or post-long-outage restart has either no `risk_state` rows
older than the trailing-loss lookback window, or a reference row from
before the lookback that is stale beyond tolerance. The previous behaviour
mapped both states to `DATA_DEGRADED`, which the cycle reads as
`entries_blocked_reason=risk_level=DATA_DEGRADED` and refuses to discover
or evaluate. That made every fresh deploy permanently undeployable until
someone manually seeded `risk_state` — a deadlock by design, not by intent.

When there is no usable trailing-loss reference AND there is no measurable
loss against any candidate baseline, no loss can have occurred. The right
level is `GREEN` with an explicit bootstrap annotation. `RED` is preserved
when a stale reference DOES show a loss above threshold — the staleness
tolerance is a precision concern, not a "ignore the loss" rule.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.riskguard.riskguard import _trailing_loss_snapshot, init_risk_db
from src.riskguard.risk_level import RiskLevel


@pytest.fixture
def fresh_risk_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_risk_db(conn)
    return conn


def _seed_row(conn: sqlite3.Connection, *, ts: str, initial_bankroll=200.0, total_pnl=0.0):
    """Insert a risk_state row that `_risk_state_reference_from_row` accepts."""
    import json
    effective = round(initial_bankroll + total_pnl, 2)
    conn.execute(
        "INSERT INTO risk_state (checked_at, level, details_json) VALUES (?, ?, ?)",
        (
            ts,
            "GREEN",
            json.dumps(
                {
                    "initial_bankroll": initial_bankroll,
                    "total_pnl": total_pnl,
                    "effective_bankroll": effective,
                }
            ),
        ),
    )
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_no_reference_row_returns_green_bootstrap(fresh_risk_conn):
    """Empty risk_state (truly fresh deploy) → GREEN, NOT DATA_DEGRADED."""
    snap = _trailing_loss_snapshot(
        fresh_risk_conn,
        now=_now(),
        lookback=timedelta(hours=24),
        current_equity=199.40,
        initial_bankroll=200.0,
        threshold_pct=0.10,
    )
    assert snap["level"] is RiskLevel.GREEN
    assert snap["degraded"] is False
    assert "bootstrap_no_history" in snap["status"]
    assert snap["loss"] == 0.0


def test_insufficient_history_returns_green_bootstrap(fresh_risk_conn):
    """risk_state has rows but all newer than lookback cutoff (haven't
    accumulated 24h of history yet) → GREEN."""
    now = datetime.now(timezone.utc)
    for hours_ago in (0.5, 1, 2, 3):
        _seed_row(fresh_risk_conn, ts=(now - timedelta(hours=hours_ago)).isoformat())

    snap = _trailing_loss_snapshot(
        fresh_risk_conn,
        now=now.isoformat(),
        lookback=timedelta(hours=24),
        current_equity=200.0,
        initial_bankroll=200.0,
        threshold_pct=0.10,
    )
    assert snap["level"] is RiskLevel.GREEN
    assert snap["degraded"] is False
    assert "bootstrap_no_history" in snap["status"]


def test_stale_reference_with_no_loss_returns_green(fresh_risk_conn):
    """Long unload window: reference exists but is older than (lookback +
    staleness tolerance). Cold-restart-after-long-gap is the same shape as
    cold-start; if no loss is showing, treat as GREEN bootstrap."""
    now = datetime.now(timezone.utc)
    _seed_row(fresh_risk_conn, ts=(now - timedelta(hours=36)).isoformat())

    snap = _trailing_loss_snapshot(
        fresh_risk_conn,
        now=now.isoformat(),
        lookback=timedelta(hours=24),
        current_equity=199.40,  # tiny apparent "loss" (well under 10% threshold)
        initial_bankroll=200.0,
        threshold_pct=0.10,
    )
    assert snap["level"] is RiskLevel.GREEN
    assert snap["degraded"] is False
    assert snap["status"] == "bootstrap_stale_reference"


def test_stale_reference_with_real_loss_still_red(fresh_risk_conn):
    """RED preservation: even with a stale reference, a measurable loss above
    threshold MUST surface. The staleness fix unblocks cold start; it must
    not unblock real risk."""
    now = datetime.now(timezone.utc)
    _seed_row(fresh_risk_conn, ts=(now - timedelta(hours=36)).isoformat())

    snap = _trailing_loss_snapshot(
        fresh_risk_conn,
        now=now.isoformat(),
        lookback=timedelta(hours=24),
        current_equity=150.0,  # 25% loss vs 200 baseline — well above 10% threshold
        initial_bankroll=200.0,
        threshold_pct=0.10,
    )
    assert snap["level"] is RiskLevel.RED
    assert snap["degraded"] is True


def test_inconsistent_history_still_data_degraded(fresh_risk_conn):
    """`inconsistent_history` is a data-corruption signal — rows exist but
    `_risk_state_reference_from_row` rejects all of them as malformed. That
    IS a real degradation, not a cold-start. Must remain DATA_DEGRADED."""
    import json
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(hours=36)).isoformat()
    # details_json present but missing required keys → row rejected as malformed
    fresh_risk_conn.execute(
        "INSERT INTO risk_state (checked_at, level, details_json) VALUES (?, ?, ?)",
        (ts, "GREEN", json.dumps({"corrupted": True})),
    )
    fresh_risk_conn.commit()

    snap = _trailing_loss_snapshot(
        fresh_risk_conn,
        now=now.isoformat(),
        lookback=timedelta(hours=24),
        current_equity=200.0,
        initial_bankroll=200.0,
        threshold_pct=0.10,
    )
    assert snap["level"] is RiskLevel.DATA_DEGRADED
    assert snap["degraded"] is True
    assert "inconsistent_history" in snap["status"]
