"""Rebuild calibration_pairs_v2 from ensemble_snapshots_v2 (high track).

Phase 4C — reads high-track canonical snapshots from ``ensemble_snapshots_v2``
and writes ``calibration_pairs_v2`` rows via ``add_calibration_pair_v2`` with
``metric_identity=HIGH_LOCALDAY_MAX``.

This script is the v2 successor to ``rebuild_calibration_pairs_canonical.py``.
Key differences from the legacy script:

- Source table: ``ensemble_snapshots_v2`` (not ``ensemble_snapshots``)
- Eligibility filter: ``temperature_metric='high'``, ``training_allowed=1``,
  ``causality_status='OK'``, ``authority='VERIFIED'``
- Write function: ``add_calibration_pair_v2(metric_identity=HIGH_LOCALDAY_MAX)``
- Target table: ``calibration_pairs_v2`` (never touches legacy ``calibration_pairs``)
- INV-15 enforced structurally inside ``add_calibration_pair_v2``
- ``assert_data_version_allowed`` called on every snapshot before processing

USAGE:

    # Dry-run (default, safe):
    python scripts/rebuild_calibration_pairs_v2.py

    # Live write (requires --no-dry-run --force):
    python scripts/rebuild_calibration_pairs_v2.py --no-dry-run --force

    # Single city (development):
    python scripts/rebuild_calibration_pairs_v2.py --dry-run --city NYC --n-mc 1000

SAFETY GATES:
- ``--dry-run`` is the default. ``--no-dry-run`` alone does not write — ``--force``
  is required in addition.
- Delete is keyed on ``bin_source='canonical_v2'`` equality; legacy rows are never
  touched.
- Entire rebuild runs inside one SAVEPOINT; any exception rolls back.
- Quarantined snapshots (``is_quarantined(data_version)``) are skipped and counted.
- ``>30%`` no-observation ratio → abort.
- Zero pairs written → abort.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.decision_group import compute_id
from src.calibration.manager import season_from_date
from src.calibration.store import add_calibration_pair_v2
from src.config import City, cities_by_name
from src.contracts.calibration_bins import (
    UnitProvenanceError,
    grid_for_city,
    validate_members_unit_plausible,
    validate_members_vs_observation,
)
from src.contracts.ensemble_snapshot_provenance import (
    DataVersionQuarantinedError,
    assert_data_version_allowed,
    is_quarantined,
)
from src.contracts.settlement_semantics import SettlementSemantics
from src.signal.ensemble_signal import p_raw_vector_from_maxes
from src.state.db import get_world_connection, init_schema
from src.state.schema.v2_schema import apply_v2_schema
from src.types.market import validate_bin_topology
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN, MetricIdentity


@dataclass(frozen=True)
class CalibrationMetricSpec:
    identity: MetricIdentity
    allowed_data_version: str


METRIC_SPECS: tuple[CalibrationMetricSpec, ...] = (
    CalibrationMetricSpec(HIGH_LOCALDAY_MAX, HIGH_LOCALDAY_MAX.data_version),
    CalibrationMetricSpec(LOW_LOCALDAY_MIN, LOW_LOCALDAY_MIN.data_version),
)


def iter_training_snapshots(conn: sqlite3.Connection, spec: CalibrationMetricSpec):
    return conn.execute(
        """
        SELECT *
        FROM ensemble_snapshots_v2
        WHERE temperature_metric = ?
          AND data_version = ?
          AND training_allowed = 1
          AND causality_status = 'OK'
          AND authority = 'VERIFIED'
        ORDER BY target_date, city, available_at
        """,
        (spec.identity.temperature_metric, spec.allowed_data_version),
    ).fetchall()


CANONICAL_BIN_SOURCE_V2 = "canonical_v2"

MIN_TRAINING_DATE = "2024-01-01"


@dataclass
class RebuildStatsV2:
    snapshots_scanned: int = 0
    snapshots_eligible: int = 0
    snapshots_quarantined: int = 0
    snapshots_no_observation: int = 0
    snapshots_unit_rejected: int = 0
    snapshots_processed: int = 0
    refused: bool = False
    pairs_written: int = 0
    pre_delete_v2_pairs: int = 0
    per_city: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "snapshots_scanned": self.snapshots_scanned,
            "snapshots_eligible": self.snapshots_eligible,
            "snapshots_quarantined": self.snapshots_quarantined,
            "snapshots_no_observation": self.snapshots_no_observation,
            "snapshots_unit_rejected": self.snapshots_unit_rejected,
            "snapshots_processed": self.snapshots_processed,
            "refused": self.refused,
            "pairs_written": self.pairs_written,
            "pre_delete_v2_pairs": self.pre_delete_v2_pairs,
            "per_city": dict(self.per_city),
        }


def _fetch_eligible_snapshots_v2(
    conn: sqlite3.Connection,
    city_filter: Optional[str],
    spec: "CalibrationMetricSpec | None" = None,
) -> list[sqlite3.Row]:
    """Pull eligible snapshots from ensemble_snapshots_v2 for the given spec."""
    track = spec.identity.temperature_metric if spec is not None else "high"
    params: list = [track, MIN_TRAINING_DATE]
    where = (
        "WHERE temperature_metric = ? "
        "AND training_allowed = 1 "
        "AND causality_status = 'OK' "
        "AND authority = 'VERIFIED' "
        "AND members_json IS NOT NULL "
        "AND target_date >= ?"
    )
    if city_filter:
        where += " AND city = ?"
        params.append(city_filter)
    sql = f"""
        SELECT snapshot_id, city, target_date, issue_time, lead_hours,
               available_at, members_json, data_version
        FROM ensemble_snapshots_v2
        {where}
        ORDER BY city, target_date, lead_hours
    """
    return conn.execute(sql, tuple(params)).fetchall()


def _fetch_verified_observation(
    conn: sqlite3.Connection,
    city: str,
    target_date: str,
) -> Optional[sqlite3.Row]:
    """One VERIFIED high_temp observation per (city, target_date)."""
    return conn.execute(
        """
        SELECT city, target_date, high_temp, unit, authority, source
        FROM observations
        WHERE city = ? AND target_date = ? AND authority = 'VERIFIED'
          AND high_temp IS NOT NULL
        ORDER BY source DESC
        LIMIT 1
        """,
        (city, target_date),
    ).fetchone()


def _collect_pre_delete_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM calibration_pairs_v2 WHERE bin_source = ?",
        (CANONICAL_BIN_SOURCE_V2,),
    ).fetchone()[0]


def _delete_canonical_v2_slice(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM calibration_pairs_v2 WHERE bin_source = ?",
        (CANONICAL_BIN_SOURCE_V2,),
    )


def _process_snapshot_v2(
    conn: sqlite3.Connection,
    snapshot: sqlite3.Row,
    city: City,
    *,
    spec: CalibrationMetricSpec,
    n_mc: Optional[int],
    rng: np.random.Generator,
    stats: RebuildStatsV2,
) -> None:
    """Match one v2 snapshot to its observation and write calibration_pairs_v2 rows."""
    target_date = snapshot["target_date"]
    data_version = snapshot["data_version"] or ""
    source = ""  # ensemble_snapshots_v2 has no source column; INV-15 gates on data_version prefix

    # Per-spec cross-check: write-time defense against cross-metric contamination (R-AU).
    if data_version != spec.allowed_data_version:
        raise DataVersionQuarantinedError(
            f"rebuild_calibration_pairs_v2: snapshot data_version={data_version!r} "
            f"does not match spec.allowed_data_version={spec.allowed_data_version!r}. "
            "Cross-metric contamination refused."
        )

    # Quarantine guard (belt-and-suspenders: eligibility query already filters
    # training_allowed=1, but data_version quarantine is a write-time contract)
    assert_data_version_allowed(data_version, context="rebuild_calibration_pairs_v2")

    obs = _fetch_verified_observation(conn, city.name, target_date)
    if obs is None:
        stats.snapshots_no_observation += 1
        return

    member_maxes = np.asarray(json.loads(snapshot["members_json"]), dtype=float)
    try:
        validate_members_unit_plausible(member_maxes, city)
    except UnitProvenanceError as e:
        stats.snapshots_unit_rejected += 1
        print(f"  UNIT-REJECT {city.name}/{target_date}: {e}")
        return

    grid = grid_for_city(city)
    bins = grid.as_bins()
    validate_bin_topology(bins)
    sem = SettlementSemantics.for_city(city)
    try:
        settlement_value = sem.assert_settlement_value(
            float(obs["high_temp"]),
            context="rebuild_calibration_pairs_v2",
        )
    except Exception as e:
        stats.snapshots_unit_rejected += 1
        print(f"  SETTLEMENT-REJECT {city.name}/{target_date}: {e}")
        return

    try:
        validate_members_vs_observation(member_maxes, city, settlement_value)
    except UnitProvenanceError as e:
        stats.snapshots_unit_rejected += 1
        print(f"  UNIT-VS-OBS-REJECT {city.name}/{target_date}: {e}")
        return

    p_raw_vec = p_raw_vector_from_maxes(
        member_maxes,
        city,
        sem,
        bins,
        n_mc=n_mc,
        rng=rng,
    )
    winning_bin = grid.bin_for_value(settlement_value)

    season = season_from_date(target_date, lat=city.lat)
    lead_days = float(snapshot["lead_hours"]) / 24.0
    available_at = snapshot["available_at"]
    decision_group_id = compute_id(
        city.name,
        target_date,
        snapshot["issue_time"],
        data_version,
    )

    pairs_this_snapshot = 0
    for b, p in zip(bins, p_raw_vec):
        outcome = 1 if b is winning_bin else 0
        add_calibration_pair_v2(
            conn,
            city=city.name,
            target_date=target_date,
            range_label=b.label,
            p_raw=float(p),
            outcome=outcome,
            lead_days=lead_days,
            season=season,
            cluster=city.cluster,
            forecast_available_at=available_at,
            metric_identity=HIGH_LOCALDAY_MAX,
            training_allowed=True,
            data_version=data_version,
            source=source,
            settlement_value=settlement_value,
            decision_group_id=decision_group_id,
            bin_source=CANONICAL_BIN_SOURCE_V2,
            authority="VERIFIED",
            causality_status="OK",
            snapshot_id=snapshot["snapshot_id"],
        )
        pairs_this_snapshot += 1

    stats.snapshots_processed += 1
    stats.pairs_written += pairs_this_snapshot
    stats.per_city[city.name] = stats.per_city.get(city.name, 0) + pairs_this_snapshot


def rebuild_v2(
    conn: sqlite3.Connection,
    *,
    dry_run: bool,
    force: bool,
    spec: CalibrationMetricSpec = METRIC_SPECS[0],
    city_filter: Optional[str] = None,
    n_mc: Optional[int] = None,
    rng: Optional[np.random.Generator] = None,
) -> RebuildStatsV2:
    """Run the v2 rebuild end-to-end. Returns accounting stats."""
    if rng is None:
        rng = np.random.default_rng()

    stats = RebuildStatsV2()

    print("=" * 70)
    print(f"CALIBRATION PAIRS V2 REBUILD ({spec.identity.temperature_metric} track, {CANONICAL_BIN_SOURCE_V2})")
    print("=" * 70)
    print(f"Mode:              {'DRY-RUN' if dry_run else 'LIVE WRITE'}")
    if city_filter:
        print(f"City filter:       {city_filter}")
    print(f"Bin source tag:    {CANONICAL_BIN_SOURCE_V2!r}")
    print(f"MetricIdentity:    {spec.identity}")
    print(f"n_mc per snapshot: {n_mc or 'default (ensemble_n_mc())'}")

    snapshots = _fetch_eligible_snapshots_v2(conn, city_filter=city_filter, spec=spec)
    stats.snapshots_scanned = len(snapshots)

    eligible: list[sqlite3.Row] = []
    for snap in snapshots:
        dv = snap["data_version"] or ""
        if is_quarantined(dv):
            stats.snapshots_quarantined += 1
            print(f"  QUARANTINED snapshot_id={snap['snapshot_id']} data_version={dv!r}")
            continue
        eligible.append(snap)
    stats.snapshots_eligible = len(eligible)

    print()
    print(f"Snapshots scanned:    {stats.snapshots_scanned}")
    print(f"  quarantined:        {stats.snapshots_quarantined}")
    print(f"  eligible:           {stats.snapshots_eligible}")

    stats.pre_delete_v2_pairs = _collect_pre_delete_count(conn)
    print(f"Existing canonical_v2 pairs (will delete): {stats.pre_delete_v2_pairs}")

    if dry_run:
        print()
        print("[dry-run] no DB changes made.")
        _print_rebuild_estimate_v2(eligible)
        return stats

    if not force:
        raise RuntimeError(
            "--no-dry-run requires --force for the destructive delete path."
        )
    if not eligible:
        stats.refused = True
        raise RuntimeError(
            "Refusing live v2 rebuild: no eligible snapshots. "
            "Check that 4B ingest has populated ensemble_snapshots_v2."
        )

    conn.execute("SAVEPOINT v2_rebuild")
    try:
        _delete_canonical_v2_slice(conn)
        start = time.monotonic()
        missing_city_count = 0
        for snap in eligible:
            city = cities_by_name.get(snap["city"])
            if city is None:
                missing_city_count += 1
                continue
            _process_snapshot_v2(
                conn, snap, city,
                spec=spec,
                n_mc=n_mc,
                rng=rng,
                stats=stats,
            )
            if stats.snapshots_processed % 500 == 0 and stats.snapshots_processed > 0:
                elapsed = time.monotonic() - start
                rate = stats.snapshots_processed / max(elapsed, 1e-6)
                print(
                    f"  progress: {stats.snapshots_processed}/{len(eligible)} "
                    f"({rate:.1f} snap/s)"
                )

        if missing_city_count:
            print(f"  WARN: {missing_city_count} snapshots had unknown city, skipped")

        # Hard-failure policy (stricter than legacy rebuild_calibration_pairs_canonical.py):
        # ANY missing-city or unit-rejection rolls back the entire SAVEPOINT.
        # The legacy script skips individual bad rows; v2 treats them as bugs
        # (cities.json drift or contaminated members_json) that must be fixed
        # before the rebuild proceeds. Soft-skip is only allowed for missing
        # observations (expected data holes), not for structural integrity failures.
        hard_failures = missing_city_count + stats.snapshots_unit_rejected
        if hard_failures:
            stats.refused = True
            raise RuntimeError(
                f"Refusing v2 rebuild: {hard_failures} hard failures "
                f"(missing_city={missing_city_count}, "
                f"unit_rejected={stats.snapshots_unit_rejected}); rolling back."
            )

        no_obs_ratio = stats.snapshots_no_observation / max(len(eligible), 1)
        if no_obs_ratio > 0.30:
            stats.refused = True
            raise RuntimeError(
                f"Refusing v2 rebuild: "
                f"{stats.snapshots_no_observation}/{len(eligible)} "
                f"({no_obs_ratio:.1%}) had no matching observation. "
                f"Expected <30%. Check WU/HKO backfill coverage."
            )

        if stats.pairs_written == 0:
            stats.refused = True
            raise RuntimeError(
                "Refusing v2 rebuild: zero pairs written; rolling back."
            )

        conn.execute("RELEASE SAVEPOINT v2_rebuild")
        conn.commit()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT v2_rebuild")
        conn.execute("RELEASE SAVEPOINT v2_rebuild")
        raise

    print()
    print("=" * 70)
    print("V2 REBUILD COMPLETE")
    print("=" * 70)
    print(f"Snapshots processed:     {stats.snapshots_processed}")
    print(f"  no matching obs:       {stats.snapshots_no_observation}")
    print(f"  unit/settlement reject:{stats.snapshots_unit_rejected}")
    print(f"Pairs written:           {stats.pairs_written}")
    if stats.per_city:
        print("Per-city pair counts:")
        for city_name, n in sorted(stats.per_city.items()):
            print(f"  {city_name:20s}  {n}")

    return stats


def _print_rebuild_estimate_v2(eligible: list[sqlite3.Row]) -> None:
    from src.contracts.calibration_bins import C_CANONICAL_GRID, F_CANONICAL_GRID
    n_bins_f = F_CANONICAL_GRID.n_bins
    n_bins_c = C_CANONICAL_GRID.n_bins
    f_count = c_count = unknown_count = 0
    for snap in eligible:
        city = cities_by_name.get(snap["city"])
        if city is None:
            unknown_count += 1
            continue
        if city.settlement_unit == "F":
            f_count += 1
        elif city.settlement_unit == "C":
            c_count += 1
    approx = f_count * n_bins_f + c_count * n_bins_c
    print()
    print("Estimated live-write rowcount (calibration_pairs_v2):")
    print(f"  F-unit snapshots: {f_count} × {n_bins_f} bins = {f_count * n_bins_f}")
    print(f"  C-unit snapshots: {c_count} × {n_bins_c} bins = {c_count * n_bins_c}")
    print(f"  Total pairs:      {approx}")
    if unknown_count:
        print(f"  unknown-city snapshots (would be skipped): {unknown_count}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild calibration_pairs_v2 from ensemble_snapshots_v2 (high track).",
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=True,
        help="Preview only — do not write to DB (default).",
    )
    parser.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false",
        help="Execute the rebuild. Must be combined with --force.",
    )
    parser.add_argument(
        "--force", dest="force", action="store_true", default=False,
        help="Required in addition to --no-dry-run to authorize destructive delete.",
    )
    parser.add_argument(
        "--city", dest="city", default=None,
        help="Limit rebuild to a single city name.",
    )
    parser.add_argument(
        "--db", dest="db_path", default=None,
        help="Path to the world DB (default: production zeus-world.db).",
    )
    parser.add_argument(
        "--n-mc", dest="n_mc", type=int, default=None,
        help="Monte Carlo iterations per snapshot (default: ensemble_n_mc() = 10,000).",
    )
    args = parser.parse_args()

    if args.db_path:
        conn = sqlite3.connect(args.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
    else:
        conn = get_world_connection()
    init_schema(conn)
    apply_v2_schema(conn)

    try:
        stats = rebuild_v2(
            conn,
            dry_run=args.dry_run,
            force=args.force,
            city_filter=args.city,
            n_mc=args.n_mc,
        )
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    return 1 if stats.refused else 0


if __name__ == "__main__":
    sys.exit(main())
