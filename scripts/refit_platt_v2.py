"""Refit Platt calibration models from calibration_pairs_v2 (high track).

Phase 4D — reads high-track calibration pairs from ``calibration_pairs_v2``
and writes per-bucket Platt models to ``platt_models_v2`` via
``save_platt_model_v2(metric_identity=HIGH_LOCALDAY_MAX)``.

Bucket key: (temperature_metric, cluster, season, data_version, input_space).
NO city or target_date columns (Phase 2 semantic-pollution fix).

Before each INSERT, calls ``deactivate_model_v2`` to flip any prior
is_active=1 row to is_active=0 for that bucket — because save_platt_model_v2
uses plain INSERT (not INSERT OR REPLACE). After a full successful refit,
hard-deletes all is_active=0 rows for the high track to keep the table clean.

USAGE:

    # Dry-run (default, safe):
    python scripts/refit_platt_v2.py

    # Live write (requires --no-dry-run --force):
    python scripts/refit_platt_v2.py --no-dry-run --force

SAFETY GATES:
- ``--dry-run`` is the default. ``--no-dry-run`` alone does not write.
- Requires ``--force`` in addition to ``--no-dry-run`` for live write.
- Minimum 15 distinct decision_group_id values per bucket (maturity gate).
- SAVEPOINT rollback on any exception (including per-bucket fit failures).
- Does not touch legacy ``platt_models`` table.
- Metric-scoped: only reads/writes temperature_metric='high'. Low-track rows
  are invisible to this script (Phase 5 will run an identical script with
  metric_identity=LOW_LOCALDAY_MIN).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.manager import maturity_level, regularization_for_level
from src.calibration.platt import ExtendedPlattCalibrator
from src.calibration.store import (
    deactivate_model_v2,
    infer_bin_width_from_label,
    save_platt_model_v2,
)
from src.config import calibration_maturity_thresholds, calibration_n_bootstrap
from src.state.db import get_world_connection, init_schema
from src.state.schema.v2_schema import apply_v2_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX, MetricIdentity
from scripts.rebuild_calibration_pairs_v2 import METRIC_SPECS

_, _, MIN_DECISION_GROUPS = calibration_maturity_thresholds()  # level3 = refit threshold


@dataclass
class RefitStatsV2:
    buckets_scanned: int = 0
    buckets_skipped_maturity: int = 0
    buckets_fit: int = 0
    buckets_failed: int = 0
    refused: bool = False
    deactivated_rows: int = 0
    per_bucket: dict[str, str] = field(default_factory=dict)


def _validate_p_raw_domain(bucket_key: str, p_raw: np.ndarray) -> None:
    if not np.isfinite(p_raw).all() or np.any((p_raw < 0.0) | (p_raw > 1.0)):
        raise RuntimeError(
            f"refit_platt_v2 refused to fit {bucket_key}: p_raw outside [0, 1]"
        )


def _fetch_buckets(conn: sqlite3.Connection, metric_identity: MetricIdentity) -> list[sqlite3.Row]:
    """Fetch metric-scoped buckets with sufficient maturity from calibration_pairs_v2."""
    return conn.execute("""
        SELECT cluster, season, data_version,
               COUNT(DISTINCT decision_group_id) AS n_eff
        FROM calibration_pairs_v2
        WHERE temperature_metric = ?
          AND training_allowed = 1
          AND authority = 'VERIFIED'
          AND decision_group_id IS NOT NULL
          AND decision_group_id != ''
          AND p_raw IS NOT NULL
        GROUP BY cluster, season, data_version
        HAVING n_eff >= ?
    """, (metric_identity.temperature_metric, MIN_DECISION_GROUPS)).fetchall()


def _fetch_pairs_for_bucket(
    conn: sqlite3.Connection,
    cluster: str,
    season: str,
    data_version: str,
    metric_identity: MetricIdentity,
) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT p_raw, lead_days, outcome, range_label, decision_group_id
        FROM calibration_pairs_v2
        WHERE temperature_metric = ?
          AND training_allowed = 1
          AND authority = 'VERIFIED'
          AND cluster = ? AND season = ? AND data_version = ?
          AND decision_group_id IS NOT NULL
          AND decision_group_id != ''
          AND p_raw IS NOT NULL
    """, (metric_identity.temperature_metric, cluster, season, data_version)).fetchall()


def _fit_bucket(
    conn: sqlite3.Connection,
    cluster: str,
    season: str,
    data_version: str,
    *,
    metric_identity: MetricIdentity,
    dry_run: bool,
    stats: RefitStatsV2,
) -> None:
    pairs = _fetch_pairs_for_bucket(conn, cluster, season, data_version, metric_identity)
    n_eff = len({p["decision_group_id"] for p in pairs})
    bucket_key = f"{metric_identity.temperature_metric}:{cluster}:{season}:{data_version}"

    if n_eff < MIN_DECISION_GROUPS:
        stats.buckets_skipped_maturity += 1
        return

    p_raw = np.array([p["p_raw"] for p in pairs])
    _validate_p_raw_domain(bucket_key, p_raw)
    lead_days = np.array([p["lead_days"] for p in pairs])
    outcomes = np.array([p["outcome"] for p in pairs])
    bin_widths = np.array(
        [infer_bin_width_from_label(p["range_label"]) for p in pairs],
        dtype=object,
    )
    decision_group_ids = np.array(
        [p["decision_group_id"] for p in pairs], dtype=object
    )

    cal = ExtendedPlattCalibrator()
    reg_C = regularization_for_level(maturity_level(n_eff))
    cal.fit(
        p_raw,
        lead_days,
        outcomes,
        bin_widths=bin_widths,
        decision_group_ids=decision_group_ids,
        n_bootstrap=calibration_n_bootstrap(),
        regularization_C=reg_C,
    )

    brier_scores = [
        (cal.predict_for_bin(float(p_raw[i]), float(lead_days[i]), bin_width=bin_widths[i]) - outcomes[i]) ** 2
        for i in range(len(p_raw))
    ]
    brier_insample = float(np.mean(brier_scores))

    summary = (
        f"A={cal.A:+.3f} B={cal.B:+.3f} C={cal.C:+.3f} "
        f"n_eff={n_eff} rows={len(pairs)} Brier={brier_insample:.4f}"
    )

    if dry_run:
        print(f"[dry] {bucket_key:50s} {summary}")
        stats.buckets_fit += 1
        stats.per_bucket[bucket_key] = f"DRY {summary}"
        return

    deactivated = deactivate_model_v2(
        conn,
        metric_identity=metric_identity,
        cluster=cluster,
        season=season,
        data_version=data_version,
        input_space=cal.input_space,
    )
    stats.deactivated_rows += deactivated

    save_platt_model_v2(
        conn,
        metric_identity=metric_identity,
        cluster=cluster,
        season=season,
        data_version=data_version,
        param_A=cal.A,
        param_B=cal.B,
        param_C=cal.C,
        bootstrap_params=cal.bootstrap_params,
        n_samples=n_eff,
        brier_insample=brier_insample,
        input_space=cal.input_space,
        authority="VERIFIED",
    )

    print(f"OK  {bucket_key:50s} {summary}")
    stats.buckets_fit += 1
    stats.per_bucket[bucket_key] = f"OK {summary}"


def refit_v2(
    conn: sqlite3.Connection,
    *,
    metric_identity: MetricIdentity,
    dry_run: bool,
    force: bool,
) -> RefitStatsV2:
    stats = RefitStatsV2()

    print("=" * 70)
    print("PLATT V2 REFIT (calibration_pairs_v2 → platt_models_v2)")
    print("=" * 70)
    print(f"Mode:           {'DRY-RUN' if dry_run else 'LIVE WRITE'}")
    print(f"MetricIdentity: {metric_identity}")
    print(f"Min groups:     {MIN_DECISION_GROUPS}")

    buckets = _fetch_buckets(conn, metric_identity)
    stats.buckets_scanned = len(buckets)
    print(f"Buckets eligible (n_eff >= {MIN_DECISION_GROUPS}): {stats.buckets_scanned}")

    if not buckets:
        stats.refused = True
        print(
            f"refit_platt_v2: no {metric_identity.temperature_metric}-track bucket has at least "
            f"{MIN_DECISION_GROUPS} distinct decision groups — nothing to refit."
        )
        return stats

    if not dry_run and not force:
        raise RuntimeError(
            "--no-dry-run requires --force for the live write path."
        )

    failed_buckets: list[str] = []

    conn.execute("SAVEPOINT v2_refit")
    try:
        for bucket in buckets:
            cluster = bucket["cluster"]
            season = bucket["season"]
            data_version = bucket["data_version"]
            bucket_key = f"{metric_identity.temperature_metric}:{cluster}:{season}:{data_version}"
            try:
                _fit_bucket(conn, cluster, season, data_version, metric_identity=metric_identity, dry_run=dry_run, stats=stats)
            except Exception as e:
                print(f"ERR {bucket_key}: {e}")
                stats.buckets_failed += 1
                failed_buckets.append(bucket_key)

        if failed_buckets:
            stats.refused = True
            raise RuntimeError(
                f"refit_platt_v2 failed for {len(failed_buckets)} bucket(s): "
                + ", ".join(sorted(failed_buckets))
            )

        conn.execute("RELEASE SAVEPOINT v2_refit")
        if not dry_run:
            conn.commit()

    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT v2_refit")
        conn.execute("RELEASE SAVEPOINT v2_refit")
        raise

    print()
    print("=" * 70)
    print(f"{'[DRY-RUN] ' if dry_run else ''}REFIT COMPLETE")
    print("=" * 70)
    print(f"Buckets fit:             {stats.buckets_fit}")
    print(f"Buckets skipped:         {stats.buckets_skipped_maturity}")
    print(f"Buckets failed:          {stats.buckets_failed}")
    if not dry_run:
        print(f"Prior rows replaced:     {stats.deactivated_rows}")

    return stats


def refit_all_v2(
    conn: sqlite3.Connection,
    *,
    dry_run: bool,
    force: bool,
) -> dict[str, RefitStatsV2]:
    """Refit Platt v2 models for ALL METRIC_SPECS in one invocation.

    Returns per-metric stats dict keyed by temperature_metric string.
    Any spec that fails propagates the exception; caller sees non-zero exit.
    """
    per_metric: dict[str, RefitStatsV2] = {}
    for spec in METRIC_SPECS:
        stats = refit_v2(
            conn,
            metric_identity=spec.identity,
            dry_run=dry_run,
            force=force,
        )
        per_metric[spec.identity.temperature_metric] = stats
    return per_metric


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refit Platt v2 models from calibration_pairs_v2 (both tracks).",
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=True,
        help="Preview only — do not write to DB (default).",
    )
    parser.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false",
        help="Execute the refit. Must be combined with --force.",
    )
    parser.add_argument(
        "--force", dest="force", action="store_true", default=False,
        help="Required in addition to --no-dry-run for live write.",
    )
    parser.add_argument(
        "--db", dest="db_path", default=None,
        help="Path to the world DB (default: production zeus-world.db).",
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
        per_metric = refit_all_v2(conn, dry_run=args.dry_run, force=args.force)
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    any_refused = any(s.refused for s in per_metric.values())
    return 1 if any_refused else 0


if __name__ == "__main__":
    sys.exit(main())
