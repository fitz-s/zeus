# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md §4.4 (Path D natural-key antibody, v3); PR-T1-B activation

"""
Antibody test: INV-decision-events-completeness (natural-key, no ATTACH)

Invariant: for every decision-tagged forecast in the last 7 days (ensemble_snapshots_v2
WHERE causality_status='OK'), decision_events must carry at least one row keyed by
the matching natural tuple (market_slug, temperature_metric, target_date).

v3 changes from v2:
- Join key is market_slug (NOT market_id or condition_id).
  condition_id excluded because market_events_v2.condition_id is nullable
  (pre-discovery markets) — SQL "= NULL" would be silent failure.
- Uses get_world_connection_read_only() and get_forecasts_connection_read_only()
  thin wrappers added in PR-T1-A (= get_*_connection(write_class=None)).

Cross-module relationship test (Fitz §3 invariant pattern):
  forecasts.ensemble_snapshots_v2 → forecasts.market_events_v2 (city→market_slug)
  → world.decision_events (natural-key lookup by market_slug)

Independent read connections — INV-37 trivially honored (no ATTACH path).
Non-empty precondition: skip (not fail) if no decision-tagged forecasts in window.

Strict-pass: xfail removed per PR-T1-B; T1 production complete.
"""

import sqlite3

import pytest

from src.state.db import (
    ZEUS_FORECASTS_DB_PATH,
    ZEUS_WORLD_DB_PATH,
    get_forecasts_connection_read_only,
    get_world_connection_read_only,
)


def test_inv_decision_events_completeness_natural_key() -> None:
    """Cross-module: every decision-tagged forecast (7d, causality_status='OK')
    in ensemble_snapshots_v2 must have >= 1 decision_events row keyed by
    (market_slug, temperature_metric, target_date).

    market_slug join (NOT condition_id — per ultraplan v3 §4.4 critic-round-2 SEV-1).
    Independent read connections (INV-37 trivially honored — no ATTACH).
    city→market_slug resolved Python-side via market_events_v2.
    pytest.skip (not fail) if no candidates in 7d window.

    See PHASE_1_ULTRAPLAN.md §4.4 for full pseudocode.
    """
    # Skip if production DBs are absent (worktree isolation; no data in test env)
    if not ZEUS_FORECASTS_DB_PATH.exists() or not ZEUS_WORLD_DB_PATH.exists():
        pytest.skip(
            "production DBs absent in this environment — "
            "non-degenerate antibody test requires live state"
        )

    forecasts = get_forecasts_connection_read_only()
    world = get_world_connection_read_only()

    try:
        candidates = forecasts.execute(
            """
            SELECT city, target_date, temperature_metric, available_at
            FROM ensemble_snapshots_v2
            WHERE recorded_at >= datetime('now', '-7 days')
              AND causality_status = 'OK'
            """
        ).fetchall()

        # Resolve (city, target_date, metric) → market_slug via market_events_v2.
        # market_slug is the durable non-null identifier. condition_id is nullable
        # (pre-discovery markets) — excluded from join key (critic round 2 SEV-1).
        slug_map = {
            (r["city"], r["target_date"], r["temperature_metric"]): r["market_slug"]
            for r in forecasts.execute(
                """
                SELECT city, target_date, temperature_metric, market_slug
                FROM market_events_v2
                WHERE market_slug IS NOT NULL
                """
            ).fetchall()
        }

        misses = []
        for c in candidates:
            key = (c["city"], c["target_date"], c["temperature_metric"])
            if key not in slug_map:
                continue  # no market = no decision possible
            market_slug = slug_map[key]
            n = world.execute(
                """
                SELECT COUNT(*) FROM decision_events
                WHERE market_slug = ?
                  AND temperature_metric = ?
                  AND target_date = ?
                """,
                (market_slug, c["temperature_metric"], c["target_date"]),
            ).fetchone()[0]
            if n == 0:
                misses.append((market_slug, c["target_date"], c["temperature_metric"]))

    finally:
        forecasts.close()
        world.close()

    if not candidates:
        pytest.skip("no decision-tagged forecasts in 7d window — non-degenerate test impossible")

    assert not misses, (
        f"INV-decision-events-completeness violated: "
        f"{len(misses)} decision-tagged forecasts have no decision_events row. "
        f"Sample: {misses[:5]}"
    )
