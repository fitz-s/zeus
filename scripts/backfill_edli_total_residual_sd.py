# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=never
# Purpose: Backfill total_residual_sd_c and heterogeneity_var_c2 on existing
#   edli_per_city_v1 rows in model_bias_ens (zeus-world.db) to the honest full
#   predictive sigma (sigma_resid * sqrt(1 + 1/n)), so pre-existing rows widen
#   q_lcb correctly without waiting for a fresh producer run.
# Reuse: verify zeus-world.db model_bias_ens has total_residual_sd_c column before
#   re-running (SCHEMA_WORLD_VERSION≥8); confirm producers (write_promoted_edli_bias.py
#   or write_d7_rolling_edli_bias.py) have been updated — backfill is idempotent but
#   a fresh producer run supersedes it for new rows. DRY-RUN by default (--commit to apply).
# Authority basis: #89 honest q_lcb pre-arm blocker. The live q_lcb inflater now reads
#   model_bias_ens.total_residual_sd_c (full forward predictive σ). Existing edli_per_city_v1
#   rows were written with total_residual_sd_c == residual_sd_c (in-sample-only) and
#   heterogeneity_var_c2 NULL. This backfill stamps the honest predictive inflation IN PLACE so
#   already-stored rows widen q_lcb correctly without waiting for a producer re-run. It does NOT
#   change effective_bias_c, residual_sd_c, or the point q — only the lower-bound σ.
"""Backfill total_residual_sd_c / heterogeneity_var_c2 on existing edli_per_city_v1 rows.

For each VERIFIED edli_per_city_v1 row with residual_sd_c > 0 and total_residual_sd_c missing
or still equal to residual_sd_c (legacy), set:

    heterogeneity_var_c2 = residual_sd_c^2 / n_live      (mean-estimation drift variance)
    total_residual_sd_c  = residual_sd_c * sqrt(1 + 1/n_live)   (full predictive σ)

n_live is the number of per-day residuals the bias mean was fit from (stored on the row). This
is the SAME estimator the producers now use (full_predictive_residual_sd); the backfill simply
applies it to rows fit before the producers were updated. Rows with n_live < 2 are left at
total == residual (no estimable drift). DRY-RUN by default; --commit writes.
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAMILY = "edli_per_city_v1"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    from src.state.db import get_world_connection

    conn = get_world_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT rowid, city, season, month, metric, n_live, residual_sd_c,
                  total_residual_sd_c, heterogeneity_var_c2
           FROM model_bias_ens
           WHERE error_model_family=? AND authority='VERIFIED'
             AND residual_sd_c IS NOT NULL AND residual_sd_c > 0""",
        (FAMILY,),
    ).fetchall()

    updates = []
    for r in rows:
        resid = float(r["residual_sd_c"])
        n = int(r["n_live"]) if r["n_live"] else 0
        cur_total = r["total_residual_sd_c"]
        # Only backfill rows still at the in-sample-only value (legacy) or NULL.
        is_legacy = cur_total is None or abs(float(cur_total) - resid) < 1e-9
        if not is_legacy or n < 2:
            continue
        het = (resid * resid) / n
        total = resid * math.sqrt(1.0 + 1.0 / n)
        updates.append((r["rowid"], r["city"], r["metric"], n, resid, total, het))

    print(f"edli VERIFIED rows: {len(rows)}; eligible for backfill: {len(updates)}\n")
    for rowid, city, metric, n, resid, total, het in updates:
        print(f"  {city:16s} {metric:4s} n={n:>2d} resid_sd={resid:.3f} -> total_sd={total:.3f} "
              f"(+{(total - resid):.3f}) het={het:.4f}")

    if not args.commit:
        print(f"\nDRY-RUN: {len(updates)} rows would be updated. Re-run with --commit.")
        return 0

    for rowid, city, metric, n, resid, total, het in updates:
        conn.execute(
            "UPDATE model_bias_ens SET total_residual_sd_c=?, heterogeneity_var_c2=? WHERE rowid=?",
            (total, het, rowid),
        )
    conn.commit()
    print(f"\nWROTE {len(updates)} backfilled rows (total_residual_sd_c + heterogeneity_var_c2).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
