#!/usr/bin/env python3
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: docs/the_path/BACKFILL_NOW.md + U0R_BAYES_SPEC.md §3/§5/§6 F1.
#   Retrospective U0R activation (operator directive 2026-06-08: no forward accrual —
#   seed the walk-forward history store from the already-downloaded fixed-lead dataset NOW).
"""
Seed raw_model_forecasts (endpoint='previous_runs') from the proven fixed-lead
multi-model dataset B0_multilead_dataset.json so the live U0RHistoryProvider has
its walk-forward training history IMMEDIATELY (no 25-day forward wait). Fusion then
reaches T2_BAYES instead of EQUAL_WEIGHT on the very next materialize cycle.

PROVENANCE / SAFETY:
  * Writes ONLY the SHADOW-ONLY research-accrual table raw_model_forecasts
    (trade_authority_status='SHADOW_ONLY', training_allowed=0) — NOT a money-path
    or order/training-truth table. It changes NO posterior until
    replacement_0_1_u0r_fusion_enabled is flipped AND the U0RHistoryProvider reads it.
  * --db is REQUIRED and never defaults to the live path: the operator points it at
    the target zeus-forecasts.db explicitly. NEVER run against a DB you must not write.
  * No-leak is enforced at SERVE time by U0RHistoryProvider (target_date < decision_date);
    this seed is genuine historical fixed-lead previous-runs data, correctly tagged.
  * Idempotent: INSERT OR IGNORE on UNIQUE(model,city,target_date,metric,source_cycle_time,endpoint).

B0 shape: {city: {"leads": {lead: {model: {target_date: [high_c, low_c]}}}}, "_settle_*":...}
All values are degC (SPEC §7 unit antibody — residual against settlement is taken in C).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone


def _iso_cycle(target_date: str, lead_days: int) -> str:
    """Fixed-lead previous-runs cycle = target_date - lead_days at 00:00:00Z."""
    d = datetime.strptime(target_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return (d - timedelta(days=int(lead_days))).isoformat()


def iter_rows(b0: dict):
    """Yield raw_model_forecasts tuples from the B0 nested dict."""
    captured = datetime.now(timezone.utc).isoformat() if False else "2026-06-08T00:00:00+00:00"
    # captured_at fixed to the seed date (deterministic, audit-stable); recorded_at is DB default.
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
                    cycle = _iso_cycle(target_date, lead)
                    for metric, value in (("high", high_c), ("low", low_c)):
                        if value is None:
                            continue
                        yield (
                            model, city, target_date[:10], metric,
                            cycle, cycle, captured, lead, float(value),
                            "previous_runs", "SHADOW_ONLY", 0,
                        )


INSERT_SQL = """
INSERT OR IGNORE INTO raw_model_forecasts
  (model, city, target_date, metric, source_cycle_time, source_available_at,
   captured_at, lead_days, forecast_value_c, endpoint, trade_authority_status, training_allowed)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed raw_model_forecasts from B0 (previous_runs).")
    ap.add_argument("--b0", default="/Users/leofitz/zeus/.omc/research/polyweather_eval/B0_multilead_dataset.json")
    ap.add_argument("--db", required=True, help="target zeus-forecasts.db (REQUIRED; never the live path unless you intend to seed live)")
    ap.add_argument("--dry-run", action="store_true", help="count rows, write nothing")
    args = ap.parse_args()

    with open(args.b0) as f:
        b0 = json.load(f)

    rows = list(iter_rows(b0))
    print(f"B0 -> {len(rows):,} raw_model_forecasts rows "
          f"({len({(r[1]) for r in rows})} cities, "
          f"{len({r[0] for r in rows})} models, "
          f"endpoint=previous_runs)")
    if args.dry_run:
        return 0

    con = sqlite3.connect(args.db)
    try:
        con.execute("PRAGMA busy_timeout = 30000")
        before = con.execute("SELECT COUNT(*) FROM raw_model_forecasts").fetchone()[0]
        con.executemany(INSERT_SQL, rows)
        con.commit()
        after = con.execute("SELECT COUNT(*) FROM raw_model_forecasts").fetchone()[0]
        print(f"raw_model_forecasts: {before:,} -> {after:,} (+{after-before:,} new; idempotent re-runs add 0)")

        # SELF-CHECK: confirm the no-leak history JOIN actually yields training rows
        # (the city-canonicalization / metric-match provenance trap). Counts forecast
        # rows that have a matching VERIFIED settlement in the SAME db.
        # The metric predicate (s.temperature_metric = r.metric) is MANDATORY: each
        # (city, target_date) has BOTH a high AND a low settlement row, so omitting it
        # joins every forecast row to 2 settlement rows -> a 2x over-count that would
        # NOT match the provider-grade JOIN (u0r_history_provider.py:93-95). The self-
        # check must report the SAME yield the live U0RHistoryProvider will actually see.
        joined = con.execute(
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
        per_city = con.execute(
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
        cities_ge25 = sum(1 for _, n in per_city if n >= 25)
        print(f"history JOIN yield: {joined:,} (forecast,settlement) pairs; "
              f"{cities_ge25}/{len(per_city)} cities have >=25 settled target_dates (>= MIN_TRAIN) -> T2_BAYES")
        if joined == 0:
            print("WARNING: ZERO JOIN yield — city/metric/target_date keys do not align with "
                  "VERIFIED settlement_outcomes; provider would degrade to EQUAL_WEIGHT. Investigate before flipping fusion.")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
