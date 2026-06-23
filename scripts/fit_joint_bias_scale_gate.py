#!/usr/bin/env python3
# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: docs/authority/statistical_calibration_authority_2026-06-12.txt Step 1 +
#   migration gate ("prequential paired log-loss lower-bound positive + modal-class reliability
#   shrinks"; cross-val adopted k_new < 1.34, modal-q 0.22 -> 0.30-0.34). Real settled chain only
#   (forecast_posteriors VERIFIED settlement_outcomes) — the authority's prequential method, not a
#   strategy replay. Decides whether the documented Step-1 joint (b,k) fix is deployable.
import math
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.fit_sigma_scale import (  # noqa: E402
    _FIT_QUERY, _build_cells, _NEG_INF, _POS_INF, FCST_DEFAULT,
)
from src.probability.joint_bias_scale import (  # noqa: E402
    CalibrationCell, fit_joint_bias_scale, fit_scale_only,
    neg_loglik_joint, neg_loglik_scale_only, _winning_bounds,
)


def _to_cells(cell_dicts):
    out = []
    for c in cell_dicts:
        edges = []
        for lo, hi in zip(c["edges_lo"], c["edges_hi"]):
            edges.append((None if lo <= _NEG_INF / 2 else float(lo),
                          None if hi >= _POS_INF / 2 else float(hi)))
        out.append(CalibrationCell(mu=0.0, sigma=float(c["sigma_impl"]),
                                   edges=tuple(edges), winning_index=int(c["won_index"]),
                                   ))
        out[-1] = CalibrationCell(mu=0.0, sigma=float(c["sigma_impl"]), edges=tuple(edges),
                                  winning_index=int(c["won_index"]))
    # attach target_date for prequential split
    return [(cd["target_date"], cell) for cd, cell in zip(cell_dicts, out)]


def _categorical_ll(cells, b, k):
    """Mean categorical log-loss (lower=better) of the winning bin under (b,k)."""
    mu, sigma, lo, hi = _winning_bounds(cells)
    from src.probability.joint_bias_scale import _winning_mass
    import numpy as np
    return float(-np.mean(np.log(_winning_mass(mu, sigma, lo, hi, b, k))))


def main():
    fcst = os.environ.get("ZEUS_FORECASTS_DB", FCST_DEFAULT)
    conn = sqlite3.connect(f"file:{fcst}?mode=ro", uri=True)
    rows = conn.execute(_FIT_QUERY).fetchall()
    conn.close()
    cells_by_unit, window = _build_cells(rows)
    # Pool C+F cells (sigma/edges are per-cell native unit; the (b,k) are unit-agnostic scalars
    # because edges and sigma share each cell's unit).
    all_dicts = [c for unit in cells_by_unit for c in cells_by_unit[unit]]
    dated = _to_cells(all_dicts)
    cells = [c for _, c in dated]
    n = len(cells)
    print(f"=== Step-1 joint (b,k) gate on REAL settled cells (n={n}, {window}) ===")
    if n < 30:
        print(f"thin: only {n} settled cells — report only, do not deploy.")
    # In-sample fit: does joint reduce k (bias was being absorbed) + recover bias b?
    k_scale, _ = fit_scale_only(cells)
    b_joint, k_joint, _ = fit_joint_bias_scale(cells)
    print(f"  scale-only (contaminated) k = {k_scale:.4f}")
    print(f"  joint (b,k):  b = {b_joint:+.4f}  k = {k_joint:.4f}")
    print(f"  GATE k_new<1.34: {'PASS' if k_joint < 1.34 else 'FAIL'} (k_joint={k_joint:.3f})")
    print(f"  bias absorbed by scale-only: {'YES' if k_scale > k_joint + 0.05 else 'no'} "
          f"(Δk={k_scale-k_joint:+.3f}); |b|={abs(b_joint):.3f}")

    # Prequential: fit on earlier target_dates, score categorical log-loss on the latest third.
    dated_sorted = sorted(dated, key=lambda x: x[0])
    cut = int(n * 0.67)
    train = [c for _, c in dated_sorted[:cut]]
    test = [c for _, c in dated_sorted[cut:]]
    if len(train) >= 20 and len(test) >= 10:
        ks, _ = fit_scale_only(train)
        bj, kj, _ = fit_joint_bias_scale(train)
        ll_scale = _categorical_ll(test, 0.0, ks)
        ll_joint = _categorical_ll(test, bj, kj)
        print(f"\n  PREQUENTIAL (train={len(train)} -> test={len(test)}):")
        print(f"    scale-only out-of-sample categorical log-loss = {ll_scale:.4f}")
        print(f"    joint (b,k) out-of-sample categorical log-loss = {ll_joint:.4f}")
        improved = ll_joint < ll_scale
        print(f"    GATE prequential paired log-loss improves: {'PASS' if improved else 'FAIL'} "
              f"(Δ={ll_scale-ll_joint:+.4f}; train b={bj:+.3f} k={kj:.3f})")
        print(f"\n  VERDICT: {'DEPLOYABLE (gate passes)' if (kj<1.34 and improved) else 'NOT yet — gate fails, stays advisory per authority'}")
    else:
        print(f"\n  prequential skipped: train={len(train)} test={len(test)} too thin.")


if __name__ == "__main__":
    raise SystemExit(main())
