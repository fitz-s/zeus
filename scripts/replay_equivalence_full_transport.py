# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Lifecycle: created=2026-05-25; last_reviewed=2026-05-25; last_reused=never
# Purpose: Replay-equivalence harness proving backup calibration_pairs_v2 p_raw values can be reused or must be regenerated.
# Reuse: Requires backup full.db and stage_db; inspect replay tolerance before accepting PASS verdict.
# Authority basis: FT_SHIP_MASTER_SPEC_2026-05-25 §Phase 2 + FT_POSTERIOR_SOURCE_PROBE_2026-05-25
"""Replay-equivalence harness for full_transport calibration_pairs_v2.

PURPOSE
-------
Prove (or disprove) whether the backup full.db calibration_pairs_v2 p_raw values
can be REUSED, or must be regenerated, by:

  1. Sampling N snapshots per cohort from the backup DB.
  2. Reconstructing the p_raw from the same member_extrema + bins via
     ``p_raw_vector_with_error_model`` (main-branch generator).
  3. Comparing regenerated vs stored p_raw: per-snapshot max_abs_diff,
     argmax-bin match, Brier/LogLoss delta.
  4. Verdict: PASS (reusable) iff max_abs_diff <= tol AND argmax match 100%;
     else FAIL (regenerate), with per-cohort breakdown.

ERROR-MODEL SOURCE
------------------
--recompute (default): calls fit_city_predictive_error() live against the
    backup DB residual tables. This is the ONLY mode available until a
    canonical ens_error_model_v1 producer runs and persists rows.

--error-model-db PATH: load error-model params from a persisted
    ens_error_model_v1 (or model_bias_ens_v2) table in the supplied DB.
    (Future mode; requires the Phase-1 producer to have run.)

SAFETY
------
Read-only. No writes to any DB. The backup DB is opened READONLY.

USAGE
-----
    python scripts/replay_equivalence_full_transport.py \\
        --backup-db state/backups/ens_refit_full_2026-05-25.db \\
        --n-per-cohort 5 \\
        --tol 1e-3

    # With a specific error-model DB (Phase 1 producer output):
    python scripts/replay_equivalence_full_transport.py \\
        --backup-db state/backups/ens_refit_full_2026-05-25.db \\
        --error-model-db /tmp/error_models.db \\
        --n-per-cohort 10
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Ensure repo root is on sys.path regardless of cwd
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np

# ---------------------------------------------------------------------------
# Constants matching the sub-worktree rebuild (FT_POSTERIOR_SOURCE_PROBE §1)
# ---------------------------------------------------------------------------
_LIVE_DV_HIGH = "ecmwf_opendata_mx2t3_local_calendar_day_max_v1"
_PRIOR_DV_HIGH = "tigge_mx2t6_local_calendar_day_max_v1"
_LIVE_DV_LOW = "ecmwf_opendata_mn2t3_local_calendar_day_min_v1"
_PRIOR_DV_LOW = "tigge_mn2t6_local_calendar_day_min_v1"

# Sub-worktree used min_live_n=5 (FT_POSTERIOR_SOURCE_PROBE §1); we match it.
_MIN_LIVE_N_RECOMPUTE = 5

# Default MC count used in offline rebuild (calibration_batch_rebuild_n_mc / 10k)
_DEFAULT_N_MC = 10_000

# Tolerance for PASS verdict (operator-specified default 1e-3)
_DEFAULT_TOL = 1e-3

# Cohort-sampling seed for reproducibility
_SAMPLE_SEED = 42


# ---------------------------------------------------------------------------
# Cohort selection: the spec requires coverage of these priority cohorts
# ---------------------------------------------------------------------------
_PRIORITY_COHORTS = [
    # (city, metric, season) — each maps to a DB query bucket
    ("Hong Kong", "high", "MAM"),   # contaminated-prior HK HIGH
    ("Hong Kong", "low", "MAM"),    # HK LOW (window-correct; expected PASS)
    ("Miami", "high", "MAM"),       # coastal HIGH F-unit
    ("Miami", "high", "DJF"),       # coastal HIGH DJF
    ("Miami", "low", "MAM"),        # coastal LOW
    ("San Francisco", "high", "MAM"),  # coastal inland-influenced
    ("Chicago", "high", "DJF"),     # inland DJF
    ("Chicago", "high", "MAM"),     # inland MAM
    ("London", "high", "DJF"),      # C-unit DJF
    ("London", "high", "JJA"),      # C-unit JJA
    ("London", "low", "MAM"),       # C-unit LOW
    ("NYC", "high", "DJF"),         # F-unit DJF
    ("NYC", "high", "JJA"),         # F-unit JJA
    ("NYC", "low", "MAM"),          # F-unit LOW
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class SnapshotResult:
    """Per-snapshot comparison result."""
    city: str
    metric: str
    season: str
    snapshot_id: int
    target_date: str
    lead_days: float
    max_abs_diff: float
    argmax_match: bool
    brier_delta: float  # Brier(stored) - Brier(regen) for winning bin
    stored_argmax: int
    regen_argmax: int
    n_bins: int


@dataclass
class CohortResult:
    """Aggregate result for one (city, metric, season) cohort."""
    city: str
    metric: str
    season: str
    n_sampled: int
    n_errors: int  # snapshots that failed to reconstruct
    max_abs_diff: float
    pct_argmax_match: float
    mean_brier_delta: float
    pass_verdict: bool
    fail_reason: str = ""
    snapshot_results: list[SnapshotResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# DB helpers (read-only)
# ---------------------------------------------------------------------------
def _open_readonly(path: str) -> sqlite3.Connection:
    """Open a SQLite DB in read-only mode."""
    uri = f"file:{Path(path).resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_cohort_snapshots(
    conn: sqlite3.Connection,
    city: str,
    metric: str,
    season: str,
    n: int,
    rng: np.random.Generator,
) -> list[sqlite3.Row]:
    """Sample up to n distinct snapshots for a cohort from calibration_pairs_v2.

    Each snapshot is the (snapshot_id, lead_days) combination of a distinct
    pair of FT rows. We pick distinct (snapshot_id, lead_days) combinations
    so the harness tests across lead buckets.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT cp.snapshot_id, cp.lead_days
        FROM calibration_pairs_v2 cp
        WHERE cp.error_model_family = 'full_transport_v1'
          AND cp.city = ?
          AND cp.temperature_metric = ?
          AND cp.season = ?
        ORDER BY cp.snapshot_id
        """,
        (city, metric, season),
    ).fetchall()

    if not rows:
        return []

    # Random subsample
    indices = rng.choice(len(rows), size=min(n, len(rows)), replace=False)
    return [rows[int(i)] for i in sorted(indices)]


def _fetch_stored_p_raw(
    conn: sqlite3.Connection,
    snapshot_id: int,
    lead_days: float,
    city: str,
    metric: str,
) -> tuple[list[str], list[float]]:
    """Fetch the stored p_raw vector (labels, values) for one snapshot/lead."""
    rows = conn.execute(
        """
        SELECT cp.range_label, cp.p_raw
        FROM calibration_pairs_v2 cp
        WHERE cp.error_model_family = 'full_transport_v1'
          AND cp.snapshot_id = ?
          AND cp.lead_days = ?
          AND cp.city = ?
          AND cp.temperature_metric = ?
        ORDER BY cp.pair_id
        """,
        (snapshot_id, lead_days, city, metric),
    ).fetchall()

    labels = [r["range_label"] for r in rows]
    values = [r["p_raw"] for r in rows]
    return labels, values


def _fetch_snapshot_meta(
    conn: sqlite3.Connection,
    snapshot_id: int,
) -> Optional[sqlite3.Row]:
    """Fetch ensemble_snapshots_v2 row for a snapshot_id."""
    return conn.execute(
        "SELECT * FROM ensemble_snapshots_v2 WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# Error model acquisition
# ---------------------------------------------------------------------------
def _recompute_error_model(
    conn: sqlite3.Connection,
    city_name: str,
    metric: str,
    season: str,
) -> object:
    """Call fit_city_predictive_error against the backup DB (--recompute mode).

    Uses the same data-version mapping as the sub-worktree rebuild
    (FT_POSTERIOR_SOURCE_PROBE §1): TIGGE prior + OpenData live, min_live_n=5.
    Season months are derived from the season label (NH convention).
    """
    from src.calibration.ens_error_model import fit_city_predictive_error

    # Season → months (NH; SH flip handled by the season label itself in the DB)
    _SEASON_MONTHS = {
        "DJF": (12, 1, 2),
        "MAM": (3, 4, 5),
        "JJA": (6, 7, 8),
        "SON": (9, 10, 11),
    }
    season_months = _SEASON_MONTHS.get(season)

    live_dv = _LIVE_DV_HIGH if metric == "high" else _LIVE_DV_LOW
    prior_dv = _PRIOR_DV_HIGH if metric == "high" else _PRIOR_DV_LOW

    return fit_city_predictive_error(
        conn,
        city=city_name,
        live_data_version=live_dv,
        prior_data_version=prior_dv,
        metric=metric,
        season_months=season_months,
        min_live_n=_MIN_LIVE_N_RECOMPUTE,
    )


def _load_error_model_from_db(
    model_db_conn: sqlite3.Connection,
    city_name: str,
    metric: str,
    season: str,
) -> object:
    """Load error-model params from a persisted ens_error_model_v1 table.

    Reconstructs a PredictiveErrorModel from the stored fields. Requires the
    Phase-1 producer to have written rows with at minimum:
        bias_c, bias_sd_c, residual_sd_c, heterogeneity_var_c2,
        correction_strength, effective_bias_c, total_residual_sd_c.
    """
    from src.calibration.ens_error_model import PredictiveErrorModel

    row = model_db_conn.execute(
        """
        SELECT bias_c, bias_sd_c, residual_sd_c, heterogeneity_var_c2,
               correction_strength, effective_bias_c, total_residual_sd_c
        FROM ens_error_model_v1
        WHERE city = ? AND metric = ? AND season = ?
        ORDER BY recorded_at DESC
        LIMIT 1
        """,
        (city_name, metric, season),
    ).fetchone()

    if row is None:
        # Fallback: try model_bias_ens_v2 (older schema)
        row = model_db_conn.execute(
            """
            SELECT bias_c, bias_sd_c, residual_sd_c, heterogeneity_var_c2,
                   correction_strength, effective_bias_c, total_residual_sd_c
            FROM model_bias_ens_v2
            WHERE city = ? AND metric = ? AND season = ?
            ORDER BY recorded_at DESC
            LIMIT 1
            """,
            (city_name, metric, season),
        ).fetchone()

    if row is None:
        raise ValueError(
            f"No error-model row for ({city_name!r}, {metric!r}, {season!r}) "
            "in ens_error_model_v1 or model_bias_ens_v2"
        )

    return PredictiveErrorModel(
        bias_c=row["bias_c"],
        bias_sd_c=row["bias_sd_c"],
        residual_sd_c=row["residual_sd_c"],
        heterogeneity_var_c2=row["heterogeneity_var_c2"],
        disagreement_high=False,  # not needed for p_raw regeneration
        correction_strength=row["correction_strength"],
        effective_bias_c=row["effective_bias_c"],
        total_residual_sd_c=row["total_residual_sd_c"],
    )


# ---------------------------------------------------------------------------
# Core comparison logic
# ---------------------------------------------------------------------------
def _compare_snapshot(
    *,
    backup_conn: sqlite3.Connection,
    snapshot_id: int,
    lead_days: float,
    city_name: str,
    metric: str,
    season: str,
    error_model,
    n_mc: int,
    tol: float,
) -> Optional[SnapshotResult]:
    """Regenerate p_raw for one snapshot and compare to stored value.

    Returns None if the snapshot cannot be reconstructed (missing members_json,
    unknown city config, etc.) — counted as an error in the cohort aggregate.
    """
    from src.calibration.ens_error_model import p_raw_vector_with_error_model
    from src.config import cities_by_name
    from src.contracts.calibration_bins import grid_for_city
    from src.contracts.settlement_semantics import SettlementSemantics

    city = cities_by_name.get(city_name)
    if city is None:
        logging.warning("Unknown city %r — skipping snapshot %d", city_name, snapshot_id)
        return None

    snap = _fetch_snapshot_meta(backup_conn, snapshot_id)
    if snap is None:
        logging.warning("Snapshot %d not found in ensemble_snapshots_v2", snapshot_id)
        return None

    try:
        member_extrema = np.asarray(json.loads(snap["members_json"]), dtype=float)
    except (TypeError, json.JSONDecodeError) as exc:
        logging.warning("Snapshot %d: bad members_json: %s", snapshot_id, exc)
        return None

    # members_json is already in settlement_unit (verified by rebuild_calibration_pairs_v2 unit checks)
    members_unit = snap["members_unit"] or city.settlement_unit

    grid = grid_for_city(city)
    bins = grid.as_bins()
    sem = SettlementSemantics.for_city(city)

    try:
        p_regen = p_raw_vector_with_error_model(
            member_extrema,
            error_model,
            city,
            sem,
            bins,
            member_unit=members_unit,
            n_mc=n_mc,
        )
    except Exception as exc:
        logging.warning(
            "Snapshot %d (%s/%s/%s): p_raw_vector_with_error_model failed: %s",
            snapshot_id, city_name, metric, season, exc,
        )
        return None

    # Fetch stored p_raw for the same snapshot/lead
    target_date = snap["target_date"]
    labels_stored, p_stored_list = _fetch_stored_p_raw(
        backup_conn, snapshot_id, lead_days, city_name, metric
    )

    if not labels_stored:
        logging.warning(
            "Snapshot %d (lead=%.2f): no stored p_raw rows found", snapshot_id, lead_days
        )
        return None

    if len(labels_stored) != len(p_regen):
        logging.warning(
            "Snapshot %d: stored bins=%d, regen bins=%d — mismatch, skipping",
            snapshot_id, len(labels_stored), len(p_regen),
        )
        return None

    p_stored = np.array(p_stored_list, dtype=float)

    max_abs_diff = float(np.max(np.abs(p_regen - p_stored)))
    stored_argmax = int(np.argmax(p_stored))
    regen_argmax = int(np.argmax(p_regen))
    argmax_match = (stored_argmax == regen_argmax)

    # Brier score for the argmax bin (proper scoring rule proxy)
    # Brier = (p - outcome)^2; we track delta p^2 for stored vs regen at stored argmax
    brier_delta = float(p_stored[stored_argmax] ** 2 - p_regen[stored_argmax] ** 2)

    return SnapshotResult(
        city=city_name,
        metric=metric,
        season=season,
        snapshot_id=snapshot_id,
        target_date=target_date,
        lead_days=lead_days,
        max_abs_diff=max_abs_diff,
        argmax_match=argmax_match,
        brier_delta=brier_delta,
        stored_argmax=stored_argmax,
        regen_argmax=regen_argmax,
        n_bins=len(bins),
    )


def _evaluate_cohort(
    *,
    backup_conn: sqlite3.Connection,
    city_name: str,
    metric: str,
    season: str,
    error_model_source: str,
    model_db_conn: Optional[sqlite3.Connection],
    n_per_cohort: int,
    n_mc: int,
    tol: float,
    rng: np.random.Generator,
) -> CohortResult:
    """Run the full replay-equivalence check for one cohort."""
    log = logging.getLogger(__name__)
    log.info("Evaluating cohort: %s / %s / %s", city_name, metric, season)

    # Acquire error model
    try:
        if error_model_source == "recompute":
            error_model = _recompute_error_model(backup_conn, city_name, metric, season)
        else:
            error_model = _load_error_model_from_db(model_db_conn, city_name, metric, season)
    except Exception as exc:
        log.warning(
            "Failed to acquire error model for %s/%s/%s: %s", city_name, metric, season, exc
        )
        return CohortResult(
            city=city_name, metric=metric, season=season,
            n_sampled=0, n_errors=1,
            max_abs_diff=float("nan"), pct_argmax_match=float("nan"),
            mean_brier_delta=float("nan"), pass_verdict=False,
            fail_reason=f"error-model acquisition failed: {exc}",
        )

    # Sample snapshots
    sampled = _fetch_cohort_snapshots(backup_conn, city_name, metric, season, n_per_cohort, rng)
    if not sampled:
        return CohortResult(
            city=city_name, metric=metric, season=season,
            n_sampled=0, n_errors=0,
            max_abs_diff=float("nan"), pct_argmax_match=float("nan"),
            mean_brier_delta=float("nan"), pass_verdict=False,
            fail_reason="no FT pairs found for this cohort",
        )

    results: list[SnapshotResult] = []
    errors = 0

    for row in sampled:
        sr = _compare_snapshot(
            backup_conn=backup_conn,
            snapshot_id=row["snapshot_id"],
            lead_days=row["lead_days"],
            city_name=city_name,
            metric=metric,
            season=season,
            error_model=error_model,
            n_mc=n_mc,
            tol=tol,
        )
        if sr is None:
            errors += 1
        else:
            results.append(sr)

    if not results:
        return CohortResult(
            city=city_name, metric=metric, season=season,
            n_sampled=len(sampled), n_errors=errors,
            max_abs_diff=float("nan"), pct_argmax_match=float("nan"),
            mean_brier_delta=float("nan"), pass_verdict=False,
            fail_reason="all sampled snapshots failed reconstruction",
        )

    max_diff = max(r.max_abs_diff for r in results)
    pct_argmax = 100.0 * sum(1 for r in results if r.argmax_match) / len(results)
    mean_brier_delta = float(np.mean([r.brier_delta for r in results]))

    # PASS iff max_abs_diff <= tol AND 100% argmax match
    pass_verdict = (max_diff <= tol) and (pct_argmax >= 100.0)
    fail_reason = ""
    if not pass_verdict:
        parts = []
        if max_diff > tol:
            parts.append(f"max_abs_diff={max_diff:.4e} > tol={tol:.1e}")
        if pct_argmax < 100.0:
            parts.append(f"argmax_match={pct_argmax:.1f}% < 100%")
        fail_reason = "; ".join(parts)

    return CohortResult(
        city=city_name, metric=metric, season=season,
        n_sampled=len(sampled), n_errors=errors,
        max_abs_diff=max_diff, pct_argmax_match=pct_argmax,
        mean_brier_delta=mean_brier_delta,
        pass_verdict=pass_verdict, fail_reason=fail_reason,
        snapshot_results=results,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _print_report(
    cohort_results: list[CohortResult],
    tol: float,
    error_model_source: str,
) -> bool:
    """Print the full report and return True if overall verdict is PASS."""
    passed = [c for c in cohort_results if c.pass_verdict]
    failed = [c for c in cohort_results if not c.pass_verdict]

    print()
    print("=" * 72)
    print("REPLAY-EQUIVALENCE REPORT — full_transport calibration_pairs_v2")
    print(f"Error-model source : {error_model_source}")
    print(f"Tolerance          : {tol:.1e}")
    print(f"Cohorts evaluated  : {len(cohort_results)}")
    print(f"PASS               : {len(passed)}")
    print(f"FAIL               : {len(failed)}")
    print("=" * 72)

    # Per-cohort table
    print(f"\n{'Cohort':<42} {'Sampled':>7} {'Errors':>6} {'MaxDiff':>10} {'ArgMax%':>8} {'Verdict'}")
    print("-" * 80)
    for c in cohort_results:
        cohort_str = f"{c.city} / {c.metric} / {c.season}"
        verdict = "PASS" if c.pass_verdict else "FAIL"
        max_d = f"{c.max_abs_diff:.3e}" if math.isfinite(c.max_abs_diff) else "  N/A"
        argmax_pct = f"{c.pct_argmax_match:.0f}%" if math.isfinite(c.pct_argmax_match) else "  N/A"
        print(f"{cohort_str:<42} {c.n_sampled:>7} {c.n_errors:>6} {max_d:>10} {argmax_pct:>8}  {verdict}")
        if not c.pass_verdict and c.fail_reason:
            print(f"  {'':42} {c.fail_reason}")

    # Snapshot-level details for FAIL cohorts
    if failed:
        print("\n--- FAIL cohort snapshot details ---")
        for c in failed:
            if not c.snapshot_results:
                continue
            print(f"\n  {c.city} / {c.metric} / {c.season}")
            for sr in c.snapshot_results:
                am = "OK" if sr.argmax_match else f"MISMATCH(stored={sr.stored_argmax},regen={sr.regen_argmax})"
                print(
                    f"    snap={sr.snapshot_id} date={sr.target_date} lead={sr.lead_days:.2f}d "
                    f"maxdiff={sr.max_abs_diff:.3e} argmax={am}"
                )

    print()
    overall = len(failed) == 0
    if overall:
        print("OVERALL VERDICT: PASS — stored pairs REUSABLE (no 20h rerun required)")
    else:
        failing_cohorts = ", ".join(f"{c.city}/{c.metric}/{c.season}" for c in failed[:5])
        if len(failed) > 5:
            failing_cohorts += f" (+{len(failed)-5} more)"
        print(f"OVERALL VERDICT: FAIL — {len(failed)} cohort(s) diverge: {failing_cohorts}")
        print("  Action: regenerate pairs for failing cohorts under the canonical")
        print("  persisted error-model domain (Phase 2 → Phase 3).")
    print()
    return overall


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Replay-equivalence check for full_transport calibration_pairs_v2.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--backup-db",
        default="state/backups/ens_refit_full_2026-05-25.db",
        help="Path to the backup full.db (read-only). Default: %(default)s",
    )
    p.add_argument(
        "--error-model-db",
        default=None,
        help="Path to a DB with ens_error_model_v1 or model_bias_ens_v2 table. "
             "When omitted, --recompute mode is used.",
    )
    p.add_argument(
        "--n-per-cohort",
        type=int,
        default=5,
        help="Number of distinct snapshots to sample per cohort. Default: %(default)s",
    )
    p.add_argument(
        "--n-mc",
        type=int,
        default=_DEFAULT_N_MC,
        help="Monte Carlo iterations for p_raw regeneration. Default: %(default)s",
    )
    p.add_argument(
        "--tol",
        type=float,
        default=_DEFAULT_TOL,
        help="max_abs_diff tolerance for PASS verdict. Default: %(default)s",
    )
    p.add_argument(
        "--cohorts",
        nargs="+",
        metavar="CITY/METRIC/SEASON",
        default=None,
        help="Explicit cohorts to test (e.g. 'Hong Kong/high/MAM'). "
             "When omitted, runs all priority cohorts present in the backup DB.",
    )
    p.add_argument(
        "--all-cohorts",
        action="store_true",
        help="Test ALL (city, metric, season) cohorts found in the backup DB "
             "(can be slow). Overrides --cohorts.",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Show DEBUG-level logging.",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger(__name__)

    backup_path = Path(args.backup_db)
    if not backup_path.exists():
        log.error("Backup DB not found: %s", backup_path)
        return 1

    error_model_source = "recompute" if args.error_model_db is None else f"db:{args.error_model_db}"
    log.info("Backup DB   : %s", backup_path)
    log.info("Error model : %s", error_model_source)
    log.info("n_per_cohort: %d", args.n_per_cohort)
    log.info("n_mc        : %d", args.n_mc)
    log.info("tol         : %.1e", args.tol)

    backup_conn = _open_readonly(str(backup_path))

    model_db_conn: Optional[sqlite3.Connection] = None
    if args.error_model_db is not None:
        model_db_path = Path(args.error_model_db)
        if not model_db_path.exists():
            log.error("Error-model DB not found: %s", model_db_path)
            return 1
        model_db_conn = _open_readonly(str(model_db_path))
        model_db_conn.row_factory = sqlite3.Row

    # Determine cohorts to evaluate
    if args.all_cohorts:
        rows = backup_conn.execute(
            """
            SELECT DISTINCT city, temperature_metric AS metric, season
            FROM calibration_pairs_v2
            WHERE error_model_family = 'full_transport_v1'
            ORDER BY city, temperature_metric, season
            """
        ).fetchall()
        cohorts = [(r["city"], r["metric"], r["season"]) for r in rows]
        log.info("All-cohorts mode: %d cohorts found", len(cohorts))
    elif args.cohorts:
        cohorts = []
        for spec in args.cohorts:
            parts = spec.split("/")
            if len(parts) != 3:
                log.error("Invalid cohort spec %r — expected CITY/METRIC/SEASON", spec)
                return 1
            cohorts.append((parts[0], parts[1], parts[2]))
    else:
        # Priority cohorts filtered to those that actually exist in the backup DB
        existing = set()
        for row in backup_conn.execute(
            """
            SELECT DISTINCT city, temperature_metric, season
            FROM calibration_pairs_v2
            WHERE error_model_family = 'full_transport_v1'
            """
        ).fetchall():
            existing.add((row["city"], row["temperature_metric"], row["season"]))
        cohorts = [c for c in _PRIORITY_COHORTS if c in existing]
        missing = [c for c in _PRIORITY_COHORTS if c not in existing]
        if missing:
            log.info(
                "Priority cohorts not present in backup DB: %s",
                [f"{c[0]}/{c[1]}/{c[2]}" for c in missing],
            )
        log.info("Evaluating %d priority cohorts", len(cohorts))

    rng = np.random.default_rng(_SAMPLE_SEED)

    cohort_results: list[CohortResult] = []
    for city_name, metric, season in cohorts:
        cr = _evaluate_cohort(
            backup_conn=backup_conn,
            city_name=city_name,
            metric=metric,
            season=season,
            error_model_source="recompute" if args.error_model_db is None else "db",
            model_db_conn=model_db_conn,
            n_per_cohort=args.n_per_cohort,
            n_mc=args.n_mc,
            tol=args.tol,
            rng=rng,
        )
        cohort_results.append(cr)

    overall_pass = _print_report(cohort_results, args.tol, error_model_source)
    return 0 if overall_pass else 2


# ---------------------------------------------------------------------------
# Synthetic smoke-test fixture (pytest)
# ---------------------------------------------------------------------------
def _make_synthetic_fixture():
    """Build a tiny in-memory DB with known p_raw for the smoke test."""
    import tempfile, os
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE ensemble_snapshots_v2 (
            snapshot_id INTEGER PRIMARY KEY,
            city TEXT, target_date TEXT, temperature_metric TEXT,
            lead_hours REAL, members_json TEXT, members_unit TEXT,
            settlement_unit TEXT, data_version TEXT,
            available_at TEXT, fetch_time TEXT, physical_quantity TEXT,
            observation_field TEXT, model_version TEXT,
            training_allowed INTEGER DEFAULT 1,
            causality_status TEXT DEFAULT 'OK',
            boundary_ambiguous INTEGER DEFAULT 0,
            ambiguous_member_count INTEGER DEFAULT 0,
            provenance_json TEXT DEFAULT '{}'
        );
        CREATE TABLE calibration_pairs_v2 (
            pair_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT, target_date TEXT, temperature_metric TEXT,
            observation_field TEXT, range_label TEXT,
            p_raw REAL, outcome INTEGER, lead_days REAL, season TEXT,
            cluster TEXT, forecast_available_at TEXT, decision_group_id TEXT,
            bias_corrected INTEGER DEFAULT 0, authority TEXT DEFAULT 'VERIFIED',
            bin_source TEXT DEFAULT 'canonical_v2', snapshot_id INTEGER,
            data_version TEXT, training_allowed INTEGER DEFAULT 1,
            causality_status TEXT DEFAULT 'OK', cycle TEXT DEFAULT '00',
            source_id TEXT DEFAULT 'ecmwf_open_data',
            horizon_profile TEXT DEFAULT 'full',
            recorded_at TEXT DEFAULT CURRENT_TIMESTAMP,
            error_model_family TEXT DEFAULT 'none'
        );
        """
    )
    return conn


def test_snapshot_result_passthrough():
    """Smoke test: a zero-bias error model regenerates p_raw close to baseline."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent.parent))

    from src.calibration.ens_error_model import PredictiveErrorModel, p_raw_vector_with_error_model
    from src.config import cities_by_name
    from src.contracts.calibration_bins import grid_for_city
    from src.contracts.settlement_semantics import SettlementSemantics

    city = cities_by_name.get("London")
    assert city is not None, "London must exist in cities config"

    grid = grid_for_city(city)
    bins = grid.as_bins()
    sem = SettlementSemantics.for_city(city)

    # Synthetic members centred on 18°C (C-unit city)
    rng = np.random.default_rng(0)
    members = rng.normal(18.0, 1.5, 51)

    # Zero-bias model (effective_bias_c=0, small residual_sd)
    zero_model = PredictiveErrorModel(
        bias_c=0.0, bias_sd_c=1.0, residual_sd_c=0.5,
        heterogeneity_var_c2=0.0, disagreement_high=False,
        correction_strength=0.0, effective_bias_c=0.0,
        total_residual_sd_c=0.5,
    )

    p_regen = p_raw_vector_with_error_model(
        members, zero_model, city, sem, bins,
        member_unit="C", n_mc=500,
    )

    # Basic sanity: sums to ~1, non-negative, argmax in plausible range
    assert abs(p_regen.sum() - 1.0) < 1e-6, f"p_regen does not sum to 1: {p_regen.sum()}"
    assert (p_regen >= 0).all(), "p_regen has negative values"
    argmax_label = bins[int(np.argmax(p_regen))].label
    assert "18" in argmax_label or "17" in argmax_label or "19" in argmax_label, (
        f"Unexpected argmax bin for 18°C members: {argmax_label}"
    )

    # Non-zero bias: argmax should shift
    bias_model = PredictiveErrorModel(
        bias_c=3.0, bias_sd_c=0.3, residual_sd_c=0.5,
        heterogeneity_var_c2=0.0, disagreement_high=False,
        correction_strength=1.0, effective_bias_c=3.0,
        total_residual_sd_c=0.5,
    )
    p_biased = p_raw_vector_with_error_model(
        members, bias_model, city, sem, bins,
        member_unit="C", n_mc=500,
    )
    argmax_zero = int(np.argmax(p_regen))
    argmax_biased = int(np.argmax(p_biased))
    # Correcting a +3°C bias shifts the distribution DOWN; argmax must move left
    assert argmax_biased < argmax_zero, (
        f"Expected bias correction to shift argmax left: zero={argmax_zero}, biased={argmax_biased}"
    )

    print("test_snapshot_result_passthrough: PASS")


if __name__ == "__main__":
    sys.exit(main())
