# Lifecycle: created=2026-04-16; last_reviewed=2026-04-24; last_reused=2026-04-24
# Purpose: Refit metric-aware Platt v2 models behind dry-run and preflight gates.
# Reuse: Inspect architecture/script_manifest.yaml and active packet receipt before live writes.

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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.architecture.decorators import capability, protects
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
from src.calibration.metric_specs import METRIC_SPECS
from scripts.verify_truth_surfaces import SHARED_DB, build_platt_refit_preflight_report

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


def _normalize_multi_filter(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    else:
        values = list(value)
    return [str(item) for item in values if str(item)]


def _append_multi_filter(
    where: list[str],
    params: list[object],
    column: str,
    value: str | Sequence[str] | None,
) -> None:
    values = _normalize_multi_filter(value)
    if not values:
        return
    placeholders = ", ".join("?" for _ in values)
    where.append(f"{column} IN ({placeholders})")
    params.extend(values)


def _fetch_affected_bucket_keys(
    conn: sqlite3.Connection,
    metric_identity: MetricIdentity,
    *,
    city_filter: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    cluster_filter: str | None = None,
    season_filter: str | Sequence[str] | None = None,
    data_version_filter: str | None = None,
) -> list[tuple[str, str, str, str, str, str]]:
    """Return bucket identities touched by a city/date scoped rebuild.

    Bucket key (Phase 2 — 2026-05-04): (cluster, season, data_version, cycle,
    source_id, horizon_profile). Cycle and source_id stratification per
    DESIGN_PHASE2_PLATT_CYCLE_STRATIFICATION.md and may4math.md Finding 1.
    Legacy rows pre-Phase-2 default to cycle='00', source_id='tigge_mars',
    horizon_profile='full'.
    """

    where = [
        "temperature_metric = ?",
        "training_allowed = 1",
        "authority = 'VERIFIED'",
        "decision_group_id IS NOT NULL",
        "decision_group_id != ''",
        "p_raw IS NOT NULL",
    ]
    params: list[object] = [metric_identity.temperature_metric]
    if city_filter:
        where.append("city = ?")
        params.append(city_filter)
    if start_date:
        where.append("target_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("target_date <= ?")
        params.append(end_date)
    if cluster_filter:
        where.append("cluster = ?")
        params.append(cluster_filter)
    _append_multi_filter(where, params, "season", season_filter)
    if data_version_filter:
        where.append("data_version = ?")
        params.append(data_version_filter)

    rows = conn.execute(f"""
        SELECT DISTINCT cluster, season, data_version, cycle, source_id, horizon_profile
        FROM calibration_pairs_v2
        WHERE {" AND ".join(where)}
        ORDER BY cluster, season, data_version, cycle, source_id, horizon_profile
    """, tuple(params)).fetchall()
    return [
        (
            str(row["cluster"]),
            str(row["season"]),
            str(row["data_version"]),
            str(row["cycle"]),
            str(row["source_id"]),
            str(row["horizon_profile"]),
        )
        for row in rows
    ]


def _append_bucket_key_filter(
    where: list[str],
    params: list[object],
    bucket_keys: list[tuple[str, str, str, str, str, str]] | None,
) -> None:
    if bucket_keys is None:
        return
    if not bucket_keys:
        where.append("1 = 0")
        return
    clauses = []
    for cluster, season, data_version, cycle, source_id, horizon_profile in bucket_keys:
        clauses.append(
            "(cluster = ? AND season = ? AND data_version = ? "
            "AND cycle = ? AND source_id = ? AND horizon_profile = ?)"
        )
        params.extend([cluster, season, data_version, cycle, source_id, horizon_profile])
    where.append("(" + " OR ".join(clauses) + ")")


def _fetch_buckets(
    conn: sqlite3.Connection,
    metric_identity: MetricIdentity,
    *,
    city_filter: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    cluster_filter: str | None = None,
    season_filter: str | Sequence[str] | None = None,
    data_version_filter: str | None = None,
) -> list[sqlite3.Row]:
    """Fetch metric-scoped buckets with sufficient maturity from calibration_pairs_v2."""
    affected_keys = None
    if city_filter or start_date or end_date:
        affected_keys = _fetch_affected_bucket_keys(
            conn,
            metric_identity,
            city_filter=city_filter,
            start_date=start_date,
            end_date=end_date,
            cluster_filter=cluster_filter,
            season_filter=season_filter,
            data_version_filter=data_version_filter,
        )
    where = [
        "temperature_metric = ?",
        "training_allowed = 1",
        "authority = 'VERIFIED'",
        "decision_group_id IS NOT NULL",
        "decision_group_id != ''",
        "p_raw IS NOT NULL",
    ]
    params: list[object] = [metric_identity.temperature_metric]
    if cluster_filter:
        where.append("cluster = ?")
        params.append(cluster_filter)
    _append_multi_filter(where, params, "season", season_filter)
    if data_version_filter:
        where.append("data_version = ?")
        params.append(data_version_filter)
    _append_bucket_key_filter(where, params, affected_keys)
    params.append(MIN_DECISION_GROUPS)
    return conn.execute(f"""
        SELECT cluster, season, data_version, cycle, source_id, horizon_profile,
               COUNT(DISTINCT decision_group_id) AS n_eff
        FROM calibration_pairs_v2
        WHERE {" AND ".join(where)}
        GROUP BY cluster, season, data_version, cycle, source_id, horizon_profile
        HAVING n_eff >= ?
    """, tuple(params)).fetchall()


def _fetch_pairs_for_bucket(
    conn: sqlite3.Connection,
    cluster: str,
    season: str,
    data_version: str,
    cycle: str,
    source_id: str,
    horizon_profile: str,
    metric_identity: MetricIdentity,
) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT p_raw, lead_days, outcome, range_label, decision_group_id
        FROM calibration_pairs_v2
        WHERE temperature_metric = ?
          AND training_allowed = 1
          AND authority = 'VERIFIED'
          AND cluster = ? AND season = ? AND data_version = ?
          AND cycle = ? AND source_id = ? AND horizon_profile = ?
          AND decision_group_id IS NOT NULL
          AND decision_group_id != ''
          AND p_raw IS NOT NULL
    """, (
        metric_identity.temperature_metric,
        cluster, season, data_version, cycle, source_id, horizon_profile,
    )).fetchall()


def _assert_platt_refit_preflight_ready(db_path: Path) -> None:
    report = build_platt_refit_preflight_report(db_path)
    if not report["ready"]:
        blocker_codes = sorted({item["code"] for item in report["blockers"]})
        raise RuntimeError(
            "Refusing live Platt v2 refit: platt-refit preflight is "
            f"{report['status']} ({', '.join(blocker_codes)})"
        )


def _fit_bucket(
    conn: sqlite3.Connection,
    cluster: str,
    season: str,
    data_version: str,
    cycle: str,
    source_id: str,
    horizon_profile: str,
    *,
    metric_identity: MetricIdentity,
    dry_run: bool,
    stats: RefitStatsV2,
) -> None:
    pairs = _fetch_pairs_for_bucket(
        conn, cluster, season, data_version,
        cycle, source_id, horizon_profile, metric_identity,
    )
    n_eff = len({p["decision_group_id"] for p in pairs})
    bucket_key = (
        f"{metric_identity.temperature_metric}:{cluster}:{season}:"
        f"{data_version}:{cycle}:{source_id}:{horizon_profile}"
    )

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
        cycle=cycle,
        source_id=source_id,
        horizon_profile=horizon_profile,
        input_space=cal.input_space,
    )
    stats.deactivated_rows += deactivated

    save_platt_model_v2(
        conn,
        metric_identity=metric_identity,
        cluster=cluster,
        season=season,
        data_version=data_version,
        cycle=cycle,
        source_id=source_id,
        horizon_profile=horizon_profile,
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


@capability("script_repair_write", lease=False)
@protects("INV-04")
def refit_v2(
    conn: sqlite3.Connection,
    *,
    metric_identity: MetricIdentity,
    dry_run: bool,
    force: bool,
    strict: bool = False,
    city_filter: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    cluster_filter: str | None = None,
    season_filter: str | Sequence[str] | None = None,
    data_version_filter: str | None = None,
) -> RefitStatsV2:
    stats = RefitStatsV2()

    print("=" * 70)
    print("PLATT V2 REFIT (calibration_pairs_v2 → platt_models_v2)")
    print("=" * 70)
    print(f"Mode:           {'DRY-RUN' if dry_run else 'LIVE WRITE'}")
    print(f"MetricIdentity: {metric_identity}")
    seasons_display = ",".join(_normalize_multi_filter(season_filter)) or "*"
    if city_filter or start_date or end_date or cluster_filter or season_filter or data_version_filter:
        print(
            "Bucket filter:   "
            f"city={city_filter or '*'} "
            f"date={start_date or '-inf'}..{end_date or '+inf'} "
            f"cluster={cluster_filter or '*'} "
            f"season={seasons_display} "
            f"data_version={data_version_filter or '*'}"
        )
    print(f"Min groups:     {MIN_DECISION_GROUPS}")

    buckets = _fetch_buckets(
        conn,
        metric_identity,
        city_filter=city_filter,
        start_date=start_date,
        end_date=end_date,
        cluster_filter=cluster_filter,
        season_filter=season_filter,
        data_version_filter=data_version_filter,
    )
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
    overall_start = time.monotonic()

    # Fix D (golden-knitting-wand.md Phase 1): per-bucket SAVEPOINT isolation.
    # Previously a single outer SAVEPOINT meant ANY bucket failure rolled back
    # ALL successfully-fit buckets. New pattern: outer v2_refit wraps the whole
    # batch; inner v2_refit_bucket_{idx} wraps each individual bucket so a NaN/
    # p_raw/lock failure in one bucket rolls back ONLY that bucket and the loop
    # continues. Successful buckets are committed at the end.
    #
    # CRITICAL: do NOT use `with conn:` around any SAVEPOINT block — Python
    # sqlite3 `with conn:` auto-commits on exit and silently releases SAVEPOINTs,
    # breaking atomicity. Use explicit conn.execute("SAVEPOINT ...") only.
    # (See memory: feedback_with_conn_nested_savepoint_audit.md)
    conn.execute("SAVEPOINT v2_refit")
    try:
        for bucket_idx, bucket in enumerate(buckets, start=1):
            cluster = bucket["cluster"]
            season = bucket["season"]
            data_version = bucket["data_version"]
            cycle = bucket["cycle"]
            source_id = bucket["source_id"]
            horizon_profile = bucket["horizon_profile"]
            bucket_key = (
                f"{metric_identity.temperature_metric}:{cluster}:{season}:"
                f"{data_version}:{cycle}:{source_id}:{horizon_profile}"
            )
            print(
                f"[{bucket_idx}/{len(buckets)}] starting bucket {bucket_key}",
                flush=True,
            )
            t0 = time.monotonic()
            sp_name = f"v2_refit_bucket_{bucket_idx}"
            conn.execute(f"SAVEPOINT {sp_name}")
            try:
                _fit_bucket(
                    conn, cluster, season, data_version,
                    cycle, source_id, horizon_profile,
                    metric_identity=metric_identity, dry_run=dry_run, stats=stats,
                )
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                elapsed = time.monotonic() - t0
                cumulative = time.monotonic() - overall_start
                print(
                    f"[{bucket_idx}/{len(buckets)}] done in {elapsed:.1f}s "
                    f"(cumulative {cumulative:.0f}s)",
                    flush=True,
                )
            except Exception as e:
                conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                stats.buckets_failed += 1
                failed_buckets.append(bucket_key)
                print(f"ERR {bucket_key}: {type(e).__name__}: {e}", flush=True)
                # Write failure record to refit_bucket_failures for operator triage.
                # Gated on `not dry_run` (PR #65 Codex P2 follow-up 2026-05-06):
                # the outer SAVEPOINT can persist on RELEASE if it began outside
                # an explicit transaction, so a dry-run preview must NOT mutate
                # state. Best-effort: if the failures table doesn't exist yet
                # (schema not migrated), log and continue — do not let the
                # ledger write kill the whole run.
                if not dry_run:
                    try:
                        conn.execute(
                            """
                            INSERT INTO refit_bucket_failures
                                (cluster, season, cycle, source_id, error_class, error_text, ts)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                cluster, season, cycle, source_id,
                                type(e).__name__, str(e)[:500],
                                datetime.now(timezone.utc).isoformat(),
                            ),
                        )
                    except Exception as ledger_err:
                        print(
                            f"WARN: could not write refit_bucket_failures row: {ledger_err}",
                            flush=True,
                        )

        # After loop: check strict mode before committing.
        strict_err: RuntimeError | None = None
        if failed_buckets:
            stats.refused = True
            if strict:
                # Strict mode triage rule (golden-knitting-wand.md Phase 1 fix-up
                # 2026-05-06, code-reviewer P0): ROLLBACK TO outer SAVEPOINT
                # ALSO rewinds the refit_bucket_failures INSERTs the inner
                # except-blocks just wrote. To preserve operator triage
                # visibility, dump the failure summary to stderr BEFORE the
                # rollback. Operator parses stderr; the DB ledger will be
                # empty after strict rollback (intentional; documented).
                # PR #65 Copilot follow-up 2026-05-06: explicit file=sys.stderr
                # on every print() in this branch — the comment above said
                # stderr but the calls defaulted to stdout, so any operator
                # parsing stderr would have missed the rollback summary.
                print(
                    f"\n=== --strict mode: ROLLING BACK ALL BUCKETS ===",
                    file=sys.stderr,
                    flush=True,
                )
                print(
                    f"Failed buckets ({len(failed_buckets)}):",
                    file=sys.stderr,
                    flush=True,
                )
                for fb in sorted(failed_buckets):
                    print(f"  ERR {fb}", file=sys.stderr, flush=True)
                print(
                    "NOTE: refit_bucket_failures table will be EMPTY after this "
                    "rollback (strict mode discards the ledger along with bucket "
                    "writes). Use these stderr lines for triage.",
                    file=sys.stderr,
                    flush=True,
                )
                # Roll back ALL buckets (including successful ones) so the DB
                # is left unchanged. Then release the outer SP and raise.
                conn.execute("ROLLBACK TO SAVEPOINT v2_refit")
                strict_err = RuntimeError(
                    f"--strict mode: {len(failed_buckets)} bucket(s) failed: "
                    + ", ".join(sorted(failed_buckets))
                )
            else:
                # Non-strict (default): commit successful buckets, report failures.
                print(
                    f"WARN: {len(failed_buckets)} bucket(s) failed; "
                    "successful buckets will be committed (non-strict mode). "
                    "See refit_bucket_failures table for details.",
                    flush=True,
                )

        conn.execute("RELEASE SAVEPOINT v2_refit")
        if not dry_run and strict_err is None:
            conn.commit()

        if strict_err is not None:
            raise strict_err

    except Exception:
        # Only reached for unexpected exceptions (not strict_err, which is raised
        # after the RELEASE). Guard: ROLLBACK only if the savepoint still exists.
        try:
            conn.execute("ROLLBACK TO SAVEPOINT v2_refit")
            conn.execute("RELEASE SAVEPOINT v2_refit")
        except Exception:
            pass
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
    strict: bool = False,
    temperature_metric: str = "all",
    city_filter: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    cluster_filter: str | None = None,
    season_filter: str | Sequence[str] | None = None,
    data_version_filter: str | None = None,
) -> dict[str, RefitStatsV2]:
    """Refit Platt v2 models for ALL METRIC_SPECS in one invocation.

    Returns per-metric stats dict keyed by temperature_metric string.
    Any spec that fails propagates the exception; caller sees non-zero exit.
    """
    per_metric: dict[str, RefitStatsV2] = {}
    specs = [
        spec for spec in METRIC_SPECS
        if temperature_metric == "all" or spec.identity.temperature_metric == temperature_metric
    ]
    for spec in specs:
        stats = refit_v2(
            conn,
            metric_identity=spec.identity,
            dry_run=dry_run,
            force=force,
            strict=strict,
            city_filter=city_filter,
            start_date=start_date,
            end_date=end_date,
            cluster_filter=cluster_filter,
            season_filter=season_filter,
            data_version_filter=data_version_filter,
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
    parser.add_argument(
        "--temperature-metric",
        dest="temperature_metric",
        choices=("high", "low", "all"),
        default="all",
        help="Metric track to refit (default: all).",
    )
    parser.add_argument("--cluster", dest="cluster", default=None, help="Limit refit to one cluster bucket.")
    parser.add_argument(
        "--season",
        dest="season",
        action="append",
        default=None,
        help="Limit refit to one season bucket; may be repeated.",
    )
    parser.add_argument("--city", dest="city", default=None, help="Derive affected bucket keys from one city.")
    parser.add_argument("--start-date", dest="start_date", default=None, help="Derive affected bucket keys on/after YYYY-MM-DD.")
    parser.add_argument("--end-date", dest="end_date", default=None, help="Derive affected bucket keys on/before YYYY-MM-DD.")
    parser.add_argument(
        "--data-version",
        dest="data_version",
        default=None,
        help="Limit refit to one calibration data_version bucket.",
    )
    parser.add_argument(
        "--strict", dest="strict", action="store_true", default=False,
        help=(
            "Fix D: fail-fast mode — if ANY bucket fails, roll back ALL buckets "
            "and exit non-zero. Default (off): per-bucket isolation; failed buckets "
            "roll back individually, successful buckets commit. Failures written "
            "to refit_bucket_failures table for triage."
        ),
    )
    args = parser.parse_args()

    db_path_for_preflight = Path(args.db_path) if args.db_path else SHARED_DB
    if not args.dry_run:
        try:
            _assert_platt_refit_preflight_ready(db_path_for_preflight)
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
            return 1

    if args.db_path:
        conn = sqlite3.connect(args.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
    else:
        conn = get_world_connection()
    init_schema(conn)
    apply_v2_schema(conn)

    try:
        per_metric = refit_all_v2(
            conn,
            dry_run=args.dry_run,
            force=args.force,
            strict=args.strict,
            temperature_metric=args.temperature_metric,
            city_filter=args.city,
            start_date=args.start_date,
            end_date=args.end_date,
            cluster_filter=args.cluster,
            season_filter=args.season,
            data_version_filter=args.data_version,
        )
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    any_refused = any(s.refused for s in per_metric.values())
    return 1 if any_refused else 0


if __name__ == "__main__":
    sys.exit(main())
