"""Rebuild calibration_pairs on the canonical bin grid.

2026-04-14 refactor — decouple calibration learning from market_events.

This script is the new write path for ``calibration_pairs``. It replaces the
legacy ``scripts/generate_calibration_pairs.py``, which required
``market_events`` (Rainstorm migration data, lost) to define bin structure.
The new path constructs a canonical per-unit bin grid
(``src/contracts/calibration_bins.py``) that mirrors live Polymarket widths
(2°F pair-integer interior bins for F cities, 1°C point-integer interior
bins for C cities, plus shoulder bins at both ends).

The P_raw computation is byte-identical to live inference: both go through
``src/signal/ensemble_signal.py::p_raw_vector_from_maxes`` which runs 10,000
Monte Carlo iterations with sensor noise σ and WMO half-up rounding per
member. Training and inference cannot diverge — they share one function.

USAGE (code-only slice — this session never runs the destructive path):

    # Read-only preview (default):
    python scripts/rebuild_calibration_pairs_canonical.py --dry-run

    # After TIGGE raw download completes and operator clears the partial-
    # ingest safety gate:
    python scripts/rebuild_calibration_pairs_canonical.py \\
        --no-dry-run --force

    # Development iteration (faster Monte Carlo):
    python scripts/rebuild_calibration_pairs_canonical.py \\
        --dry-run --n-mc 1000 --city NYC

SAFETY GATES:
- ``--dry-run`` is the default. ``--no-dry-run`` alone **does not** execute
  the destructive DELETE path; ``--force`` is a separate additional gate.
- ``--allow-unaudited-ensemble`` is required if any matched snapshot has
  ``data_version LIKE 'tigge_step%_v1_%'`` (the current partial-TIGGE
  pattern). The user directive 2026-04-14 said the partial TIGGE snapshots
  are not audited; this flag is the operator's explicit override.
- Entire rebuild runs inside one SAVEPOINT; any exception rolls back both
  the pre-delete and the new inserts.
- DELETE is keyed on ``bin_source='canonical_v1'`` equality, not LIKE.
  Legacy rows (``bin_source='legacy'``) and their decision groups are
  never touched.
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

from src.calibration.effective_sample_size import (
    build_decision_groups,
    write_decision_groups,
)
from src.calibration.manager import season_from_date
from src.calibration.store import add_calibration_pair
from src.config import City, cities_by_name
from src.contracts.calibration_bins import (
    UnitProvenanceError,
    grid_for_city,
    validate_members_unit_plausible,
)
from src.contracts.settlement_semantics import SettlementSemantics
from src.signal.ensemble_signal import p_raw_vector_from_maxes
from src.state.db import get_world_connection, init_schema


CANONICAL_BIN_SOURCE = "canonical_v1"

# Any snapshot whose data_version matches this prefix is flagged as partial
# TIGGE (unaudited, per user directive 2026-04-14). Rebuild refuses unless
# --allow-unaudited-ensemble is passed explicitly.
_UNAUDITED_DATA_VERSION_PREFIXES = ("tigge_step", "tigge_partial_")


@dataclass
class RebuildStats:
    snapshots_scanned: int = 0
    snapshots_eligible: int = 0
    snapshots_unaudited: int = 0
    snapshots_no_observation: int = 0
    snapshots_unit_rejected: int = 0
    snapshots_processed: int = 0
    pairs_written: int = 0
    decision_groups_written: int = 0
    pre_delete_canonical_pairs: int = 0
    pre_delete_linked_groups: int = 0
    preserved_legacy_pairs: int = 0
    preserved_unrelated_groups: int = 0
    per_city: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "snapshots_scanned": self.snapshots_scanned,
            "snapshots_eligible": self.snapshots_eligible,
            "snapshots_unaudited": self.snapshots_unaudited,
            "snapshots_no_observation": self.snapshots_no_observation,
            "snapshots_unit_rejected": self.snapshots_unit_rejected,
            "snapshots_processed": self.snapshots_processed,
            "pairs_written": self.pairs_written,
            "decision_groups_written": self.decision_groups_written,
            "pre_delete_canonical_pairs": self.pre_delete_canonical_pairs,
            "pre_delete_linked_groups": self.pre_delete_linked_groups,
            "preserved_legacy_pairs": self.preserved_legacy_pairs,
            "preserved_unrelated_groups": self.preserved_unrelated_groups,
            "per_city": dict(self.per_city),
        }


# ---------------------------------------------------------------------------
# Snapshot fetch + pre-delete accounting
# ---------------------------------------------------------------------------


def _fetch_eligible_snapshots(
    conn: sqlite3.Connection,
    city_filter: Optional[str],
) -> list[sqlite3.Row]:
    """Pull VERIFIED ensemble_snapshots with non-null members_json.

    The JOIN with observations happens per-row in the main loop so we can
    report the 'no matching observation' count separately from 'matched but
    unit-rejected'.
    """
    params: tuple = ()
    where = "WHERE authority = 'VERIFIED' AND members_json IS NOT NULL"
    if city_filter:
        where += " AND city = ?"
        params = (city_filter,)
    sql = f"""
        SELECT snapshot_id, city, target_date, lead_hours,
               available_at, members_json, data_version
        FROM ensemble_snapshots
        {where}
        ORDER BY city, target_date, lead_hours
    """
    return conn.execute(sql, params).fetchall()


def _fetch_verified_observation(
    conn: sqlite3.Connection,
    city: str,
    target_date: str,
) -> Optional[sqlite3.Row]:
    """One VERIFIED observation per (city, target_date), prefer wu over hko.

    Ordering by source DESC puts 'wu_icao_history' ahead of 'hko_daily_api'
    lexicographically, matching the historical preference encoded in
    rebuild_settlements.py.
    """
    return conn.execute(
        """
        SELECT city, target_date, high_temp, unit, authority, source
        FROM observations
        WHERE city = ? AND target_date = ? AND authority = 'VERIFIED'
        ORDER BY source DESC
        LIMIT 1
        """,
        (city, target_date),
    ).fetchone()


def _collect_pre_delete_counts(conn: sqlite3.Connection) -> dict:
    n_pairs_canonical = conn.execute(
        "SELECT COUNT(*) FROM calibration_pairs WHERE bin_source = ?",
        (CANONICAL_BIN_SOURCE,),
    ).fetchone()[0]
    n_pairs_legacy = conn.execute(
        "SELECT COUNT(*) FROM calibration_pairs WHERE bin_source != ?",
        (CANONICAL_BIN_SOURCE,),
    ).fetchone()[0]
    n_groups_linked = conn.execute(
        """
        SELECT COUNT(*) FROM calibration_decision_group
        WHERE group_id IN (
            SELECT DISTINCT decision_group_id FROM calibration_pairs
            WHERE bin_source = ? AND decision_group_id IS NOT NULL
        )
        """,
        (CANONICAL_BIN_SOURCE,),
    ).fetchone()[0]
    n_groups_total = conn.execute(
        "SELECT COUNT(*) FROM calibration_decision_group"
    ).fetchone()[0]
    return {
        "pairs_canonical": n_pairs_canonical,
        "pairs_legacy": n_pairs_legacy,
        "groups_linked": n_groups_linked,
        "groups_unrelated": n_groups_total - n_groups_linked,
    }


# ---------------------------------------------------------------------------
# Write layer
# ---------------------------------------------------------------------------


def _delete_canonical_slice(conn: sqlite3.Connection) -> None:
    """Orphan-free delete: decision groups first, then pair rows.

    Decision groups are deleted first — if we deleted pairs first, the
    subquery joining groups to pairs by ``decision_group_id`` would find
    zero rows and delete nothing.
    """
    conn.execute(
        """
        DELETE FROM calibration_decision_group
        WHERE group_id IN (
            SELECT DISTINCT decision_group_id FROM calibration_pairs
            WHERE bin_source = ? AND decision_group_id IS NOT NULL
        )
        """,
        (CANONICAL_BIN_SOURCE,),
    )
    conn.execute(
        "DELETE FROM calibration_pairs WHERE bin_source = ?",
        (CANONICAL_BIN_SOURCE,),
    )


def _process_snapshot(
    conn: sqlite3.Connection,
    snapshot: sqlite3.Row,
    city: City,
    *,
    n_mc: Optional[int],
    rng: np.random.Generator,
    stats: RebuildStats,
) -> None:
    """Match one snapshot to its observation and write N canonical pairs."""
    target_date = snapshot["target_date"]
    obs = _fetch_verified_observation(conn, city.name, target_date)
    if obs is None:
        stats.snapshots_no_observation += 1
        return

    member_maxes = np.asarray(
        json.loads(snapshot["members_json"]), dtype=float
    )
    try:
        validate_members_unit_plausible(member_maxes, city)
    except UnitProvenanceError as e:
        stats.snapshots_unit_rejected += 1
        print(f"  UNIT-REJECT {city.name}/{target_date}: {e}")
        return

    grid = grid_for_city(city)
    bins = grid.as_bins()
    sem = SettlementSemantics.for_city(city)
    try:
        settlement_value = sem.assert_settlement_value(
            float(obs["high_temp"]),
            context="rebuild_calibration_pairs_canonical",
        )
    except Exception as e:
        stats.snapshots_unit_rejected += 1
        print(f"  SETTLEMENT-REJECT {city.name}/{target_date}: {e}")
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

    pairs_this_snapshot = 0
    for b, p in zip(bins, p_raw_vec):
        outcome = 1 if b is winning_bin else 0
        add_calibration_pair(
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
            settlement_value=settlement_value,
            bin_source=CANONICAL_BIN_SOURCE,
            # VERIFIED is safe because both inputs were VERIFIED (enforced
            # by the WHERE clauses at fetch time). refit_platt.py filters
            # calibration_pairs by authority='VERIFIED' to ensure only
            # provenance-verified pairs train the Platt model.
            authority="VERIFIED",
        )
        pairs_this_snapshot += 1

    stats.snapshots_processed += 1
    stats.pairs_written += pairs_this_snapshot
    stats.per_city[city.name] = stats.per_city.get(city.name, 0) + pairs_this_snapshot


# ---------------------------------------------------------------------------
# Core orchestration
# ---------------------------------------------------------------------------


def rebuild(
    conn: sqlite3.Connection,
    *,
    dry_run: bool,
    force: bool,
    city_filter: Optional[str] = None,
    n_mc: Optional[int] = None,
    allow_unaudited_ensemble: bool = False,
    rng: Optional[np.random.Generator] = None,
) -> RebuildStats:
    """Run the rebuild end-to-end. Returns accounting stats.

    ``dry_run=True`` (default) prints the plan and writes nothing.
    ``dry_run=False`` additionally requires ``force=True`` to proceed with
    the destructive DELETE; passing only ``dry_run=False`` raises.
    """
    if rng is None:
        rng = np.random.default_rng()

    stats = RebuildStats()

    print("=" * 70)
    print("CALIBRATION PAIRS CANONICAL REBUILD")
    print("=" * 70)
    mode = "DRY-RUN" if dry_run else "LIVE WRITE"
    print(f"Mode:              {mode}")
    if city_filter:
        print(f"City filter:       {city_filter}")
    print(f"Canonical bin source tag:  {CANONICAL_BIN_SOURCE!r}")
    print(f"n_mc per snapshot: {n_mc or 'default (ensemble_n_mc())'}")

    # --- Phase 1: snapshot accounting + audit gate ------------------------
    snapshots = _fetch_eligible_snapshots(conn, city_filter=city_filter)
    stats.snapshots_scanned = len(snapshots)

    unaudited_ids: list[int] = []
    eligible: list[sqlite3.Row] = []
    for snap in snapshots:
        dv = (snap["data_version"] or "").lower()
        if any(dv.startswith(p) for p in _UNAUDITED_DATA_VERSION_PREFIXES):
            unaudited_ids.append(snap["snapshot_id"])
            if not allow_unaudited_ensemble:
                continue
        eligible.append(snap)
    stats.snapshots_unaudited = len(unaudited_ids)
    stats.snapshots_eligible = len(eligible)

    print()
    print(f"Snapshots scanned:   {stats.snapshots_scanned}")
    print(f"  unaudited (tigge_step*, tigge_partial_*): {stats.snapshots_unaudited}")
    print(f"  eligible for rebuild: {stats.snapshots_eligible}")
    if stats.snapshots_unaudited > 0 and not allow_unaudited_ensemble:
        print()
        print(
            "REFUSING: matched snapshots contain partial/unaudited TIGGE data.\n"
            "Pass --allow-unaudited-ensemble to override (operator responsibility)."
        )
        return stats

    # --- Phase 2: pre-delete accounting -----------------------------------
    counts = _collect_pre_delete_counts(conn)
    stats.pre_delete_canonical_pairs = counts["pairs_canonical"]
    stats.pre_delete_linked_groups = counts["groups_linked"]
    stats.preserved_legacy_pairs = counts["pairs_legacy"]
    stats.preserved_unrelated_groups = counts["groups_unrelated"]

    print()
    print("Will delete:")
    print(f"  {stats.pre_delete_canonical_pairs} canonical_v1 calibration_pairs rows")
    print(f"  {stats.pre_delete_linked_groups} linked calibration_decision_group rows")
    print("Will preserve:")
    print(f"  {stats.preserved_legacy_pairs} legacy (non-canonical) calibration_pairs rows")
    print(f"  {stats.preserved_unrelated_groups} unrelated calibration_decision_group rows")

    if dry_run:
        print()
        print("[dry-run] no DB changes made.")
        _print_rebuild_estimate(eligible)
        return stats
    if not force:
        raise RuntimeError(
            "--no-dry-run requires --force for the destructive delete path. "
            "Re-run with both flags if you really want to rebuild."
        )

    # --- Phase 3: atomic rebuild ------------------------------------------
    conn.execute("SAVEPOINT canonical_rebuild")
    try:
        _delete_canonical_slice(conn)
        start = time.monotonic()
        missing_city_count = 0
        for snap in eligible:
            city = cities_by_name.get(snap["city"])
            if city is None:
                missing_city_count += 1
                continue
            _process_snapshot(
                conn, snap, city,
                n_mc=n_mc,  # None → p_raw_vector_from_maxes uses ensemble_n_mc()
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

        # Decision group materialization (mirrors generate_calibration_pairs.py)
        groups = build_decision_groups(conn)
        stats.decision_groups_written = write_decision_groups(
            conn,
            groups,
            recorded_at=datetime.now(timezone.utc).isoformat(),
            update_pair_rows=True,
        )
        conn.execute("RELEASE SAVEPOINT canonical_rebuild")
        conn.commit()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT canonical_rebuild")
        conn.execute("RELEASE SAVEPOINT canonical_rebuild")
        raise

    print()
    print("=" * 70)
    print("REBUILD COMPLETE")
    print("=" * 70)
    print(f"Snapshots processed:    {stats.snapshots_processed}")
    print(f"  no matching obs:      {stats.snapshots_no_observation}")
    print(f"  unit/settlement reject: {stats.snapshots_unit_rejected}")
    print(f"Pairs written:          {stats.pairs_written}")
    print(f"Decision groups written: {stats.decision_groups_written}")
    if stats.per_city:
        print("Per-city pair counts:")
        for city, n in sorted(stats.per_city.items()):
            print(f"  {city:20s}  {n}")

    return stats


def _print_rebuild_estimate(eligible: list[sqlite3.Row]) -> None:
    """Print an order-of-magnitude estimate of what a live rebuild would write."""
    from src.contracts.calibration_bins import F_CANONICAL_GRID, C_CANONICAL_GRID
    n_bins_f = F_CANONICAL_GRID.n_bins
    n_bins_c = C_CANONICAL_GRID.n_bins

    f_count = 0
    c_count = 0
    unknown_count = 0
    for snap in eligible:
        city = cities_by_name.get(snap["city"])
        if city is None:
            unknown_count += 1
            continue
        if city.settlement_unit == "F":
            f_count += 1
        elif city.settlement_unit == "C":
            c_count += 1
    approx_rows = f_count * n_bins_f + c_count * n_bins_c
    print()
    print("Estimated live-write rowcount:")
    print(f"  F-unit snapshots: {f_count} × {n_bins_f} bins = {f_count * n_bins_f}")
    print(f"  C-unit snapshots: {c_count} × {n_bins_c} bins = {c_count * n_bins_c}")
    print(f"  Total pairs:      {approx_rows}")
    if unknown_count:
        print(f"  unknown-city snapshots (would be skipped): {unknown_count}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild calibration_pairs on the canonical bin grid.",
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
        help=(
            "Monte Carlo iterations per snapshot. Defaults to ensemble_n_mc() "
            "(10,000). Use --n-mc 1000 for fast development iteration."
        ),
    )
    parser.add_argument(
        "--allow-unaudited-ensemble", dest="allow_unaudited", action="store_true",
        default=False,
        help=(
            "Override the partial-TIGGE gate. Required if any matched "
            "ensemble_snapshots row has data_version LIKE 'tigge_step%%_v1_%%'."
        ),
    )
    args = parser.parse_args()

    if args.db_path:
        conn = sqlite3.connect(args.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
    else:
        conn = get_world_connection()
    init_schema(conn)

    try:
        stats = rebuild(
            conn,
            dry_run=args.dry_run,
            force=args.force,
            city_filter=args.city,
            n_mc=args.n_mc,
            allow_unaudited_ensemble=args.allow_unaudited,
        )
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    return 0 if stats.snapshots_scanned >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
