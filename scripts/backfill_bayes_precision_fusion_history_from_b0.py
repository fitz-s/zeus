#!/usr/bin/env python3
# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: Seed raw_model_forecasts (endpoint=previous_runs, training_allowed=0) from the proven B0 fixed-lead multi-model dataset so BayesPrecisionFusionHistoryProvider has walk-forward history immediately.
# Reuse: --b0 and --db are both REQUIRED; --b0 must point at B0_multilead_dataset.json; --db must point at target zeus-forecasts.db. Use --dry-run first. Never run against live DB without operator intent.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: docs/the_path/BACKFILL_NOW.md + BAYES_PRECISION_FUSION_SPEC.md §3/§5/§6 F1 + BLOCKER 4
#   (product-identity provenance, Fitz Constraint #4). Operator BLOCKER (the_path PR review
#   2026-06-08): the seed MUST construct DETERMINISTIC product identity per row using the SAME
#   identity construction the live download writer uses, persist via the SAME conflict-guarded
#   idempotent path, and REFUSE (hard error) rather than write identity-less rows.
"""Seed raw_model_forecasts (endpoint='previous_runs') from the proven fixed-lead
multi-model dataset B0_multilead_dataset.json so the live BayesPrecisionFusionHistoryProvider has
its walk-forward training history IMMEDIATELY (no 25-day forward wait). Fusion then
reaches T2_BAYES instead of EQUAL_WEIGHT on the very next materialize cycle.

PROVENANCE / SAFETY:
  * Writes ONLY raw_model_forecasts training-history rows
    (training_allowed=0) — NOT a posterior/readiness, order, or training-truth table.
    It changes no posterior; only BayesPrecisionFusionHistoryProvider may consume
    these rows as causally available training history.
  * --db is REQUIRED and never defaults to the live path: the operator points it at
    the target zeus-forecasts.db explicitly. NEVER run against a DB you must not write.
  * No-leak is enforced at SERVE time by BayesPrecisionFusionHistoryProvider (target_date < decision_date);
    this seed is genuine historical fixed-lead previous-runs data, correctly tagged.

PRODUCT IDENTITY (operator BLOCKER, the_path PR review 2026-06-08):
  Each seeded row carries the FULL deterministic product identity (product_id,
  request_url_hash, source_id, source_family, provider, model_name, request_params_json,
  lat/lon/timezone requested, cell_selection, elevation_param, downscaling_policy,
  endpoint_mode='previous_runs', model_domain_hash, coverage_status) constructed by the
  SAME _bayes_precision_fusion_product_identity the LIVE download writer uses (src/data/bayes_precision_fusion_download).
  So a seed row is byte-for-byte identity-compatible with a live-fetched row on the new
  UNIQUE(model,product_id,request_url_hash,city,target_date,metric,source_cycle_time,endpoint).

IDEMPOTENCY: the OLD script inserted NULL product_id/request_url_hash; under the new UNIQUE,
  SQLite treats NULL!=NULL, so a 2nd run DUPLICATED every seed row -> doubled n_train and
  corrupted EB-lambda / covariance / tau0. Stamping full identity + persisting via the same
  conflict-guarded _persist_rows path (INSERT OR IGNORE on the FULL identity) makes a 2nd run
  add ZERO rows.

REFUSAL: if a B0 row's requested coordinates/timezone (city identity) CANNOT be resolved, the
  identity cannot be reconstructed; the script RAISES BackfillIdentityError and writes NOTHING
  rather than seeding unreconstructable identity-less rows (operator requirement #2).

B0 shape: {city: {"leads": {lead: {model: {target_date: [high_c, low_c]}}}}, "_settle_*":...}
All values are degC (SPEC §7 unit antibody — residual against settlement is taken in C).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.data.bayes_precision_fusion_download import (
    BayesPrecisionFusionDownloadTarget,
    _persist_rows,
    _bayes_precision_fusion_product_identity,
)

# captured_at fixed to the seed date (deterministic, audit-stable); recorded_at is DB default.
_SEED_CAPTURED_AT = "2026-06-08T00:00:00+00:00"
_PREVIOUS_RUNS_ENDPOINT = "previous_runs"


class BackfillIdentityError(RuntimeError):
    """A B0 row's product/request identity CANNOT be constructed (e.g. the city does not resolve
    to requested coordinates/timezone in cities_by_name). Operator requirement #2: REFUSE to seed
    rather than write identity-less rows (which would reintroduce the NULL!=NULL idempotency hole
    and be unreconstructable to the exact Open-Meteo product). Raised BEFORE any write."""


def _iso_cycle(target_date: str, lead_days: int) -> str:
    """Fixed-lead previous-runs cycle = target_date - lead_days at 00:00:00Z."""
    d = datetime.strptime(target_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return (d - timedelta(days=int(lead_days))).isoformat()


def _resolve_city_target(
    *, city: str, metric: str, target_date: str, lead_days: int,
) -> BayesPrecisionFusionDownloadTarget:
    """Resolve a B0 city key to the requested-coordinate target the live writer stamps identity
    from. Reuses cities_by_name (name + aliases + slug_names) — the SAME coordinate/timezone source
    src/main.py uses to build live BayesPrecisionFusionDownloadTargets. Raises BackfillIdentityError if unresolved."""
    from src.config import cities  # noqa: PLC0415

    if not hasattr(_resolve_city_target, "_lookup"):
        lookup: dict[str, object] = {}
        for c in cities:
            lookup[c.name] = c
            for alias in getattr(c, "aliases", ()) or ():
                lookup.setdefault(alias, c)
            for slug in getattr(c, "slug_names", ()) or ():
                lookup.setdefault(slug, c)
        _resolve_city_target._lookup = lookup  # type: ignore[attr-defined]

    city_cfg = _resolve_city_target._lookup.get(city)  # type: ignore[attr-defined]
    if city_cfg is None:
        raise BackfillIdentityError(
            f"cannot construct product identity for B0 city {city!r}: it does not resolve to a "
            "configured city (no requested lat/lon/timezone). REFUSING to seed identity-less rows."
        )
    return BayesPrecisionFusionDownloadTarget(
        city=city_cfg.name,  # canonicalize to the settlement_outcomes city identifier
        metric=metric,
        target_date=target_date,
        lead_days=int(lead_days),
        latitude=float(city_cfg.lat),
        longitude=float(city_cfg.lon),
        timezone_name=str(city_cfg.timezone),
    )


def iter_identity_rows(b0: dict):
    """Yield full-identity raw_model_forecasts row dicts from the B0 nested dict.

    Each row carries the SAME product identity the live download writer stamps for the same
    (model, endpoint='previous_runs', target), so seed rows are idempotent on the new UNIQUE key
    and reconstructable to their exact Open-Meteo product. Raises BackfillIdentityError if any
    row's city identity cannot be resolved (operator requirement #2)."""
    for city, sub in b0.items():
        if city.startswith("_"):
            continue
        leads = (sub or {}).get("leads", {})
        for lead_str, models in leads.items():
            lead = int(lead_str)
            for model, by_date in (models or {}).items():
                for target_date, pair in (by_date or {}).items():
                    if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                        continue
                    high_c, low_c = pair[0], pair[1]
                    td = target_date[:10]
                    cycle = _iso_cycle(td, lead)
                    for metric, value in (("high", high_c), ("low", low_c)):
                        if value is None:
                            continue
                        target = _resolve_city_target(
                            city=city, metric=metric, target_date=td, lead_days=lead,
                        )
                        identity = _bayes_precision_fusion_product_identity(model, _PREVIOUS_RUNS_ENDPOINT, target)
                        yield {
                            "model": model,
                            "city": target.city,
                            "target_date": td,
                            "metric": metric,
                            "source_cycle_time": cycle,
                            "source_available_at": cycle,  # fixed-lead causality
                            "captured_at": _SEED_CAPTURED_AT,
                            "lead_days": lead,
                            "forecast_value_c": float(value),
                            "endpoint": _PREVIOUS_RUNS_ENDPOINT,
                            **identity,
                        }


def backfill_bayes_precision_fusion_history(*, b0: dict, db: Path, dry_run: bool = False) -> dict[str, object]:
    """Seed raw_model_forecasts (previous_runs) from the B0 dict with FULL product identity.

    Constructs all rows FIRST (so a single unresolvable city REFUSES the whole seed before any
    write — operator requirement #2: never partial identity-less rows). Then persists via the SAME
    conflict-guarded, idempotent _persist_rows the live download writer uses, so:
      * a seed row is idempotent on the new UNIQUE(model,product_id,request_url_hash,city,
        target_date,metric,source_cycle_time,endpoint) -> a 2nd run adds 0 rows;
      * a seed row matches a live-fetched row on that full identity (no duplicate, no conflict).
    Returns a provenance report. Writes nothing on dry_run."""
    rows = list(iter_identity_rows(b0))  # raises BackfillIdentityError before any write
    n_cities = len({r["city"] for r in rows})
    n_models = len({r["model"] for r in rows})

    report: dict[str, object] = {
        "status": "BAYES_PRECISION_FUSION_BACKFILL_DRY_RUN" if dry_run else "BAYES_PRECISION_FUSION_BACKFILL_SEEDED",
        "candidate_row_count": len(rows),
        "city_count": n_cities,
        "model_count": n_models,
        "endpoint": _PREVIOUS_RUNS_ENDPOINT,
    }
    if dry_run:
        report["written_row_count"] = 0
        return report

    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        before = conn.execute("SELECT COUNT(*) FROM raw_model_forecasts").fetchone()[0]
        # _persist_rows runs the BLOCKER 4 conflict pass then INSERT OR IGNORE on the FULL identity.
        # The connection is autocommit (sqlite3 default isolation_level=''), so each statement
        # self-commits — the conflict-audit durability contract in _persist_rows is satisfied.
        written = _persist_rows(conn, rows)
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM raw_model_forecasts").fetchone()[0]
    finally:
        conn.close()

    report["written_row_count"] = int(written)
    report["row_count_before"] = int(before)
    report["row_count_after"] = int(after)
    return report


def _print_history_join_self_check(db: Path) -> None:
    """SELF-CHECK: confirm the no-leak history JOIN actually yields training rows (the
    city-canonicalization / metric-match provenance trap). Counts forecast rows that have a
    matching VERIFIED settlement in the SAME db. The metric predicate
    (s.temperature_metric = r.metric) is MANDATORY: each (city, target_date) has BOTH a high AND a
    low settlement row, so omitting it joins every forecast row to 2 settlement rows -> a 2x
    over-count that would NOT match the provider-grade JOIN (bayes_precision_fusion_history_provider.py)."""
    conn = sqlite3.connect(str(db))
    try:
        joined = conn.execute(
            """
            SELECT COUNT(*) FROM raw_model_forecasts r
            JOIN settlement_outcomes s
              ON s.city = r.city
             AND s.target_date = r.target_date
             AND s.temperature_metric = r.metric
             AND s.authority = 'VERIFIED'
            WHERE r.endpoint='previous_runs'
            """
        ).fetchone()[0]
        per_city = conn.execute(
            """
            SELECT r.city, COUNT(DISTINCT r.target_date) n
            FROM raw_model_forecasts r
            JOIN settlement_outcomes s
              ON s.city = r.city AND s.target_date = r.target_date
             AND s.temperature_metric = r.metric AND s.authority='VERIFIED'
            WHERE r.endpoint='previous_runs'
            GROUP BY r.city ORDER BY n DESC
            """
        ).fetchall()
    finally:
        conn.close()
    cities_ge25 = sum(1 for _, n in per_city if n >= 25)
    print(f"history JOIN yield: {joined:,} (forecast,settlement) pairs; "
          f"{cities_ge25}/{len(per_city)} cities have >=25 settled target_dates (>= MIN_TRAIN) -> T2_BAYES")
    if joined == 0:
        print("WARNING: ZERO JOIN yield — city/metric/target_date keys do not align with "
              "VERIFIED settlement_outcomes; provider would degrade to EQUAL_WEIGHT. Investigate before flipping fusion.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed raw_model_forecasts from B0 (previous_runs).")
    ap.add_argument("--b0", required=True, help="path to B0_multilead_dataset.json (REQUIRED; no default to avoid pointing at the wrong deployment)")
    ap.add_argument("--db", required=True, help="target zeus-forecasts.db (REQUIRED; never the live path unless you intend to seed live)")
    ap.add_argument("--dry-run", action="store_true", help="count rows, write nothing")
    args = ap.parse_args()

    with open(args.b0) as f:
        b0 = json.load(f)

    report = backfill_bayes_precision_fusion_history(b0=b0, db=Path(args.db), dry_run=args.dry_run)
    print(
        f"B0 -> {report['candidate_row_count']:,} raw_model_forecasts rows "
        f"({report['city_count']} cities, {report['model_count']} models, "
        f"endpoint=previous_runs)"
    )
    if args.dry_run:
        return 0
    print(
        f"raw_model_forecasts: {report['row_count_before']:,} -> {report['row_count_after']:,} "
        f"(+{report['written_row_count']:,} new; idempotent re-runs add 0)"
    )
    _print_history_join_self_check(Path(args.db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
