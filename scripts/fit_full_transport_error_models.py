# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Lifecycle: created=2026-05-25; last_reviewed=2026-05-25; last_reused=never
# Purpose: Fit PredictiveErrorModel posteriors for all (city, metric, season) buckets and persist to model_bias_ens_v2.
# Reuse: Requires isolated staging DB; inspect Fix-A cycle selection commit before reuse.
# Authority basis: Zeus #64 / #69 — fit + persist ft posteriors → model_bias_ens_v2.
#   Ported from reference: scripts/run_offline_platt_refit.py + onboard_cities.py
#   _run_fit_ens_bias_v2 logic. Uses Fix A's corrected metric-aware cycle selection
#   (commit 5260dd2809 on feat/ft-ship-64).
"""Fit PredictiveErrorModel posteriors for all (city, metric, season) buckets and
persist them to ``model_bias_ens_v2`` in an isolated staging / copy DB.

For each bucket the pipeline is:
  TIGGE (prior) residuals  ─┐
                             ├─ fit_city_predictive_error (Fix-A cycle selection)
  OpenData (live) residuals ─┘
        │
        ▼
  PredictiveErrorModel (bias_c, residual_sd_c, correction_strength, …)
        │
        ▼
  write_bias_model → model_bias_ens_v2

All 13 canonical extension columns are written alongside the legacy columns.

SAFETY RAILS
────────────
* ``--db`` is REQUIRED and must NOT resolve to the canonical prod DB paths
  (zeus-world.db / zeus-forecasts.db).  The script REFUSES if the path ends in
  either canonical name.
* Default is DRY-RUN — prints what would be written without touching the DB.
  Pass ``--commit`` to actually write.
* The canonical-fields migration (migrate_model_bias_ens_v2_canonical_fields.py)
  is run automatically (dry-run skipped, commit-mode applied) before fitting starts
  so the target DB always has the canonical columns.

USAGE
─────
    # dry-run (default) — discover cities + report planned fits
    python scripts/fit_full_transport_error_models.py --db /tmp/scratch.db

    # commit to scratch copy
    cp /path/to/ens_refit_full_2026-05-25.db /tmp/scratch.db
    python scripts/fit_full_transport_error_models.py --db /tmp/scratch.db --commit

    # single metric only
    python scripts/fit_full_transport_error_models.py \\
        --db /tmp/scratch.db --metric high --commit
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ZEUS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ZEUS_ROOT))

logger = logging.getLogger(__name__)

# ── data-version constants (matches onboard_cities.py) ──────────────────────
# Bug fix 2026-05-27: was hardcoded HIGH-only, causing --metric low to zero-cover
# every city (script looked up HIGH residuals on LOW snapshots). Metric-aware now.
_ENS_DATA_VERSIONS = {
    "high": {
        "live": "ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
        "prior": "tigge_mx2t6_local_calendar_day_max_v1",
    },
    "low": {
        "live": "ecmwf_opendata_mn2t3_local_calendar_day_min_v1",
        "prior": "tigge_mn2t6_local_calendar_day_min_v1",
    },
}
# Back-compat module-level names: kept as HIGH defaults for legacy callers; the
# fit loop below now resolves per-metric via _ENS_DATA_VERSIONS[metric].
_ENS_LIVE_DATA_VERSION = _ENS_DATA_VERSIONS["high"]["live"]
_ENS_PRIOR_DATA_VERSION = _ENS_DATA_VERSIONS["high"]["prior"]

# ── season definitions ───────────────────────────────────────────────────────
_SEASONS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("DJF", (12, 1, 2)),
    ("MAM", (3, 4, 5)),
    ("JJA", (6, 7, 8)),
    ("SON", (9, 10, 11)),
)

# ── production DB basenames that are NEVER valid targets ─────────────────────
_FORBIDDEN_BASENAMES = {"zeus-world.db", "zeus-forecasts.db", "zeus_trades.db"}


def _refuse_prod_db(db_path: Path) -> None:
    if db_path.name in _FORBIDDEN_BASENAMES:
        raise SystemExit(
            f"SAFETY: --db must point to a copy, not a production DB. "
            f"Refusing to write to {db_path}"
        )


def _get_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ZEUS_ROOT),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


def _fit_signature_hash(
    city: str,
    metric: str,
    season: str,
    live_dv: str,
    prior_dv: str,
    kappa: float,
    n_tig: int,
    n_opd: int,
) -> str:
    payload = json.dumps(
        {
            "city": city, "metric": metric, "season": season,
            "live_data_version": live_dv, "prior_data_version": prior_dv,
            "kappa": kappa, "n_tig": n_tig, "n_opd": n_opd,
        },
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _discover_cities(conn: sqlite3.Connection) -> list[str]:
    """Return sorted list of cities that have ensemble_snapshots_v2 data."""
    rows = conn.execute(
        "SELECT DISTINCT city FROM ensemble_snapshots_v2 ORDER BY city"
    ).fetchall()
    return [r[0] for r in rows]


def _observed_coverage_months(
    conn: sqlite3.Connection,
    *,
    city: str,
    data_version: str,
    metric: str,
    season_months: tuple[int, ...],
    settled_before: str,
) -> str:
    """CSV of distinct target-date months actually present in the prior fit slice.

    Mirrors the contributor / lead / cutoff filters used by load_bucket_residuals
    (legacy_tigge_null_passthrough population) so the recorded coverage matches the
    data the fit actually saw. Returns e.g. '3,4,5' or '' if none. Used to stamp
    coverage_months on the row for the reader's month-scope guard.
    """
    if not season_months:
        return ""
    ph = ",".join("?" for _ in season_months)
    try:
        rows = conn.execute(
            f"""
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
              AND CAST(SUBSTR(e.target_date, 6, 2) AS INTEGER) IN ({ph})
            ORDER BY m
            """,
            [city, data_version, metric, settled_before, settled_before, *season_months],
        ).fetchall()
        return ",".join(str(int(r[0])) for r in rows)
    except sqlite3.Error as exc:
        # Do NOT return "" — an empty coverage cell reads as 'no declared scope',
        # which would silently DISABLE the reader's month-scope guard for this row.
        # Stamp a malformed sentinel so read_bias_model fails CLOSED on it (the row
        # is never served until re-fit with valid coverage). Surface the error.
        logger.warning("coverage-months probe failed for %s/%s/%s: %s — stamping 'invalid'",
                       city, metric, season_months, exc)
        return "invalid"


def _apply_canonical_migration(conn: sqlite3.Connection) -> None:
    """Ensure canonical columns exist in the target DB (run migration inline)."""
    from scripts.migrate_model_bias_ens_v2_canonical_fields import migrate  # noqa: PLC0415
    result = migrate(conn, dry_run=False)
    applied = result.get("applied", [])
    skipped = result.get("skipped_already_present", [])
    if applied:
        logger.info("Canonical migration: added %d columns: %s", len(applied), applied)
    if skipped:
        logger.debug("Canonical migration: %d columns already present.", len(skipped))


def fit_all(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
    metric_filter: str | None = None,
    city_filter: str | None = None,
    kappa: float = 1.0,
) -> dict:
    """Fit posteriors for all (city, metric, season) buckets.

    Parameters
    ----------
    conn       : Connection to the isolated staging/copy DB.
    dry_run    : When True, fit and log but do NOT write to DB.
    metric_filter : If set, only fit this metric ('high' or 'low').
    city_filter : If set, only fit this city.
    kappa      : Transport-prior kappa (default 1.0 = full transport weight).

    Returns
    -------
    dict with keys: fitted, skipped, zero_coverage_cities, rows_written.
    """
    from src.calibration.ens_bias_repo import (  # noqa: PLC0415
        init_ens_bias_schema, write_bias_model,
    )
    from src.calibration.ens_error_model import (  # noqa: PLC0415
        fit_city_predictive_error, current_gate_set_hash, MIN_PRIOR_N,
        conservative_identity_model,
    )
    from src.calibration.ens_bias_repo import load_bucket_residuals  # noqa: PLC0415

    if not dry_run:
        # Both schema helpers write to DB — must not run on the read-only dry-run connection.
        init_ens_bias_schema(conn)
        _apply_canonical_migration(conn)
        conn.commit()

    metrics = [metric_filter] if metric_filter else ["high", "low"]
    cities = _discover_cities(conn) if city_filter is None else [city_filter]
    logger.info(
        "Producer: %d cities × %d metrics × %d seasons (dry_run=%s)",
        len(cities), len(metrics), len(_SEASONS), dry_run,
    )

    code_commit = _get_git_commit()
    gate_set_hash = current_gate_set_hash()
    today_str = datetime.now(timezone.utc).date().isoformat()
    transport_delta_policy = f"kappa={kappa};delta=paired_load_bucket_residuals"
    logger.info("gate_set_hash=%s code_commit=%s", gate_set_hash, code_commit[:12])

    fitted = 0
    skipped = 0
    rows_written = 0
    zero_coverage_cities: list[str] = []

    for city in cities:
        city_fitted = 0
        for season, months in _SEASONS:
            for metric in metrics:
                season_months = tuple(months)
                bucket_label = f"{city}/{metric}/{season}"
                # Bug fix 2026-05-27: resolve metric-aware data versions
                # (was using HIGH-only constants → zero LOW coverage).
                _dv = _ENS_DATA_VERSIONS[metric]
                _live_dv = _dv["live"]
                _prior_dv = _dv["prior"]
                try:
                    # Probe live residuals to get n counts for signature hash
                    tig_residuals = load_bucket_residuals(
                        conn, city=city, data_version=_prior_dv,
                        metric=metric, season_months=season_months,
                        require_verified=False,
                        contributor_policy="legacy_tigge_null_passthrough",
                    )
                    opd_residuals = load_bucket_residuals(
                        conn, city=city, data_version=_live_dv,
                        metric=metric, season_months=season_months,
                        contributor_policy="full_contributor_only",
                    )

                    if not tig_residuals:
                        logger.debug("No TIGGE prior residuals for %s — skipping.", bucket_label)
                        skipped += 1
                        continue

                    model = fit_city_predictive_error(
                        conn,
                        city=city,
                        live_data_version=_live_dv,
                        prior_data_version=_prior_dv,
                        season_months=season_months,
                        metric=metric,
                        kappa=kappa,
                    )

                    sig_hash = _fit_signature_hash(
                        city, metric, season,
                        _live_dv, _prior_dv,
                        kappa, len(tig_residuals), len(opd_residuals),
                    )
                    error_model_key = (
                        f"{city}|{metric}|{season}"
                        f"|full_transport_v1|{_live_dv}"
                    )

                    # Coverage months actually present in the prior fit window. A
                    # season-labelled row that only covered one month must declare it
                    # so the reader's month-scope guard can reject misapplication
                    # (COVERAGE_MISLABELED antibody, 2026-05-28).
                    coverage_months_csv = _observed_coverage_months(
                        conn, city=city, data_version=_prior_dv, metric=metric,
                        season_months=season_months, settled_before=today_str,
                    )

                    # C-handler: insufficient prior cannot support a confident learned
                    # correction (n_prior=1 → Qingdao class). Write an explicit
                    # identity/no-correction row instead of a confident city bias.
                    is_identity = len(tig_residuals) < MIN_PRIOR_N
                    # SD2 / Blocker C: an insufficient-prior identity must serve no
                    # learned shift AND a CONSERVATIVE-WIDE residual (never the 0.5C
                    # floor masquerading as confidence). conservative_identity_model
                    # zeros the correction and floors residual_sd_c/total to the wide
                    # CONSERVATIVE_RESIDUAL_FLOOR_C. Built once here; written below.
                    ident = conservative_identity_model(model) if is_identity else None

                    if dry_run:
                        logger.info(
                            "[dry-run] %s: %sbias_c=%.4f  effective_bias_c=%.4f"
                            "  residual_sd_c=%.4f  correction_strength=%.3f"
                            "  n_tig=%d  n_opd=%d  coverage=%s",
                            bucket_label,
                            "IDENTITY " if is_identity else "",
                            0.0 if is_identity else model.bias_c,
                            0.0 if is_identity else model.effective_bias_c,
                            (ident.residual_sd_c if is_identity else model.residual_sd_c),
                            0.0 if is_identity else model.correction_strength,
                            len(tig_residuals), len(opd_residuals), coverage_months_csv,
                        )
                    elif is_identity:
                        # Identity / no-correction: bias forced to 0, correction_strength 0,
                        # estimator tagged, authority STAGING. Served as "no learned shift",
                        # never as a confident correction.
                        write_bias_model(
                            conn,
                            city=city, season=season, metric=metric,
                            live_data_version=_live_dv, prior_data_version=_prior_dv,
                            posterior_bias_c=ident.bias_c, posterior_sd_c=ident.bias_sd_c,
                            n_live=len(opd_residuals), n_prior=len(tig_residuals),
                            weight_live=0.0,
                            estimator="ens_error_model.identity_insufficient_prior",
                            training_cutoff=today_str, recorded_at=today_str,
                            error_model_family="full_transport_v1",
                            error_model_key=error_model_key,
                            transport_delta_policy=transport_delta_policy,
                            bias_c=ident.bias_c, bias_sd_c=ident.bias_sd_c,
                            residual_sd_c=ident.residual_sd_c,
                            heterogeneity_var_c2=ident.heterogeneity_var_c2,
                            correction_strength=ident.correction_strength,
                            effective_bias_c=ident.effective_bias_c,
                            total_residual_sd_c=ident.total_residual_sd_c,
                            code_commit=code_commit, fit_signature_hash=sig_hash,
                            authority="STAGING",
                            gate_set_hash=gate_set_hash,
                            coverage_months=coverage_months_csv,
                        )
                        rows_written += 1
                    else:
                        write_bias_model(
                            conn,
                            city=city,
                            season=season,
                            metric=metric,
                            live_data_version=_live_dv,
                            prior_data_version=_prior_dv,
                            posterior_bias_c=model.bias_c,
                            posterior_sd_c=model.bias_sd_c,
                            n_live=len(opd_residuals),
                            n_prior=len(tig_residuals),
                            weight_live=0.0,  # not directly exposed by PredictiveErrorModel
                            estimator="ens_error_model.fit_city_predictive_error",
                            training_cutoff=today_str,
                            recorded_at=today_str,
                            # canonical extension fields
                            error_model_family="full_transport_v1",
                            error_model_key=error_model_key,
                            transport_delta_policy=transport_delta_policy,
                            bias_c=model.bias_c,
                            bias_sd_c=model.bias_sd_c,
                            residual_sd_c=model.residual_sd_c,
                            heterogeneity_var_c2=model.heterogeneity_var_c2,
                            correction_strength=model.correction_strength,
                            effective_bias_c=model.effective_bias_c,
                            total_residual_sd_c=model.total_residual_sd_c,
                            code_commit=code_commit,
                            fit_signature_hash=sig_hash,
                            authority="STAGING",
                            gate_set_hash=gate_set_hash,
                            coverage_months=coverage_months_csv,
                        )
                        rows_written += 1

                    fitted += 1
                    city_fitted += 1

                except (ValueError, RuntimeError) as exc:
                    logger.debug("Skipped %s: %s", bucket_label, exc)
                    skipped += 1

        if city_fitted == 0:
            zero_coverage_cities.append(city)

    if not dry_run:
        conn.commit()

    logger.info(
        "Producer done: fitted=%d skipped=%d rows_written=%d zero_coverage=%d",
        fitted, skipped, rows_written, len(zero_coverage_cities),
    )
    if zero_coverage_cities:
        logger.warning("Zero-coverage cities: %s", sorted(zero_coverage_cities))

    return {
        "fitted": fitted,
        "skipped": skipped,
        "rows_written": rows_written,
        "zero_coverage_cities": zero_coverage_cities,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db",
        required=True,
        type=Path,
        help="Path to isolated staging/copy DB (NEVER prod). Required.",
    )
    p.add_argument(
        "--commit",
        action="store_true",
        default=False,
        help="Write to DB. Default is dry-run (no writes).",
    )
    p.add_argument(
        "--metric",
        choices=("high", "low"),
        default=None,
        help="Fit only this metric (default: both).",
    )
    p.add_argument(
        "--city",
        default=None,
        help="Fit only this city (default: all discovered cities).",
    )
    p.add_argument(
        "--kappa",
        type=float,
        default=1.0,
        help="Transport prior kappa (default 1.0).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = build_parser().parse_args(argv)

    db_path = args.db.resolve()
    _refuse_prod_db(db_path)

    if not db_path.exists():
        logger.error("DB not found: %s", db_path)
        return 1

    dry_run = not args.commit
    if dry_run:
        logger.info("[DRY RUN] Fitting posteriors — use --commit to persist.")
    else:
        logger.info("Fitting and persisting posteriors to: %s", db_path)

    # Bug 6 fix (Zeus #64 PR #342): dry-run must not open RW / run PRAGMA journal_mode=WAL
    # (that creates -wal/-shm side-effect files even though nothing is committed).
    if dry_run:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 60000")
        # No WAL pragma in dry-run: read-only URI mode; WAL would create side-effect files.
    else:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 60000")
        conn.execute("PRAGMA journal_mode=WAL")

    try:
        result = fit_all(
            conn,
            dry_run=dry_run,
            metric_filter=args.metric,
            city_filter=args.city,
            kappa=args.kappa,
        )
    finally:
        conn.close()

    logger.info("Result: %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
