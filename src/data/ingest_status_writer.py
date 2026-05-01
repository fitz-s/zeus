# Created: 2026-04-30
# Last reused/audited: 2026-04-30
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §2.5
"""Ingest status rollup writer — Phase 2 ingest improvement.

Queries data_coverage + observation_instants + forecasts + solar_daily +
ensemble_snapshots and computes a summary JSON written to state/ingest_status.json.

Writer cadence (per design SC-4):
  - Every K2 tick completion calls write_ingest_status.
  - A dedicated _ingest_status_rollup_tick runs every 5 minutes.
  - Whichever fires first wins (both are wrapped in acquire_lock("ingest_status")).

Reader contract: poll at most every 30s.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows_in_period(conn, table: str, ts_col: str, hours: int) -> int:
    """Count rows written in the last N hours via ts_col (ISO string)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        cur = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {ts_col} >= ?", (cutoff,)
        )
        row = cur.fetchone()
        return row[0] if row else 0
    except Exception as exc:
        logger.debug("rows_in_period %s.%s failed: %s", table, ts_col, exc)
        return -1


def _holes_by_city_count(conn, data_table: str) -> dict[str, int]:
    """Count MISSING rows per city for a given data_table."""
    try:
        cur = conn.execute(
            """
            SELECT city, COUNT(*) as cnt
            FROM data_coverage
            WHERE data_table = ? AND status = 'MISSING'
            GROUP BY city
            ORDER BY cnt DESC
            """,
            (data_table,),
        )
        return {r[0]: r[1] for r in cur.fetchall()}
    except Exception as exc:
        logger.debug("holes_by_city_count for %s failed: %s", data_table, exc)
        return {}


def _last_quarantine_reason(conn) -> str | None:
    """Return the most recent quarantine reason from availability_fact."""
    try:
        cur = conn.execute(
            """
            SELECT failure_type, details_json
            FROM availability_fact
            WHERE failure_type LIKE '%quarantine%' OR failure_type = 'QUARANTINED'
            ORDER BY started_at DESC LIMIT 1
            """
        )
        row = cur.fetchone()
        if row:
            return f"{row[0]}: {row[1]}" if row[1] else row[0]
    except Exception:
        pass
    # Fallback: check data_coverage FAILED reason
    try:
        cur = conn.execute(
            """
            SELECT reason FROM data_coverage
            WHERE status = 'FAILED' AND reason IS NOT NULL
            ORDER BY fetched_at DESC LIMIT 1
            """
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return None


def _read_source_health(state_dir: Path | None = None) -> dict | None:
    """Read source_health.json if available."""
    if state_dir is None:
        try:
            from src.config import state_path
            path = state_path("source_health.json")
        except Exception:
            return None
    else:
        path = state_dir / "source_health.json"

    try:
        if Path(path).exists():
            return json.loads(Path(path).read_text())
    except Exception as exc:
        logger.debug("Could not read source_health.json: %s", exc)
    return None


def write_ingest_status(
    world_conn,
    *,
    state_dir: Path | None = None,
) -> None:
    """Query DB tables and write state/ingest_status.json atomically.

    Tables queried (all in world_conn / zeus-world.db):
      - observation_instants (ts_col: utc_timestamp)
      - forecasts            (ts_col: imported_at)
      - solar_daily          (ts_col: fetched_at)
      - ensemble_snapshots   (ts_col: fetch_time)
      - data_coverage        (holes count per table)
    """
    if state_dir is None:
        from src.config import state_path
        out_path = state_path("ingest_status.json")
    else:
        out_path = state_dir / "ingest_status.json"

    # Per-table row counts
    table_stats: dict[str, dict[str, Any]] = {}

    # observation_instants
    table_stats["observation_instants"] = {
        "rows_last_hour": _rows_in_period(world_conn, "observation_instants", "utc_timestamp", 1),
        "rows_last_day": _rows_in_period(world_conn, "observation_instants", "utc_timestamp", 24),
        "holes_by_city_count": _holes_by_city_count(world_conn, "observation_instants"),
    }

    # forecasts
    # imported_at may be null for old rows; use captured_at as fallback
    table_stats["forecasts"] = {
        "rows_last_hour": _rows_in_period(world_conn, "forecasts", "imported_at", 1),
        "rows_last_day": _rows_in_period(world_conn, "forecasts", "imported_at", 24),
        "holes_by_city_count": _holes_by_city_count(world_conn, "forecasts"),
    }

    # solar_daily — check if fetched_at column exists
    table_stats["solar_daily"] = {
        "rows_last_hour": _rows_in_period(world_conn, "solar_daily", "fetched_at", 1),
        "rows_last_day": _rows_in_period(world_conn, "solar_daily", "fetched_at", 24),
        "holes_by_city_count": _holes_by_city_count(world_conn, "solar_daily"),
    }

    # ensemble_snapshots
    table_stats["ensemble_snapshots"] = {
        "rows_last_hour": _rows_in_period(world_conn, "ensemble_snapshots", "fetch_time", 1),
        "rows_last_day": _rows_in_period(world_conn, "ensemble_snapshots", "fetch_time", 24),
        "holes_by_city_count": {},  # ensemble_snapshots not in data_coverage
    }

    # observations (daily observations — uses fetched_at from data_coverage proxy)
    table_stats["observations"] = {
        "rows_last_hour": -1,  # no reliable ts column in observations table
        "rows_last_day": -1,
        "holes_by_city_count": _holes_by_city_count(world_conn, "observations"),
    }

    last_quarantine = _last_quarantine_reason(world_conn)
    source_health = _read_source_health(state_dir)

    payload = {
        "written_at": _now_iso(),
        "tables": table_stats,
        "last_quarantine_reason": last_quarantine,
        "source_health_written_at": source_health.get("written_at") if source_health else None,
        "source_health_summary": {
            src: {
                "consecutive_failures": data.get("consecutive_failures", 0),
                "degraded_since": data.get("degraded_since"),
                "latency_ms": data.get("latency_ms"),
            }
            for src, data in (source_health.get("sources", {}) if source_health else {}).items()
        },
    }

    tmp = Path(str(out_path) + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(out_path)
    logger.info("ingest_status.json written")
