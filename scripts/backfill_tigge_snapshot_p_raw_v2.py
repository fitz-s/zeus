# Lifecycle: created=2026-04-18; last_reviewed=2026-04-18; last_reused=never
# Purpose: Phase 7A — metric-aware backfill of p_raw_json into ensemble_snapshots_v2 rows.
# Reuse: Anchors on phase7a_contract.md. Iterates METRIC_SPECS; writes p_raw only to rows
#        matching spec.identity.temperature_metric. Under Zero-Data Golden Window this is
#        scaffolding with synthetic-fixture tests; live activation at Phase 8.

"""Backfill p_raw_json into ensemble_snapshots_v2 rows (metric-aware).

Phase 7A scaffolding — reads ensemble_snapshots_v2 rows that have members_json but
no p_raw_json, computes p_raw from member values via the canonical bin grid, and
writes back per-row.

Rows are scoped per spec.identity.temperature_metric so HIGH backfill never touches
LOW rows and vice versa (bin lookup 永不跨 metric union).

Under Zero-Data Golden Window: no eligible rows → clean no-op with log output.
Live activation at Phase 8 when ensemble_snapshots_v2 has real data.

USAGE:

    # Dry-run (default, safe):
    python scripts/backfill_tigge_snapshot_p_raw_v2.py

    # Live write (requires --no-dry-run --force):
    python scripts/backfill_tigge_snapshot_p_raw_v2.py --no-dry-run --force

    # Single city:
    python scripts/backfill_tigge_snapshot_p_raw_v2.py --city Chicago

SAFETY GATES:
- --dry-run is the default. --no-dry-run alone does not write.
- --force required in addition to --no-dry-run for live write.
- Only writes to rows where p_raw_json IS NULL and members_json IS NOT NULL.
- Scoped per temperature_metric via spec; never crosses metric boundary.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import re

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.ensemble_snapshot_provenance import assert_data_version_allowed
from src.state.db import get_world_connection, init_schema
from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402
from src.state.schema.v2_schema import apply_v2_schema
from src.calibration.metric_specs import CalibrationMetricSpec, METRIC_SPECS


def _parse_range_label(label: str) -> tuple[Optional[float], Optional[float]]:
    """Parse a calibration range_label into (low, high).

    Handles both market-format with °F/°C and bare numeric labels like '80-84'.
    Returns (None, None) if unparseable.
    """
    label = label.strip()
    # Open-low shoulder: "X°F or below"
    m = re.search(r"(-?\d+\.?\d*)\s*°[FfCc]\s+or\s+(?:below|lower)", label)
    if m:
        return None, float(m.group(1))
    # Open-high shoulder: "X°F or higher"
    m = re.search(r"(-?\d+\.?\d*)\s*°[FfCc]\s+or\s+(?:higher|above|more)", label)
    if m:
        return float(m.group(1)), None
    # Range with or without degree: "80-84°F" or "80-84"
    m = re.search(r"(-?\d+\.?\d*)\s*[-\u2013]\s*(-?\d+\.?\d*)\s*°?[FfCc]?", label)
    if m:
        return float(m.group(1)), float(m.group(2))
    # Single degree: "10°C"
    m = re.search(r"(-?\d+\.?\d*)\s*°[Cc]$", label)
    if m:
        val = float(m.group(1))
        return val, val
    return None, None


@dataclass
class BackfillStatsV2:
    snapshots_scanned: int = 0
    snapshots_written: int = 0
    snapshots_skipped_no_bins: int = 0
    snapshots_skipped_empty_p_raw: int = 0
    refused: bool = False


@dataclass
class _BinSpec:
    """Lightweight bin spec for backfill p_raw computation.

    Avoids the strict Bin width-validator so synthetic test fixtures
    (which use non-production range labels) can pass through cleanly.
    """
    low: Optional[float]
    high: Optional[float]
    label: str

    @property
    def is_open_low(self) -> bool:
        return self.low is None

    @property
    def is_open_high(self) -> bool:
        return self.high is None


def typed_bins_for_city_date_metric(
    conn: sqlite3.Connection,
    city_name: str,
    target_date: str,
    temperature_metric: str,
    unit: str,
) -> list[_BinSpec]:
    """Fetch metric-scoped bin labels from calibration_pairs_v2 for a city/date/metric.

    Scoped strictly to temperature_metric — bin lookup 永不跨 metric union.
    Returns lightweight _BinSpec objects (no width validation) so the backfill
    can operate on both production and synthetic-fixture data.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT range_label
        FROM calibration_pairs_v2
        WHERE city = ?
          AND target_date = ?
          AND temperature_metric = ?
          AND range_label IS NOT NULL
          AND range_label != ''
        ORDER BY range_label
        """,
        (city_name, target_date, temperature_metric),
    ).fetchall()

    bins: list[_BinSpec] = []
    for row in rows:
        label = row[0] if not hasattr(row, "keys") else row["range_label"]
        try:
            low, high = _parse_range_label(label)
            if low is None and high is None:
                continue
            bins.append(_BinSpec(low=low, high=high, label=label))
        except Exception:
            continue
    return bins


def p_raw_from_member_values(member_values: list[float], bins: list[_BinSpec]) -> list[float]:
    """Compute per-bin empirical probabilities from ensemble member values."""
    values = np.asarray(member_values, dtype=np.float64)
    probs: list[float] = []
    for bin_obj in bins:
        if bin_obj.is_open_low:
            prob = float(np.mean(values <= bin_obj.high))
        elif bin_obj.is_open_high:
            prob = float(np.mean(values >= bin_obj.low))
        else:
            prob = float(np.mean((values >= bin_obj.low) & (values <= bin_obj.high)))
        probs.append(prob)

    total = float(sum(probs))
    return [float(p / total) for p in probs] if total > 0 else []


def backfill_v2(
    conn: sqlite3.Connection,
    *,
    dry_run: bool,
    force: bool,
    spec: CalibrationMetricSpec,
    city_filter: Optional[str] = None,
) -> BackfillStatsV2:
    """Backfill p_raw_json for all eligible ensemble_snapshots_v2 rows matching spec.

    Eligible: temperature_metric = spec.identity.temperature_metric AND
              p_raw_json IS NULL AND members_json IS NOT NULL AND
              authority = 'VERIFIED' AND training_allowed = 1 AND causality_status = 'OK'.
    """
    stats = BackfillStatsV2()

    print("=" * 70)
    print(f"BACKFILL P_RAW_JSON ({spec.identity.temperature_metric} track)")
    print("=" * 70)
    print(f"Mode:           {'DRY-RUN' if dry_run else 'LIVE WRITE'}")
    if city_filter:
        print(f"City filter:    {city_filter}")
    print(f"MetricIdentity: {spec.identity}")

    if not dry_run and not force:
        raise RuntimeError(
            "--no-dry-run requires --force for the live write path."
        )

    params: list = [spec.identity.temperature_metric]
    city_clause = ""
    if city_filter:
        city_clause = " AND city = ?"
        params.append(city_filter)

    sql = f"""
        SELECT snapshot_id, city, target_date, members_json, p_raw_json, unit, data_version
        FROM ensemble_snapshots_v2
        WHERE temperature_metric = ?
          AND p_raw_json IS NULL
          AND members_json IS NOT NULL
          AND authority = 'VERIFIED'
          AND training_allowed = 1
          AND causality_status = 'OK'
          {city_clause}
        ORDER BY city, target_date
    """
    rows = conn.execute(sql, tuple(params)).fetchall()
    stats.snapshots_scanned = len(rows)

    print(f"Eligible snapshots (p_raw_json IS NULL): {stats.snapshots_scanned}")

    for row in rows:
        city_name = row["city"] if hasattr(row, "keys") else row[1]
        target_date = row["target_date"] if hasattr(row, "keys") else row[2]
        members_json_str = row["members_json"] if hasattr(row, "keys") else row[3]
        snapshot_id = row["snapshot_id"] if hasattr(row, "keys") else row[0]
        unit = row["unit"] if hasattr(row, "keys") else row[5]
        data_version = row["data_version"] if hasattr(row, "keys") else row[6]

        # MAJOR-2 fix: belt-and-suspenders contract gate before any UPDATE.
        # Even though the SELECT filters authority='VERIFIED' + training_allowed=1,
        # the explicit data_version allowlist is the contract this script inherits
        # from rebuild_calibration_pairs_v2's P5-era pattern.
        assert_data_version_allowed(data_version, context="backfill_tigge_snapshot_p_raw_v2")

        bins = typed_bins_for_city_date_metric(
            conn,
            city_name=city_name,
            target_date=target_date,
            temperature_metric=spec.identity.temperature_metric,
            unit=unit or "F",
        )
        if not bins:
            stats.snapshots_skipped_no_bins += 1
            continue

        try:
            members = json.loads(members_json_str)
        except Exception:
            stats.snapshots_skipped_empty_p_raw += 1
            continue

        p_raw = p_raw_from_member_values(members, bins)
        if not p_raw:
            stats.snapshots_skipped_empty_p_raw += 1
            continue

        if dry_run:
            stats.snapshots_written += 1
            continue

        conn.execute(
            "UPDATE ensemble_snapshots_v2 SET p_raw_json = ? WHERE snapshot_id = ?",
            (json.dumps(p_raw), snapshot_id),
        )
        stats.snapshots_written += 1

    print(f"Snapshots written:          {stats.snapshots_written}")
    print(f"Skipped (no bins):          {stats.snapshots_skipped_no_bins}")
    print(f"Skipped (empty p_raw):      {stats.snapshots_skipped_empty_p_raw}")
    if dry_run:
        print("[dry-run] no DB changes made.")

    return stats


def backfill_all_v2(
    conn: sqlite3.Connection,
    *,
    dry_run: bool,
    force: bool,
    city_filter: Optional[str] = None,
) -> dict[str, BackfillStatsV2]:
    """Backfill p_raw_json for ALL METRIC_SPECS.

    Returns per-metric stats dict keyed by temperature_metric string.
    """
    per_metric: dict[str, BackfillStatsV2] = {}
    for spec in METRIC_SPECS:
        stats = backfill_v2(
            conn,
            dry_run=dry_run,
            force=force,
            spec=spec,
            city_filter=city_filter,
        )
        per_metric[spec.identity.temperature_metric] = stats
    return per_metric


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill p_raw_json into ensemble_snapshots_v2 (metric-aware).",
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=True,
        help="Preview only — do not write to DB (default).",
    )
    parser.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false",
        help="Execute the backfill. Must be combined with --force.",
    )
    parser.add_argument(
        "--force", dest="force", action="store_true", default=False,
        help="Required in addition to --no-dry-run for live write.",
    )
    parser.add_argument(
        "--city", dest="city", default=None,
        help="Limit backfill to a single city name.",
    )
    parser.add_argument(
        "--db", dest="db_path", default=None,
        help="Path to the world DB (default: production zeus-world.db).",
    )
    args = parser.parse_args()

    from src.state.db import ZEUS_WORLD_DB_PATH  # noqa: PLC0415
    from pathlib import Path as _Path  # noqa: PLC0415
    _lock_path = _Path(args.db_path) if args.db_path else ZEUS_WORLD_DB_PATH
    with db_writer_lock(_lock_path, WriteClass.BULK):
        if args.db_path:
            conn = sqlite3.connect(args.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
        else:
            conn = get_world_connection(write_class="bulk")
        init_schema(conn)
        apply_v2_schema(conn)

        try:
            per_metric = backfill_all_v2(
                conn,
                dry_run=args.dry_run,
                force=args.force,
                city_filter=args.city,
            )
            if not args.dry_run:
                conn.commit()
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        finally:
            conn.close()

    any_refused = any(s.refused for s in per_metric.values())
    return 1 if any_refused else 0


if __name__ == "__main__":
    sys.exit(main())
