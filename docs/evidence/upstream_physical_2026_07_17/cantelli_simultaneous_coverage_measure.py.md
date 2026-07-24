"""Empirical coverage of the COMPOSED finite-evidence floor  max(CP_rho, Cantelli).

Read-only over state/zeus-forecasts.db. Extends cp_coverage.py: for every
(target, settlement-bin) cell it reconstructs BOTH serving floor terms exactly as
_current_evidence_tail_ucb_floors does --

  CP_rho   = betaincinv(k_eff+1, n_eff-k_eff, 1-alpha),  n_eff = n/(1+(n-1)rho)
  Cantelli = sigma^2 / (sigma^2 + gap^2)   for a bin wholly on one side of mu
           = 0                             for the bin straddling mu

-- and measures whether the per-cell composed floor F = max(CP_rho, Cantelli)
covers the settled outcome rate.  mu/sigma are reconstructed from the ENS members
alone (mu = member mean, sigma = within-member population std).  The SERVED
predictive sigma additionally folds provider-between + center-delta spread in
quadrature (sigma_served >= sigma_ens_within), so the served Cantelli >= this
reconstruction and the served composed floor >= the floor measured here: a
conservative LOWER proxy.  If this proxy floor covers, the served floor covers a
fortiori.

Two questions the max composition raises, and how each is measured:
  (A) Marginal per-bin coverage of the composed floor.  Since F >= CP_rho
      pointwise, the k-bucketed CP coverage (already proven in cp_coverage_report)
      dominates it; re-confirmed here as the CP panel.
  (B) The Cantelli PLUG-IN concern (estimated mu,sigma used as if known).  This
      can only break coverage in cells where Cantelli BINDS (Cantelli > CP_rho) --
      elsewhere CP takes over and masks any Cantelli error.  So we stratify the
      Cantelli-binding cells by floor value and check empirical rate <= floor.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
from collections import Counter, defaultdict

import numpy as np
from scipy.special import betaincinv

DB = "state/zeus-forecasts.db"
ALPHA = 0.05
MIN_MEMBERS = 20
PAD_C = 3.0
MIN_CELLS_PER_BUCKET = 30
N_BOOT = 1000
SEED = 42

# Serving fitted rho (state/ens_member_dependence/ens_member_dependence_20260717.json).
RHO = {"high": 0.004639, "low": 0.053955}
RHO_MAX = max(RHO.values())


def label(value, policy):
    if policy == "wmo_half_up":
        return int(math.floor(value + 0.5))
    if policy in ("oracle_truncate", "floor"):
        return int(math.floor(value))
    if policy == "ceil":
        return int(math.ceil(value))
    return None


def preimage_offsets(policy, half_step=0.5):
    if policy == "wmo_half_up":
        return (-half_step, +half_step)
    if policy in ("oracle_truncate", "floor"):
        return (0.0, +2.0 * half_step)
    if policy == "ceil":
        return (-2.0 * half_step, 0.0)
    return (-half_step, +half_step)


def to_unit(v, src, dst):
    src = "F" if src in ("degf", "f") else ("C" if src in ("degc", "c") else None)
    if src is None or dst not in ("C", "F"):
        return None
    if src == dst:
        return v
    return v * 9.0 / 5.0 + 32.0 if src == "C" else (v - 32.0) * 5.0 / 9.0


def parse_ts(s):
    return dt.datetime.fromisoformat(s)


def load_targets():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        """
        SELECT es.city, es.target_date, es.temperature_metric,
               es.members_json, es.members_unit, es.settlement_unit,
               es.settlement_rounding_policy, es.source_cycle_time, es.issue_time,
               es.local_day_start_utc, es.source_available_at, es.available_at,
               es.snapshot_id, so.settlement_value, so.settlement_unit
        FROM ensemble_snapshots es
        JOIN settlement_outcomes so
          ON lower(so.city)=lower(es.city) AND so.target_date=es.target_date
         AND so.temperature_metric=es.temperature_metric
         AND so.authority='VERIFIED' AND so.settlement_value IS NOT NULL
        WHERE es.source_id='ecmwf_open_data' AND es.model_version='ecmwf_ens'
          AND es.authority='VERIFIED' AND es.causality_status='OK'
          AND es.boundary_ambiguous=0
          AND es.forecast_window_attribution_status='FULLY_INSIDE_TARGET_LOCAL_DAY'
          AND es.contributes_to_target_extrema=1
        """
    ).fetchall()
    con.close()
    best = {}
    for (city, td, metric, mjson, munit, sunit, policy, cyc, iss, lds,
         savail, avail, sid, sval, so_unit) in rows:
        cyc = cyc or iss
        end_of_day = parse_ts(lds) + dt.timedelta(days=1)
        if parse_ts(cyc) > end_of_day:
            continue
        key = (city.lower(), td, metric)
        rank = (cyc, savail or avail or "", sid)
        if key not in best or rank > best[key][0]:
            best[key] = (rank, dict(
                city=city, td=td, metric=metric.strip().lower(), mjson=mjson,
                munit=(munit or "").strip().lower(), sunit=(sunit or "C").strip().upper(),
                policy=(policy or "wmo_half_up").strip().lower(),
                sval=float(sval), so_unit=(so_unit or "C").strip().upper()))
    return [v[1] for v in best.values()]


def cantelli(mu, sigma, low, high):
    """Serving one-sided Cantelli moment mass for a bin preimage [low, high)."""
    var = sigma * sigma
    if low > mu:
        gap = low - mu
        return var / (var + gap * gap)
    if high < mu:
        gap = mu - high
        return var / (var + gap * gap)
    return 0.0


def cp(k, n, rho, a=ALPHA):
    if k >= n:
        return 1.0
    if rho <= 0.0:
        return float(betaincinv(k + 1, n - k, 1.0 - a))
    n_eff = n / (1.0 + (n - 1) * rho)
    k_eff = k * n_eff / n
    return float(betaincinv(k_eff + 1.0, n_eff - k_eff, 1.0 - a))


def build_cells(targets, pad_c=PAD_C):
    """One row per (target,bin): metric, n, k, outcome, cp, cant, floor, target_key."""
    cells = []
    n_counter = Counter()
    dropped = 0
    for t in targets:
        try:
            raw = [float(x) for x in json.loads(t["mjson"]) if x is not None]
        except (TypeError, ValueError):
            dropped += 1
            continue
        conv = [to_unit(v, t["munit"], t["sunit"]) for v in raw]
        if any(c is None for c in conv):
            dropped += 1
            continue
        labels = [label(v, t["policy"]) for v in conv]
        if any(l is None for l in labels):
            dropped += 1
            continue
        n = len(labels)
        if n < MIN_MEMBERS:
            dropped += 1
            continue
        n_counter[n] += 1
        # ENS-only predictive shape (conservative LOWER proxy for served sigma).
        mu = sum(conv) / len(conv)
        sigma = math.sqrt(sum((v - mu) ** 2 for v in conv) / len(conv))
        if not (math.isfinite(sigma) and sigma > 0.0):
            dropped += 1
            continue
        rho = RHO.get(t["metric"], RHO_MAX)
        out_v = to_unit(t["sval"], t["so_unit"].lower(), t["sunit"])
        out_lbl = label(out_v, t["policy"])
        counts = Counter(labels)
        low_off, high_off = preimage_offsets(t["policy"])
        pad = int(round(pad_c * (9.0 / 5.0))) if t["sunit"] == "F" else int(round(pad_c))
        lo, hi = min(labels) - pad, max(labels) + pad
        grid = set(range(lo, hi + 1))
        grid.add(out_lbl)
        tkey = (t["city"].lower(), t["td"], t["metric"])
        for b in grid:
            k = counts.get(b, 0)
            cp_v = cp(k, n, rho)
            low = b + low_off
            high = b + high_off
            cant_v = cantelli(mu, sigma, low, high)
            floor = max(cp_v, cant_v)
            out = 1 if b == out_lbl else 0
            cells.append((t["metric"], n, k, out, cp_v, cant_v, floor, tkey))
    return cells, n_counter, dropped


def boot_upper_rate(sub, reps=N_BOOT, seed=SEED):
    """Target-clustered block-bootstrap 97.5-pct upper of the outcome rate over a cell subset."""
    by_t = defaultdict(list)
    for c in sub:
        by_t[c[7]].append(c[3])
    tkeys = list(by_t.keys())
    if not tkeys:
        return float("nan"), 0.0
    rng = np.random.default_rng(seed)
    rates = []
    for _ in range(reps):
        pick = rng.choice(len(tkeys), size=len(tkeys), replace=True)
        num = 0
        den = 0
        for i in pick:
            outs = by_t[tkeys[i]]
            num += sum(outs)
            den += len(outs)
        if den:
            rates.append(num / den)
    point = sum(c[3] for c in sub) / len(sub)
    return (float(np.percentile(rates, 97.5)) if rates else float("nan")), point


def panel_cp_by_k(cells, n_modal, k_max=13):
    """Re-confirm CP(k,rho) coverage marginally (dominance floor for the composed max)."""
    by_k = defaultdict(list)
    for c in cells:
        by_k[c[2]].append(c)
    out = []
    for k in sorted(by_k):
        if k > k_max:
            break
        sub = by_k[k]
        if len(sub) < MIN_CELLS_PER_BUCKET:
            continue
        up, pt = boot_upper_rate(sub)
        cp_v = sub[0][4]  # cp depends only on (k,n,rho); n_modal-consistent
        out.append((k, len(sub), pt, up, cp_v, "VIOL" if up > cp_v else ""))
    return out


def panel_cantelli_binding(cells, n_buckets=10):
    """Coverage in cells where Cantelli BINDS (cant > cp) -- the plug-in test.

    Stratify by composed-floor value into equal-count buckets; in each, boot-upper
    of the outcome rate vs the min floor in the bucket (the weakest UCB claim).
    """
    binding = [c for c in cells if c[5] > c[4] + 1e-12]
    total = len(cells)
    if not binding:
        return [], 0, total
    binding.sort(key=lambda c: c[6])
    per = max(MIN_CELLS_PER_BUCKET, len(binding) // n_buckets)
    out = []
    i = 0
    while i < len(binding):
        sub = binding[i:i + per]
        i += per
        if len(sub) < MIN_CELLS_PER_BUCKET:
            if out:  # merge tail into last bucket
                prev = out.pop()
                sub = prev[6] + sub
            else:
                break
        floors = [c[6] for c in sub]
        fmin = min(floors)
        fmean = sum(floors) / len(floors)
        up, pt = boot_upper_rate(sub)
        out.append((len(sub), fmin, fmean, pt, up, "VIOL" if up > fmin else "", sub))
    return [(a, b, c, d, e, f) for (a, b, c, d, e, f, _s) in out], len(binding), total


def walk_forward(cells, n_modal):
    dates = sorted({c[7][1] for c in cells})
    mid = dates[len(dates) // 2]
    train = [c for c in cells if c[7][1] < mid]
    test = [c for c in cells if c[7][1] >= mid]
    # Cantelli-binding coverage on TEST using floors reconstructed identically (walk-forward
    # by date; the rho is a fixed serving constant so the only split concern is the empirical
    # rate estimate). Report test failures.
    _, _, _ = train, None, None
    rows, nbind, ntot = panel_cantelli_binding(test)
    fails = [r for r in rows if r[4] > r[1]]  # boot_upper > fmin
    return mid, len(test), nbind, ntot, rows, fails


def main():
    print("loading targets ...", flush=True)
    targets = load_targets()
    print(f"settled targets: {len(targets)}")
    cells, n_counter, dropped = build_cells(targets)
    n_modal = n_counter.most_common(1)[0][0]
    print(f"cells: {len(cells)}  dropped: {dropped}  modal n: {n_modal}")

    for metric in ("high", "low"):
        mc = [c for c in cells if c[0] == metric]
        print(f"\n=== metric={metric}  cells={len(mc)}  rho={RHO[metric]} ===")
        print("CP marginal panel (k, cells, rate, boot_up, CP_rho, flag):")
        for row in panel_cp_by_k(mc, n_modal):
            print(f"  k={row[0]:2d} n={row[1]:6d} r={row[2]:.4f} up={row[3]:.4f} CP={row[4]:.4f} {row[5]}")
        rows, nbind, ntot = panel_cantelli_binding(mc)
        print(f"Cantelli-binding cells: {nbind}/{ntot} ({100*nbind/max(ntot,1):.1f}%)")
        print("  Cantelli-binding coverage (cells, floor_min, floor_mean, rate, boot_up, flag):")
        for r in rows:
            print(f"    n={r[0]:6d} fmin={r[1]:.4f} fmean={r[2]:.4f} r={r[3]:.4f} up={r[4]:.4f} {r[5]}")
        mid, ntest, nb, nt, wrows, fails = walk_forward(mc, n_modal)
        print(f"  walk-forward test>= {mid}: cells={ntest} cantelli-binding={nb} "
              f"failures={'NONE' if not fails else fails}")

    # Pooled composed-floor headline: fraction of cells the floor covers vs mean rate.
    fl = np.array([c[6] for c in cells])
    ou = np.array([c[3] for c in cells])
    print(f"\nPOOLED composed floor: mean_floor={fl.mean():.4f} mean_outcome={ou.mean():.4f} "
          f"(floor is a UCB; mean_floor >> mean_outcome = conservative)")
    # Direct exceedance: bucket ALL cells by floor decile, boot-upper rate vs floor_min.
    order = np.argsort(fl)
    print("Composed floor reliability (all cells, floor deciles):")
    idx = order.tolist()
    per = max(MIN_CELLS_PER_BUCKET, len(idx) // 10)
    j = 0
    while j < len(idx):
        seg = idx[j:j + per]
        j += per
        if len(seg) < MIN_CELLS_PER_BUCKET:
            break
        sub = [cells[i] for i in seg]
        fmin = min(c[6] for c in sub)
        up, pt = boot_upper_rate(sub)
        flag = "VIOL" if up > fmin else ""
        print(f"  n={len(sub):6d} floor_min={fmin:.4f} rate={pt:.4f} boot_up={up:.4f} {flag}")


if __name__ == "__main__":
    main()
