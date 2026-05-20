# Lifecycle: created=2026-05-20; last_reviewed=2026-05-20; last_reused=never
# Purpose: Antibody — validates no_trade_events reason values and mutual exclusion with decision_events.
# Reuse: Verify NoTradeReason enum completeness and cross-table natural-key collision guard.
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §5.2, §5.3 (sha 00c2399742)

"""Antibody test: INV-no-trade-events-completeness

Invariant: no_trade_events reason values must all be valid NoTradeReason
members; PK must not collide with decision_events natural key (mutual exclusion).

Cross-module relationship test:
  world.decision_events (trade taken) vs world.no_trade_events (trade skipped)
  Join key: (market_slug, temperature_metric, target_date, observation_time)
  -- exactly the DecisionNaturalKey 5-tuple (minus decision_seq).

Skips when world DB is absent (CI/paper environments). Non-degenerate only
when at least one no_trade_events row exists in the last 24h.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


LOOKBACK_HOURS = 24


def test_inv_no_trade_events_completeness() -> None:
    """Cross-module: no_trade_events row reason values must all be valid
    NoTradeReason members; PK must not collide with decision_events natural key.

    Specifically:
    1. All reason TEXT values in no_trade_events must be in {r.value for r in NoTradeReason}
    2. No (market_slug, temperature_metric, target_date, observation_time, decision_seq)
       tuple may appear in BOTH decision_events AND no_trade_events (mutual exclusion
       within a decision_seq -- a single cycle produces at most one event in one table).

    Uses direct sqlite3.connect() (read-only URI); skips if DB absent.
    xfail fires because no_trade_events does not exist until T2 production pass.
    """
    from src.contracts.no_trade_reason import NoTradeReason
    from src.state.db import ZEUS_WORLD_DB_PATH

    try:
        conn = sqlite3.connect(f"file:{ZEUS_WORLD_DB_PATH}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        pytest.skip("world DB not present in this environment -- live-only antibody")
    conn.row_factory = sqlite3.Row

    cutoff_iso = (
        datetime.now(tz=timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    ).isoformat()

    try:
        # 1. Validate reason values -- triggers OperationalError (xfail) if table absent
        no_trade_rows = conn.execute(
            """
            SELECT market_slug, temperature_metric, target_date,
                   observation_time, decision_seq, reason
            FROM no_trade_events
            WHERE observed_at >= :cutoff
            """,
            {"cutoff": cutoff_iso},
        ).fetchall()

        valid_reasons = {r.value for r in NoTradeReason}
        unknown_reasons = [
            row["reason"]
            for row in no_trade_rows
            if row["reason"] not in valid_reasons
        ]
        assert not unknown_reasons, (
            f"INV-no-trade-events-completeness: unknown reason values found: "
            f"{unknown_reasons[:10]}"
        )

        if not no_trade_rows:
            pytest.skip(
                f"no no_trade_events rows in last {LOOKBACK_HOURS}h -- "
                "non-degenerate antibody requires at least one row"
            )

        # 2. Mutual-exclusion: no natural key may appear in both tables.
        # Build set of (market_slug, temperature_metric, target_date, observation_time, decision_seq)
        # from decision_events in same window.
        decision_keys = {
            (row["market_slug"], row["temperature_metric"],
             row["target_date"], row["observation_time"], row["decision_seq"])
            for row in conn.execute(
                """
                SELECT market_slug, temperature_metric, target_date,
                       observation_time, decision_seq
                FROM decision_events
                WHERE observation_time >= :cutoff
                """,
                {"cutoff": cutoff_iso},
            ).fetchall()
        }

        no_trade_keys = {
            (row["market_slug"], row["temperature_metric"],
             row["target_date"], row["observation_time"], row["decision_seq"])
            for row in no_trade_rows
        }

        overlap = decision_keys & no_trade_keys
        assert not overlap, (
            f"INV-no-trade-events-completeness: {len(overlap)} natural keys appear in "
            f"BOTH decision_events AND no_trade_events (mutual exclusion violated):\n"
            + "\n".join(str(k) for k in list(overlap)[:5])
        )

    finally:
        conn.close()
