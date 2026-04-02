#!/usr/bin/env python3
"""Audit that Zeus time semantics are migrated, populated, and load-bearing."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.signal.diurnal import build_day0_temporal_context
from src.state.db import get_connection


def run_audit() -> dict:
    conn = get_connection()

    counts = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM observation_instants) AS observation_instants,
            (SELECT COUNT(*) FROM solar_daily) AS solar_daily,
            (SELECT COUNT(*) FROM diurnal_curves) AS diurnal_curves,
            (SELECT COUNT(*) FROM diurnal_peak_prob) AS diurnal_peak_prob,
            (SELECT SUM(dst_active) FROM observation_instants) AS dst_active_rows,
            (SELECT SUM(is_ambiguous_local_hour) FROM observation_instants) AS ambiguous_rows,
            (SELECT SUM(is_missing_local_hour) FROM observation_instants) AS missing_rows
        """
    ).fetchone()

    city_breakdown = [
        dict(r)
        for r in conn.execute(
            """
            SELECT city, COUNT(*) AS rows, SUM(dst_active) AS dst_rows,
                   SUM(is_ambiguous_local_hour) AS ambiguous_rows
            FROM observation_instants
            GROUP BY city
            ORDER BY city
            """
        ).fetchall()
    ]

    source_density = [
        dict(r)
        for r in conn.execute(
            """
            SELECT city,
                   AVG(source_count) AS avg_sources_per_hour,
                   MIN(source_count) AS min_sources_per_hour,
                   MAX(source_count) AS max_sources_per_hour
            FROM (
              SELECT city, target_date, substr(local_timestamp, 1, 13) AS local_hour_bucket,
                     COUNT(DISTINCT source) AS source_count
              FROM observation_instants
              WHERE is_ambiguous_local_hour = 0 AND is_missing_local_hour = 0
              GROUP BY city, target_date, local_hour_bucket
            )
            GROUP BY city
            ORDER BY city
            """
        ).fetchall()
    ]

    transition_samples = [
        dict(r)
        for r in conn.execute(
            """
            SELECT city, target_date, source, local_timestamp, utc_timestamp,
                   utc_offset_minutes, dst_active, is_ambiguous_local_hour
            FROM observation_instants
            WHERE is_ambiguous_local_hour = 1
               OR target_date IN ('2025-03-09', '2025-03-30', '2025-10-26', '2025-11-02')
            ORDER BY city, target_date, utc_timestamp
            LIMIT 40
            """
        ).fetchall()
    ]
    conn.close()

    contexts = []
    for city, target_date, timezone_name, observation_time in [
        ("NYC", "2025-03-09", "America/New_York", "2025-03-09T07:30:00+00:00"),
        ("London", "2025-10-26", "Europe/London", "2025-10-26T00:30:00+00:00"),
        ("Tokyo", "2026-03-31", "Asia/Tokyo", "2026-03-30T18:30:00+00:00"),
    ]:
        ctx = build_day0_temporal_context(
            city,
            date.fromisoformat(target_date),
            timezone_name,
            observation_time=observation_time,
            observation_source="audit_probe",
        )
        contexts.append(
            {
                "city": city,
                "target_date": target_date,
                "context_built": ctx is not None,
                "current_local_timestamp": ctx.current_local_timestamp.isoformat() if ctx else None,
                "current_utc_timestamp": ctx.current_utc_timestamp.isoformat() if ctx else None,
                "dst_active": ctx.dst_active if ctx else None,
                "utc_offset_minutes": ctx.utc_offset_minutes if ctx else None,
                "time_basis": ctx.time_basis if ctx else None,
                "phase": str(ctx.phase) if ctx else None,
            }
        )

    return {
        "counts": dict(counts),
        "city_breakdown": city_breakdown,
        "source_density": source_density,
        "transition_samples": transition_samples,
        "day0_temporal_context_probes": contexts,
    }


if __name__ == "__main__":
    print(json.dumps(run_audit(), ensure_ascii=False, indent=2))
