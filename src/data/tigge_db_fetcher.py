# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/2026-05-01_live_alpha/evidence/tigge_ingest_decision_2026-05-01.md
"""DB-backed TIGGE ensemble fetcher.

Reads ``ensemble_snapshots_v2`` and assembles a synthetic hourly grid so
that ``select_hours_for_target_date`` (which requires >= 20 hourly indices
per day) can select each calendar-day slice.

Row layout in the DB:
  Each row stores 51 floats (members_json) representing the daily aggregate
  (max or min temperature) for one (city, target_date, temperature_metric,
  lead_hours) combination.  ``lead_hours`` identifies which ECMWF run the
  row came from; when multiple runs exist for the same target_date, the row
  with the highest snapshot_id (most-recent ingest) is used.

Synthetic grid construction:
  For each distinct target_date in the DB result, 24 UTC timestamps are
  generated (YYYY-MM-DDTHH:00:00+00:00, hours 0-23).  The 51-member
  vector is broadcast across all 24 columns.  This gives a
  (51 members, 24 * N_days) array where:
    max(broadcast_row) == original_value   (correct for high-temp)
    min(broadcast_row) == original_value   (correct for low-temp)
  so ``member_maxes_for_target_date`` / ``member_mins_for_target_date``
  return exactly the stored value regardless of how many hours are selected.

Freshness:
  Only rows with ``recorded_at > now - 24h`` are considered, filtered
  further by ``authority='VERIFIED'`` and ``causality_status='OK'``.
  Returns None (not raises) when no qualifying rows are found so the
  evaluator can treat it as "no ensemble" and skip the candidate cleanly.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import numpy as np

from src.data.forecast_ingest_protocol import ForecastBundle
from src.data.forecast_source_registry import stable_payload_hash
from src.state.db import get_world_connection

if TYPE_CHECKING:
    from src.config import City

_log = logging.getLogger(__name__)

SOURCE_ID = "tigge"
AUTHORITY_TIER = "FORECAST"
_FRESHNESS_WINDOW_HOURS = 24
# Matches the data_version prefix used by both high+low variants
_TIGGE_DATA_VERSION_PREFIX = "tigge_"


def fetch_from_db(
    city: "City",
    temperature_metric: str,
    fetch_time: datetime,
    *,
    freshness_hours: int = _FRESHNESS_WINDOW_HOURS,
) -> Optional[ForecastBundle]:
    """Query ensemble_snapshots_v2 and build a ForecastBundle.

    Parameters
    ----------
    city:
        City object whose ``.name`` matches the ``city`` column in the DB.
    temperature_metric:
        ``'high'`` or ``'low'``.
    fetch_time:
        UTC datetime used as the reference for the freshness window.
    freshness_hours:
        Max age (hours) of ``recorded_at`` to accept.

    Returns
    -------
    ForecastBundle or None
        Returns None when no VERIFIED+OK rows exist within the freshness
        window, so the evaluator can skip the candidate without crashing.
    """
    cutoff = (fetch_time - timedelta(hours=freshness_hours)).isoformat()

    conn = get_world_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                target_date,
                issue_time,
                available_at,
                members_json,
                members_unit,
                snapshot_id,
                data_version
            FROM ensemble_snapshots_v2
            WHERE city = ?
              AND temperature_metric = ?
              AND authority = 'VERIFIED'
              AND causality_status = 'OK'
              AND data_version LIKE ?
              AND recorded_at > ?
              AND snapshot_id = (
                  SELECT MAX(s2.snapshot_id)
                  FROM ensemble_snapshots_v2 s2
                  WHERE s2.city = ensemble_snapshots_v2.city
                    AND s2.target_date = ensemble_snapshots_v2.target_date
                    AND s2.temperature_metric = ensemble_snapshots_v2.temperature_metric
                    AND s2.authority = 'VERIFIED'
                    AND s2.causality_status = 'OK'
                    AND s2.data_version LIKE ?
                    AND s2.recorded_at > ?
              )
            ORDER BY target_date ASC
            """,
            (
                city.name,
                temperature_metric,
                _TIGGE_DATA_VERSION_PREFIX + "%",
                cutoff,
                _TIGGE_DATA_VERSION_PREFIX + "%",
                cutoff,
            ),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        _log.warning(
            "tigge_db_fetcher: no VERIFIED rows for city=%s metric=%s within %dh",
            city.name,
            temperature_metric,
            freshness_hours,
        )
        return None

    # Build synthetic hourly grid.
    # Each target_date gets 24 timestamps (00:00..23:00 UTC).
    # The 51-member vector is broadcast across all 24 hours.
    all_times: list[str] = []
    all_member_rows: list[list[float]] = []  # will be transposed later

    latest_issue_time: Optional[str] = None
    latest_available_at: Optional[str] = None
    first_snapshot_id: Optional[int] = None

    for row in rows:
        members_raw: list[float] = json.loads(row["members_json"])
        if len(members_raw) != 51:
            _log.warning(
                "tigge_db_fetcher: city=%s target_date=%s has %d members (expected 51), skipping",
                city.name,
                row["target_date"],
                len(members_raw),
            )
            continue

        # Generate 24 hourly UTC timestamps for this calendar day
        date_str: str = row["target_date"]  # e.g. "2026-05-01"
        for hour in range(24):
            all_times.append(f"{date_str}T{hour:02d}:00:00+00:00")

        # Repeat members across 24 columns
        for _ in range(24):
            all_member_rows.append(members_raw)

        # Track provenance from the first row
        if first_snapshot_id is None:
            first_snapshot_id = row["snapshot_id"]
        # Use latest issue_time and available_at across all rows
        issue = row["issue_time"]
        if issue and (latest_issue_time is None or issue > latest_issue_time):
            latest_issue_time = issue
        avail = row["available_at"]
        if avail and (latest_available_at is None or avail > latest_available_at):
            latest_available_at = avail

    if not all_times:
        _log.warning(
            "tigge_db_fetcher: city=%s metric=%s — all rows skipped (member count mismatch)",
            city.name,
            temperature_metric,
        )
        return None

    # Shape: (n_hours, 51) → transpose → (51, n_hours)
    members_hourly = np.array(all_member_rows, dtype=np.float64).T

    # Raw payload dict that _parse_ingest_bundle will recognise via _extract_times
    raw_payload: dict = {
        "times": all_times,
        "members_hourly": members_hourly.tolist(),
        "source_id": SOURCE_ID,
        "temperature_metric": temperature_metric,
        "city": city.name,
    }

    # issue_time and available_at for the evidence contract
    run_init_utc: datetime
    if latest_issue_time:
        try:
            run_init_utc = datetime.fromisoformat(
                latest_issue_time.replace("Z", "+00:00")
            )
            if run_init_utc.tzinfo is None:
                run_init_utc = run_init_utc.replace(tzinfo=timezone.utc)
        except ValueError:
            run_init_utc = fetch_time
    else:
        run_init_utc = fetch_time

    # Parse available_at for later injection into parsed dict
    available_at_str: Optional[str] = latest_available_at

    bundle = ForecastBundle(
        source_id=SOURCE_ID,
        run_init_utc=run_init_utc,
        lead_hours=tuple(range(len(all_times))),
        captured_at=fetch_time,
        raw_payload_hash=stable_payload_hash(raw_payload),
        authority_tier=AUTHORITY_TIER,
        ensemble_members=tuple(members_hourly.tolist()),
        raw_payload=raw_payload,
    )

    # Stash provenance extras for the evaluator evidence contract.
    # _parse_ingest_bundle doesn't know about available_at; we inject it via
    # a sentinel on the raw_payload so the evaluator can find it.
    if available_at_str:
        bundle.raw_payload["available_at"] = available_at_str  # type: ignore[index]

    return bundle
