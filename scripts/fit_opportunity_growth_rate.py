#!/usr/bin/env python3
# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: docs/authority/exit_portfolio_execution_authority_2026-06-13.md
#   E3 (g* = expected log-growth/day of capital released to the future opportunity
#   set; g*(T) = (1/T)·mean_b log(W_T,b^+/W_T,b^0), FITTED from replay, never
#   hand-set; LICENSE the g* adjustment only when its CI is narrow enough that the
#   sell/hold sign is invariant over the interval). Precedent pattern:
#   scripts/fit_settlement_sigma_floor.py / scripts/fit_sigma_scale.py — the fit
#   script is the artifact's ONLY writer; the exit policy READS the artifact and
#   defaults g*=0 (conservative) until the CI licenses the sign.
#   Plan: docs/evidence/plans/2026-06-13_exit_capability.md.
"""Fit the future-opportunity log-growth rate g* from settled deployment replay.

g* is the value (in log-growth per day) of one extra dollar released to the
future opportunity set. The exit policy (src/strategy/exit_policy.py) uses it via
the multiplier M(T)=e^{g*·T}: a positive, licensed g* lowers the bar to sell now
because the freed capital can compound elsewhere.

THE LAW (E3): g* is FITTED from replay, never hand-set, and the g* adjustment is
LICENSED only when its CI is narrow enough that the sell/hold sign is invariant
over the interval. This script writes state/opportunity_growth_rate.json with the
point estimate AND a bootstrap CI; the exit policy DEFAULTS g*=0 (conservative)
until an operator-reviewed CI licenses a non-zero value. There is NO artificial
cap — g*=0 is the honest conservative prior, not a throttle.

ESTIMATOR
  Read-only over state/zeus-world.db edli_live_profit_audit FILLED+SETTLED rows.
  For each settled deployment b with deployed capital K_b (kelly_size_usd) and
  realized P&L pnl_b (pnl_usd), the realized gross return is R_b = 1 + pnl_b/K_b
  and the realized log-growth is log(R_b). The holding period in days is
  T_b = max((settled_at - quote_seen_at)/1day, T_MIN_DAYS). The per-deployment
  daily log-growth is g_b = log(R_b)/T_b. g* = mean_b g_b, with a block bootstrap
  by settlement day to preserve same-day correlation.

  HARD REFUSAL below MIN_SETTLED_DEPLOYMENTS — a data-sufficiency licence (honest
  math), so the artifact is never written from a handful of fills.

READ-ONLY: opens zeus-world.db via file:...?mode=ro uri; SELECT-only; writes only
state/opportunity_growth_rate.json.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from datetime import datetime, timezone

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORLD_DEFAULT = os.path.join(REPO, "state", "zeus-world.db")
OUT_DEFAULT = os.path.join(REPO, "state", "opportunity_growth_rate.json")

# Data-sufficiency licence: do not write the artifact below this many settled,
# capital-bearing deployments (honest math, not an artificial trading cap).
MIN_SETTLED_DEPLOYMENTS = 30

# Floor on the holding period so a same-cycle settle does not divide by ~0 and
# explode the per-day rate.
T_MIN_DAYS = 0.25

AUTHORITY = "opportunity_growth_rate_v1_replay"


def _parse_iso(value: object) -> datetime | None:
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _load_settled_deployments(world_path: str) -> list[dict]:
    """Read FILLED+SETTLED capital-bearing rows read-only."""
    con = sqlite3.connect(f"file:{world_path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT pnl_usd, kelly_size_usd, filled_size, avg_fill_price,
                   quote_seen_at, created_at, settlement_outcome, condition_id
            FROM edli_live_profit_audit
            WHERE settlement_outcome IS NOT NULL
              AND pnl_usd IS NOT NULL
              AND order_lifecycle_state IN ('FILLED', 'SETTLED', 'CLOSED')
            """
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise SystemExit(f"edli_live_profit_audit read failed: {exc}")
    finally:
        con.close()

    out: list[dict] = []
    for r in rows:
        # Deployed capital: prefer kelly_size_usd; else filled_size × avg_fill_price.
        cap = r["kelly_size_usd"]
        if cap is None or float(cap) <= 0.0:
            fs = r["filled_size"]
            px = r["avg_fill_price"]
            cap = float(fs) * float(px) if fs and px else None
        if cap is None or float(cap) <= 0.0:
            continue
        pnl = float(r["pnl_usd"])
        gross_return = 1.0 + pnl / float(cap)
        if gross_return <= 0.0:
            # A total wipeout (return <= 0) has log-growth -inf; clamp the loss at
            # the worst representable single-bet ruin (-100% capped just above 0)
            # so one catastrophic row does not dominate the mean as -inf. This is
            # a numerical floor on log(R), NOT a P&L cap.
            gross_return = 1e-6
        t0 = _parse_iso(r["quote_seen_at"]) or _parse_iso(r["created_at"])
        t1 = _parse_iso(r["created_at"])
        if t0 is None or t1 is None or t1 <= t0:
            t_days = T_MIN_DAYS
        else:
            t_days = max((t1 - t0).total_seconds() / 86400.0, T_MIN_DAYS)
        day_key = (t1 or t0).date().isoformat() if (t1 or t0) else "unknown"
        out.append(
            {
                "log_growth": math.log(gross_return),
                "t_days": t_days,
                "g_daily": math.log(gross_return) / t_days,
                "day": day_key,
            }
        )
    return out


def fit_g_star(deployments: list[dict], n_boot: int = 2000, seed: int = 7) -> dict:
    """Point estimate + day-block-bootstrap CI of g* (daily log-growth)."""
    g = np.array([d["g_daily"] for d in deployments], dtype=float)
    days = np.array([d["day"] for d in deployments])
    n = g.size
    g_hat = float(np.mean(g))

    # Day-block bootstrap: resample whole settlement-days to preserve same-day
    # correlation (block bootstrap, authority Q4d σ_Δ guidance).
    rng = np.random.default_rng(seed)
    unique_days = np.unique(days)
    by_day = {d: g[days == d] for d in unique_days}
    boots = np.empty(n_boot)
    for b in range(n_boot):
        sampled = rng.choice(unique_days, size=unique_days.size, replace=True)
        vals = np.concatenate([by_day[d] for d in sampled])
        boots[b] = float(np.mean(vals))
    ci_lo = float(np.percentile(boots, 2.5))
    ci_hi = float(np.percentile(boots, 97.5))

    # Sign-invariance license (E3): the g* adjustment is licensed only when the CI
    # does not straddle zero — then the sell/hold sign is invariant over the
    # interval. Else the exit defaults g*=0 (the artifact still records the point
    # estimate for audit, but flags licensed=False).
    licensed = bool((ci_lo > 0.0) or (ci_hi < 0.0))

    return {
        "g_star_daily": g_hat,
        "g_star_ci": [ci_lo, ci_hi],
        "n_deployments": int(n),
        "n_days": int(unique_days.size),
        "licensed_sign_invariant": licensed,
        "authority": AUTHORITY,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Fit future-opportunity g* from settled replay (E3).")
    ap.add_argument("--world", default=WORLD_DEFAULT, help="zeus-world.db (edli_live_profit_audit).")
    ap.add_argument("--out", default=OUT_DEFAULT, help="output artifact path.")
    ap.add_argument("--n-boot", type=int, default=2000)
    args = ap.parse_args()

    deployments = _load_settled_deployments(args.world)
    if len(deployments) < MIN_SETTLED_DEPLOYMENTS:
        print(
            f"REFUSE: {len(deployments)} settled deployments < "
            f"MIN_SETTLED_DEPLOYMENTS={MIN_SETTLED_DEPLOYMENTS}; "
            f"g* unfit, artifact NOT written (exit defaults g*=0)."
        )
        return 2

    fit = fit_g_star(deployments, n_boot=args.n_boot)
    fit["fitted_at"] = datetime.now(timezone.utc).isoformat()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(fit, fh, indent=2)
    print(
        f"g*={fit['g_star_daily']:.6f}/day ci={fit['g_star_ci']} "
        f"n={fit['n_deployments']} days={fit['n_days']} "
        f"licensed_sign_invariant={fit['licensed_sign_invariant']} -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
