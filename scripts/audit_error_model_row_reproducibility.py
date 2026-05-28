# Created: 2026-05-27
# Last reused or audited: 2026-05-28
# Lifecycle: created=2026-05-27; last_reviewed=2026-05-28; last_reused=never
# Purpose: Read-only reproducibility gate — recompute every model_bias_ens_v2 row via current code and classify vs stored.
# Reuse: Read-only (both DBs mode=ro). Safe to re-run any time; allowlisted in db_writer_lock.
# Authority basis: operator audit 2026-05-27 — row reproducibility gate for full_transport_v1
#   stored rows. Recompute every stored model_bias_ens_v2 row using CURRENT code + CURRENT
#   DB and classify each row vs its stored value. Production rows that do not REPRODUCE
#   are not canonical under current code/gates and must not ship.
"""Canonical row reproducibility audit for model_bias_ens_v2 (READ-ONLY).

For every stored row in ``model_bias_ens_v2`` (world.db), recompute the canonical fit
using the **current** ``fit_city_predictive_error`` against the **current** source
residuals in forecasts.db, then classify the row:

  REPRODUCIBLE        — stored matches recompute within tolerance
  NON_REPRODUCIBLE    — stored differs from recompute beyond tolerance
                        (delta_bias matches ungated paired_delta → pre-gate fit)
  INSUFFICIENT_PRIOR  — recompute has n_prior < 2 (TIGGE source unusable)
  INSUFFICIENT_LIVE   — recompute has n_live < min_live_n (live ignored, prior-only)
  INSUFFICIENT_PAIRED — recompute has n_paired < MIN_PAIRED_N (transport disabled)
  COVERAGE_MISLABELED — stored season label disagrees with actual month coverage
  FIT_ERROR           — fit_city_predictive_error raised (e.g. zero TIGGE prior)

Outputs: CSV + summary to stdout/file. NEVER writes to any DB.

USAGE
─────
    python scripts/audit_error_model_row_reproducibility.py \
        --world-db state/zeus-world.db \
        --forecasts-db state/zeus-forecasts.db \
        --family full_transport_v1 \
        --metric high \
        --out docs/operations/ROW_REPRODUCIBILITY_AUDIT_2026-05-27.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

ZEUS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ZEUS_ROOT))

logger = logging.getLogger(__name__)

_SEASON_MONTHS: dict[str, tuple[int, ...]] = {
    "DJF": (12, 1, 2),
    "MAM": (3, 4, 5),
    "JJA": (6, 7, 8),
    "SON": (9, 10, 11),
}

# Tolerances (degC) for "REPRODUCIBLE" classification.
TOL_BIAS_C = 0.05
TOL_SD_C = 0.10

# Min counts — sourced from ens_error_model (single source of truth, not duplicated)
# so a threshold change there is reflected here automatically. Imported at module
# load; ZEUS_ROOT is on sys.path (set above) before this import runs.
from src.calibration.ens_error_model import (  # noqa: E402
    MIN_PAIRED_N,
    MIN_PRIOR_N,
    DEFAULT_MIN_LIVE_N as MIN_LIVE_N,
)


def _current_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ZEUS_ROOT),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


def _open_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _months_for_row(season: str, month: int) -> tuple[int, ...] | None:
    """Resolve season_months given (season, month).

    Producer convention (scripts/fit_full_transport_error_models.py): season is one of
    DJF/MAM/JJA/SON with month=0 sentinel. Returns season tuple; None if unknown season.
    """
    if month and 1 <= int(month) <= 12:
        # Month-specific row — coverage is the single month, not the season family.
        return (int(month),)
    return _SEASON_MONTHS.get(season)


def _audit_one_row(
    src_conn: sqlite3.Connection,
    *,
    city: str,
    season: str,
    month: int,
    metric: str,
    live_data_version: str,
    prior_data_version: str,
    settled_before: str | None,
    stored_bias_c: float,
    stored_sd_c: float,
    stored_n_live: int,
    stored_n_prior: int,
    stored_n_paired: int | None,
    stored_paired_delta_c: float | None,
) -> dict:
    """Recompute row + classify; never writes."""
    from src.calibration.ens_bias_repo import (
        load_bucket_residuals, load_paired_delta,
    )
    from src.calibration.ens_error_model import (
        fit_city_predictive_error, MIN_PAIRED_N as _MIN_PAIRED_N,
    )

    season_months = _months_for_row(season, month)
    if season_months is None:
        return {
            "status": "FIT_ERROR",
            "reason": f"unknown_season {season!r} month={month}",
            "recomputed_bias_c": None, "recomputed_sd_c": None,
            "recomputed_n_live": None, "recomputed_n_prior": None,
            "recomputed_n_paired": None, "recomputed_paired_delta_c": None,
            "coverage_months_observed": None,
        }

    common = dict(metric=metric, season_months=season_months,
                  settled_before=settled_before)

    # Load source residuals + paired delta directly to capture counts + coverage.
    try:
        tig = load_bucket_residuals(
            src_conn, city=city, data_version=prior_data_version,
            require_verified=False,
            contributor_policy="legacy_tigge_null_passthrough", **common,
        )
    except Exception as e:
        return {
            "status": "FIT_ERROR",
            "reason": f"load_tigge_failed:{type(e).__name__}:{e}",
            "recomputed_bias_c": None, "recomputed_sd_c": None,
            "recomputed_n_live": None, "recomputed_n_prior": None,
            "recomputed_n_paired": None, "recomputed_paired_delta_c": None,
            "coverage_months_observed": None,
        }
    try:
        opd = load_bucket_residuals(
            src_conn, city=city, data_version=live_data_version,
            contributor_policy="full_contributor_only", **common,
        )
    except Exception as e:
        opd = []
        logger.debug("load_opendata failed for %s/%s/%s: %s", city, season, metric, e)
    try:
        delta = load_paired_delta(
            src_conn, city=city, live_data_version=live_data_version,
            prior_data_version=prior_data_version, **common,
        )
    except Exception as e:
        delta = []
        logger.debug("load_paired_delta failed for %s/%s/%s: %s", city, season, metric, e)

    n_tig = len(tig)
    n_opd = len(opd)
    n_paired = len(delta)
    paired_delta_mean = (sum(delta) / n_paired) if n_paired else None

    # Coverage months actually observed (via direct snapshot probe).
    coverage_months: tuple[int, ...] = ()
    try:
        rows = src_conn.execute(
            """
            SELECT DISTINCT CAST(SUBSTR(e.target_date, 6, 2) AS INTEGER) AS m
            FROM ensemble_snapshots_v2 e
            JOIN settlements_v2 s
              ON s.city = e.city AND s.target_date = e.target_date
             AND s.temperature_metric = e.temperature_metric
            WHERE e.city = ? AND e.data_version = ? AND e.temperature_metric = ?
              AND e.lead_hours <= 48
              AND (e.contributes_to_target_extrema IS NULL
                   OR e.contributes_to_target_extrema = 1)
              AND COALESCE(e.boundary_ambiguous, 0) = 0
              AND (? IS NULL OR e.target_date < ?)
              AND CAST(SUBSTR(e.target_date, 6, 2) AS INTEGER) IN ({})
            ORDER BY m
            """.format(",".join("?" * len(season_months))),
            [city, prior_data_version, metric, settled_before, settled_before, *season_months],
        ).fetchall()
        coverage_months = tuple(int(r["m"]) for r in rows)
    except Exception as e:
        logger.debug("coverage probe failed: %s", e)

    # Classification gates
    if n_tig < MIN_PRIOR_N:
        return {
            "status": "INSUFFICIENT_PRIOR",
            "reason": f"n_prior={n_tig}<{MIN_PRIOR_N}",
            "recomputed_bias_c": None, "recomputed_sd_c": None,
            "recomputed_n_live": n_opd, "recomputed_n_prior": n_tig,
            "recomputed_n_paired": n_paired,
            "recomputed_paired_delta_c": paired_delta_mean,
            "coverage_months_observed": ",".join(str(m) for m in coverage_months),
        }

    # Run canonical fit
    try:
        model = fit_city_predictive_error(
            src_conn,
            city=city,
            live_data_version=live_data_version,
            prior_data_version=prior_data_version,
            season_months=season_months,
            metric=metric,
            settled_before=settled_before,
        )
    except Exception as e:
        return {
            "status": "FIT_ERROR",
            "reason": f"fit_failed:{type(e).__name__}:{e}",
            "recomputed_bias_c": None, "recomputed_sd_c": None,
            "recomputed_n_live": n_opd, "recomputed_n_prior": n_tig,
            "recomputed_n_paired": n_paired,
            "recomputed_paired_delta_c": paired_delta_mean,
            "coverage_months_observed": ",".join(str(m) for m in coverage_months),
        }

    recomputed_bias_c = float(model.bias_c)
    recomputed_sd_c = float(model.residual_sd_c)
    delta_bias = recomputed_bias_c - float(stored_bias_c)
    delta_sd = recomputed_sd_c - float(stored_sd_c)

    # Determine ungated-paired hypothesis: if delta_bias ≈ paired_delta and the recompute
    # would now gate it out, the stored row was fit pre-gate.
    pregate_signature = False
    if paired_delta_mean is not None and n_paired < _MIN_PAIRED_N:
        # Stored fit took the full paired delta; recompute gates it out.
        # Equivalent: stored - prior_only_recompute ≈ paired_delta_mean.
        if abs((-delta_bias) - paired_delta_mean) < 0.50:  # 0.5degC tolerance
            pregate_signature = True

    coverage_mislabel = False
    expected_months = set(season_months)
    if month and 1 <= int(month) <= 12:
        # Single-month rows: coverage must include that month at minimum.
        if int(month) not in set(coverage_months):
            coverage_mislabel = True
    else:
        # Season row: if only ONE month is in coverage but row is labeled as full season,
        # mislabel risk applies for forward use against other months.
        if len(coverage_months) == 1 and len(expected_months) > 1:
            coverage_mislabel = True

    # Status
    if abs(delta_bias) <= TOL_BIAS_C and abs(delta_sd) <= TOL_SD_C:
        status = "REPRODUCIBLE"
        reason = "within_tolerance"
    elif n_opd < MIN_LIVE_N and n_paired < _MIN_PAIRED_N and pregate_signature:
        status = "NON_REPRODUCIBLE"
        reason = f"pregate_ungated_paired_delta delta_bias={delta_bias:+.4f} paired_delta_mean={paired_delta_mean:+.4f}"
    elif n_paired < _MIN_PAIRED_N and abs(delta_bias) > TOL_BIAS_C:
        status = "NON_REPRODUCIBLE"
        reason = f"transport_gated_now delta_bias={delta_bias:+.4f} n_paired={n_paired}"
    elif abs(delta_bias) > TOL_BIAS_C:
        status = "NON_REPRODUCIBLE"
        reason = f"bias_diff delta_bias={delta_bias:+.4f}"
    elif abs(delta_sd) > TOL_SD_C:
        status = "NON_REPRODUCIBLE"
        reason = f"sd_diff delta_sd={delta_sd:+.4f}"
    else:
        status = "REPRODUCIBLE"
        reason = "within_tolerance"

    if coverage_mislabel and status == "REPRODUCIBLE":
        status = "COVERAGE_MISLABELED"
        reason = f"coverage_months={coverage_months} expected={tuple(sorted(expected_months))}"

    out = {
        "status": status,
        "reason": reason,
        "recomputed_bias_c": recomputed_bias_c,
        "recomputed_sd_c": recomputed_sd_c,
        "recomputed_n_live": n_opd,
        "recomputed_n_prior": n_tig,
        "recomputed_n_paired": n_paired,
        "recomputed_paired_delta_c": paired_delta_mean,
        "coverage_months_observed": ",".join(str(m) for m in coverage_months),
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--world-db", required=True, type=Path,
                    help="state/zeus-world.db (read-only)")
    ap.add_argument("--forecasts-db", required=True, type=Path,
                    help="state/zeus-forecasts.db (read-only — source residuals)")
    ap.add_argument("--family", default="full_transport_v1",
                    help="error_model_family filter (default: full_transport_v1)")
    ap.add_argument("--metric", default=None, choices=["high", "low", None],
                    help="restrict to one metric (default: both)")
    ap.add_argument("--city", default=None,
                    help="restrict to one city")
    ap.add_argument("--out", type=Path, default=None,
                    help="CSV output path (default: stdout-only summary)")
    ap.add_argument("--limit", type=int, default=None,
                    help="limit number of rows audited (debug)")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logger.info("audit_error_model_row_reproducibility @ %s", _current_commit())

    if not args.world_db.exists():
        raise SystemExit(f"world-db not found: {args.world_db}")
    if not args.forecasts_db.exists():
        raise SystemExit(f"forecasts-db not found: {args.forecasts_db}")

    world = _open_readonly(args.world_db)
    src = _open_readonly(args.forecasts_db)

    # Discover stored rows
    where = ["error_model_family = ?"]
    params: list[object] = [args.family]
    if args.metric:
        where.append("metric = ?")
        params.append(args.metric)
    if args.city:
        where.append("city = ?")
        params.append(args.city)

    sql = (
        "SELECT city, season, month, metric, "
        "live_data_version, prior_data_version, training_cutoff, "
        "bias_c, residual_sd_c, n_live, n_prior, n_paired, paired_delta_c, "
        "code_commit, fit_signature_hash, authority "
        f"FROM model_bias_ens_v2 WHERE {' AND '.join(where)} "
        "ORDER BY city, season, metric"
    )
    rows = world.execute(sql, params).fetchall()
    if args.limit:
        rows = rows[: args.limit]
    logger.info("audit set: %d rows", len(rows))

    results: list[dict] = []
    for r in rows:
        out = _audit_one_row(
            src,
            city=r["city"], season=r["season"], month=int(r["month"] or 0),
            metric=r["metric"],
            live_data_version=r["live_data_version"],
            prior_data_version=r["prior_data_version"],
            settled_before=r["training_cutoff"],
            stored_bias_c=float(r["bias_c"]) if r["bias_c"] is not None else 0.0,
            stored_sd_c=float(r["residual_sd_c"]) if r["residual_sd_c"] is not None else 0.0,
            stored_n_live=int(r["n_live"] or 0),
            stored_n_prior=int(r["n_prior"] or 0),
            stored_n_paired=(int(r["n_paired"]) if r["n_paired"] is not None else None),
            stored_paired_delta_c=(float(r["paired_delta_c"])
                                   if r["paired_delta_c"] is not None else None),
        )
        rec = {
            "city": r["city"], "season": r["season"], "month": r["month"],
            "metric": r["metric"],
            "family": args.family,
            "live_data_version": r["live_data_version"],
            "prior_data_version": r["prior_data_version"],
            "training_cutoff": r["training_cutoff"],
            "stored_bias_c": r["bias_c"],
            "stored_sd_c": r["residual_sd_c"],
            "stored_n_live": r["n_live"],
            "stored_n_prior": r["n_prior"],
            "stored_n_paired": r["n_paired"],
            "stored_paired_delta_c": r["paired_delta_c"],
            "stored_code_commit": r["code_commit"],
            "stored_fit_signature_hash": r["fit_signature_hash"],
            "stored_authority": r["authority"],
            **out,
        }
        # delta columns for convenience
        if out["recomputed_bias_c"] is not None:
            rec["delta_bias_c"] = out["recomputed_bias_c"] - float(r["bias_c"] or 0.0)
            rec["delta_sd_c"] = out["recomputed_sd_c"] - float(r["residual_sd_c"] or 0.0)
        else:
            rec["delta_bias_c"] = None
            rec["delta_sd_c"] = None
        results.append(rec)
        if rec["status"] != "REPRODUCIBLE":
            logger.info(
                "%-12s %s %s/%s  STORED bias=%.4f sd=%.4f  →  RECOMPUTE bias=%s sd=%s  status=%s  reason=%s",
                rec["city"], rec["season"], rec["metric"], rec["family"],
                float(r["bias_c"] or 0.0), float(r["residual_sd_c"] or 0.0),
                ("%.4f" % out["recomputed_bias_c"]) if out["recomputed_bias_c"] is not None else "NA",
                ("%.4f" % out["recomputed_sd_c"]) if out["recomputed_sd_c"] is not None else "NA",
                rec["status"], rec["reason"],
            )

    # Summary
    counts = Counter(rec["status"] for rec in results)
    logger.info("=" * 60)
    logger.info("AUDIT SUMMARY  (family=%s)  total=%d", args.family, len(results))
    for status in ("REPRODUCIBLE", "NON_REPRODUCIBLE", "INSUFFICIENT_PRIOR",
                   "INSUFFICIENT_LIVE", "INSUFFICIENT_PAIRED",
                   "COVERAGE_MISLABELED", "FIT_ERROR"):
        if counts.get(status):
            logger.info("  %-22s %d", status, counts[status])
    logger.info("=" * 60)

    # Write CSV
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", newline="") as f:
            if results:
                writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
                writer.writeheader()
                for rec in results:
                    writer.writerow(rec)
        logger.info("wrote %d rows -> %s", len(results), args.out)

    # Exit code: 0 if all REPRODUCIBLE, 1 otherwise (CI/ship-gate friendly).
    bad = sum(c for s, c in counts.items() if s != "REPRODUCIBLE")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
