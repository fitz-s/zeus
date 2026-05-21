# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T3

"""shoulder_exposure_ledger — writer/reader for shoulder_exposure_ledger world DB table.

Schema lives in src/state/schema/shoulder_exposure_ledger_schema.py.
Table belongs to zeus-world.db (world DB, K1 split).

Design:
  - Append-only log of shoulder exposure entries keyed by decision_event_id + side.
  - Used by shoulder_cluster_cap.check_shoulder_cluster_cap to detect cross-city
    same-direction shoulder sell under the same weather-system cluster (plan §2 T3 G3).
  - Used by shadow_readiness_report to aggregate total exposure.

INV-37 contract:
  All write/read functions require caller-provided conn (INV-37).
  Never auto-opens a connection.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from src.state.schema.shoulder_exposure_ledger_schema import SCHEMA_VERSION


def write_shoulder_exposure_entry(
    *,
    shoulder_side: str,
    weather_system_cluster: str,
    city: str,
    target_date: str,
    source: str,
    regime: str,
    notional_usd: float,
    decision_event_id: str,
    observed_at: str,
    conn: sqlite3.Connection,
) -> None:
    """Append one shoulder exposure entry to the ledger.

    Parameters
    ----------
    shoulder_side:
        "sell" or "buy" — direction of the shoulder exposure.
    weather_system_cluster:
        Cluster ID from correlation_cluster.tail_correlation_cluster_for.
        Empty string ("") when regime is UNKNOWN — no cluster aggregation.
    city:
        City name (matches config/cities.json slug).
    target_date:
        ISO date string "YYYY-MM-DD".
    source:
        Forecast source key (e.g. "ecmwf", "gfs").
    regime:
        Weather regime tag string (WeatherRegimeTag.value).
    notional_usd:
        Proposed notional in USD for this exposure entry.
    decision_event_id:
        FK-like reference to decision_events.decision_event_id
        (documented FK, not SQL-enforced — decision_events is in same DB
        but auto-FK is not enforced in SQLite here; see plan §2 T3).
    observed_at:
        ISO-8601 UTC timestamp of the write.
    conn:
        Required world-DB connection (INV-37). Never auto-opens.

    Notes
    -----
    Append-only: does not UPDATE existing entries. Each positive decision
    produces one new ledger row. PK is AUTOINCREMENT id.
    """
    conn.execute(
        """
        INSERT INTO shoulder_exposure_ledger (
            shoulder_side, weather_system_cluster, city, target_date,
            source, regime, notional_usd, decision_event_id,
            observed_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            shoulder_side,
            weather_system_cluster,
            city,
            target_date,
            source,
            regime,
            notional_usd,
            decision_event_id,
            observed_at,
            SCHEMA_VERSION,
        ),
    )


def read_cluster_exposure(
    cluster: str,
    side: str,
    *,
    conn: sqlite3.Connection,
) -> float:
    """Return total notional_usd for a given cluster + side.

    Parameters
    ----------
    cluster:
        weather_system_cluster to aggregate.
    side:
        "sell" or "buy".
    conn:
        World-DB connection (INV-37).

    Returns
    -------
    float
        Sum of notional_usd for all entries with matching cluster + side.
        Returns 0.0 when no entries exist.
    """
    row = conn.execute(
        """
        SELECT COALESCE(SUM(notional_usd), 0.0)
        FROM shoulder_exposure_ledger
        WHERE weather_system_cluster = ? AND shoulder_side = ?
        """,
        (cluster, side),
    ).fetchone()
    return float(row[0]) if row else 0.0


def read_distinct_cities_in_cluster(
    cluster: str,
    side: str,
    *,
    conn: sqlite3.Connection,
) -> list[str]:
    """Return sorted list of distinct cities with entries for a given cluster + side.

    Used by check_shoulder_cluster_cap to detect cross-city correlation:
    if a different city already has a same-direction entry, refuse the new one.

    Parameters
    ----------
    cluster:
        weather_system_cluster to query.
    side:
        "sell" or "buy".
    conn:
        World-DB connection (INV-37).

    Returns
    -------
    list[str]
        Sorted list of distinct city names. Empty list when no entries exist.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT city
        FROM shoulder_exposure_ledger
        WHERE weather_system_cluster = ? AND shoulder_side = ?
        ORDER BY city
        """,
        (cluster, side),
    ).fetchall()
    return [r[0] for r in rows]


def read_total_exposure_usd(*, conn: sqlite3.Connection) -> float:
    """Return total notional_usd across all shoulder exposure entries.

    Used by shadow_readiness_report for aggregate portfolio shoulder exposure.

    Parameters
    ----------
    conn:
        World-DB connection (INV-37).

    Returns
    -------
    float
        Sum of all notional_usd entries in the ledger. Returns 0.0 if empty.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(notional_usd), 0.0) FROM shoulder_exposure_ledger"
    ).fetchone()
    return float(row[0]) if row else 0.0
