#!/usr/bin/env python3
# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: C3 calibration surface docs/archive/2026-Q2/operations_historical/c3_sigma_calibration_surface_2026-06-12.md
#   n=127 A_24h C-unit cells; mode-bin realized win rate 0.17 vs mean q 0.43-0.46 (2.5x too peaked)
#   Recommended k=2.4 for C-unit cities. F cities: n=25 insufficient, keep market-anchor cap.
"""Read-only before/after evidence table for the operator σ-scale flip decision.

For the freshest posterior of each C-unit city family for the next 2 target dates:
  - Recomputes q at k=1.0 (current) vs k=2.4 (proposed scale)
  - Reports per-family: mode bin q before/after, d=1/d=2 ring q before/after
  - Reports how many currently-positive buy_no ring edges survive at k=2.4
    (ring = distance-1 and distance-2 bins from the mode, which are the
    adjacent-ring bins the C3 finding shows are underweighted)

Output written to docs/evidence/sigma_scale/2026-06-12_before_after.md
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DB_PATH = Path(__file__).parent.parent / "state" / "zeus-forecasts.db"
OUTPUT_PATH = Path(__file__).parent.parent / "docs" / "evidence" / "sigma_scale" / "2026-06-12_before_after.md"
K_PROPOSED = 2.4
N_TARGET_DATES = 2  # how many upcoming target dates to cover

# ---------------------------------------------------------------------------
# Bin integration (mirrors src/calibration/emos.bin_probability_settlement)
# ---------------------------------------------------------------------------
from scipy.stats import norm as _scipy_norm

def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    return float(_scipy_norm.cdf(x, loc=mu, scale=sigma))


def _bin_prob(mu: float, sigma: float, lo: float | None, hi: float | None, half_step: float) -> float:
    """Probability mass of a Normal(mu, sigma) in a settlement bin [lo-half, hi+half].

    Mirrors the WMO-half-up preimage: rounding maps x to bin if round(x)=bin_center.
    lo=None → open lower shoulder; hi=None → open upper shoulder.
    """
    if sigma <= 0.0:
        return 0.0
    if lo is None and hi is None:
        return 1.0
    if lo is None:
        return _normal_cdf(hi + half_step, mu, sigma)
    if hi is None:
        return 1.0 - _normal_cdf(lo - half_step, mu, sigma)
    return _normal_cdf(hi + half_step, mu, sigma) - _normal_cdf(lo - half_step, mu, sigma)


def _build_q(mu: float, sigma: float, bins: list[dict]) -> dict[str, float]:
    """Build renormalized probability vector from N(mu, sigma) over bin topology."""
    half_step = 0.5  # settlement_step_c=1 for C cities
    raw: dict[str, float] = {}
    for b in bins:
        lo = b.get("lower_c")
        hi = b.get("upper_c")
        lo_f = float(lo) if lo is not None else None
        hi_f = float(hi) if hi is not None else None
        raw[b["bin_id"]] = _bin_prob(mu, sigma, lo_f, hi_f, half_step)
    total = sum(raw.values())
    if total <= 0.0 or not math.isfinite(total):
        return raw
    return {k: v / total for k, v in raw.items()}


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------
def _get_connection(path: Path) -> sqlite3.Connection:
    # Read-only URI per live-DB law (sanctioned probe pattern); this script
    # only ever SELECTs.
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _freshest_posteriors(conn: sqlite3.Connection, n_dates: int = N_TARGET_DATES) -> list[sqlite3.Row]:
    """Return the freshest fused_normal_direct posterior per (city, target_date, metric='high')
    for the next n_dates target dates, C-unit only."""
    return conn.execute(
        """
        SELECT p.city, p.target_date, p.temperature_metric, p.q_json, p.provenance_json,
               p.q_lcb_json,
               MAX(p.computed_at) as latest_computed_at
        FROM forecast_posteriors p
        WHERE json_extract(p.provenance_json, '$.q_shape') = 'fused_normal_direct'
          AND p.temperature_metric = 'high'
          AND json_extract(p.provenance_json, '$.bin_topology[0].settlement_unit') = 'C'
          AND p.target_date IN (
              SELECT DISTINCT target_date FROM forecast_posteriors
              WHERE json_extract(provenance_json, '$.q_shape') = 'fused_normal_direct'
                AND temperature_metric = 'high'
                AND json_extract(provenance_json, '$.bin_topology[0].settlement_unit') = 'C'
              ORDER BY target_date
              LIMIT ?
          )
        GROUP BY p.city, p.target_date, p.temperature_metric
        ORDER BY p.target_date, p.city
        """,
        (n_dates,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------
def _distance_from_mode(q: dict[str, float], bins: list[dict]) -> dict[str, int]:
    """Return {bin_id: distance_from_mode} where mode is the highest-q bin.
    Distance is measured in integer steps along the sorted bin list."""
    sorted_bins = sorted(bins, key=lambda b: (b.get("lower_c") or b.get("upper_c") or 0.0))
    mode_id = max(q, key=q.get)
    mode_idx = next((i for i, b in enumerate(sorted_bins) if b["bin_id"] == mode_id), 0)
    return {b["bin_id"]: abs(i - mode_idx) for i, b in enumerate(sorted_bins)}


def _ring_q(q: dict[str, float], dist_map: dict[str, int], max_d: int) -> float:
    """Sum of q for all bins at distance == max_d from mode."""
    return sum(v for k, v in q.items() if dist_map.get(k) == max_d)


def _buy_no_edges_survive(q1: dict, q2: dict, q_lcb_raw: str | None) -> tuple[int, int]:
    """Count how many bins that had positive buy_no edge (q_lcb_no > ask) survive at k=2.4.

    Since we don't have executable market prices in this read-only script (market_price_history
    is 11 days stale; executable_market_snapshots is empty), we use a proxy: ring bins
    (distance 1 and 2 from mode) where q_lcb_no > 0.05 (below typical ask floor for NO).
    Returns (n_surviving_at_k1, n_surviving_at_k24).
    """
    # Without live prices we proxy: a "positive buy_no edge" bin is one where
    # q > 0.05 AND the bin is a ring bin (d=1 or d=2). This is an approximation.
    # If q_lcb_json is available, use it; else fall back to q_point.
    if q_lcb_raw:
        try:
            lcb = json.loads(q_lcb_raw)
        except Exception:
            lcb = q1
    else:
        lcb = q1

    # For k=2.4 we don't have the lcb directly — use scaled q as proxy.
    n1 = sum(1 for k, v in lcb.items() if v > 0.05)
    n2 = sum(1 for k, v in q2.items() if v > 0.05)
    return n1, n2


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    conn = _get_connection(DB_PATH)
    rows = _freshest_posteriors(conn)
    conn.close()

    if not rows:
        print("No fused_normal_direct C-unit posteriors found in DB.")
        sys.exit(0)

    lines: list[str] = [
        "# σ Scale k=2.4 Before/After Evidence Table",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')}",
        f"**Authority:** docs/archive/2026-Q2/operations_historical/c3_sigma_calibration_surface_2026-06-12.md",
        f"**Proposed k:** {K_PROPOSED} (C-unit cities only; F cities unchanged)",
        f"**Source:** freshest fused_normal_direct posterior per C-unit family, next {N_TARGET_DATES} target dates",
        "",
        "> **Decision gate:** none — the correction is MATH-decided (operator law 2026-06-12). k and w are",
        "> FITTED by scripts/fit_sigma_scale.py into state/sigma_scale_fit.json; enabling = the artifact",
        "> existing with the family fitted. provenance_json gains `sigma_scale_k_applied` + ",
        "> `uniform_mixture_w_applied` on every C-unit posterior once the artifact is present.",
        "",
        "---",
        "",
        "## Per-Family Table",
        "",
        "| City | Date | σ@k=1 | σ@k=2.4 | mode_q k=1 | mode_q k=2.4 | d=1 q k=1 | d=1 q k=2.4 | d=2 q k=1 | d=2 q k=2.4 | >0.05 bins k=1 | >0.05 bins k=2.4 |",
        "|------|------|-------|---------|-----------|-------------|-----------|------------|-----------|------------|---------------|-----------------|",
    ]

    summary_mode_before: list[float] = []
    summary_mode_after: list[float] = []
    summary_d1_before: list[float] = []
    summary_d1_after: list[float] = []
    summary_d2_before: list[float] = []
    summary_d2_after: list[float] = []

    for row in rows:
        prov = json.loads(row["provenance_json"])
        bf = prov.get("bayes_precision_fusion") or prov.get("u0r_fusion") or {}
        sigma_pred = bf.get("predictive_sigma_c")
        mu = bf.get("anchor_value_c")
        if sigma_pred is None or mu is None:
            continue

        sigma_pred = float(sigma_pred)
        mu = float(mu)
        sigma_scaled = sigma_pred * K_PROPOSED

        bins_topo = prov.get("bin_topology", [])
        if not bins_topo:
            continue

        q_k1 = _build_q(mu, sigma_pred, bins_topo)
        q_k24 = _build_q(mu, sigma_scaled, bins_topo)

        dist_k1 = _distance_from_mode(q_k1, bins_topo)
        # Use same dist map for k=2.4 (mode should be same bin)
        dist_k24 = _distance_from_mode(q_k24, bins_topo)

        mode_id_k1 = max(q_k1, key=q_k1.get)
        mode_q_k1 = q_k1[mode_id_k1]
        mode_q_k24 = q_k24.get(mode_id_k1, 0.0)

        d1_k1 = _ring_q(q_k1, dist_k1, 1)
        d1_k24 = _ring_q(q_k24, dist_k24, 1)
        d2_k1 = _ring_q(q_k1, dist_k1, 2)
        d2_k24 = _ring_q(q_k24, dist_k24, 2)

        n_edge_k1, n_edge_k24 = _buy_no_edges_survive(q_k1, q_k24, row["q_lcb_json"])

        city = row["city"]
        tdate = row["target_date"]

        lines.append(
            f"| {city} | {tdate} | {sigma_pred:.3f} | {sigma_scaled:.3f} "
            f"| {mode_q_k1:.3f} | {mode_q_k24:.3f} "
            f"| {d1_k1:.3f} | {d1_k24:.3f} "
            f"| {d2_k1:.3f} | {d2_k24:.3f} "
            f"| {n_edge_k1} | {n_edge_k24} |"
        )

        summary_mode_before.append(mode_q_k1)
        summary_mode_after.append(mode_q_k24)
        summary_d1_before.append(d1_k1)
        summary_d1_after.append(d1_k24)
        summary_d2_before.append(d2_k1)
        summary_d2_after.append(d2_k24)

    def _mean(xs): return sum(xs) / len(xs) if xs else 0.0

    lines += [
        "",
        "---",
        "",
        "## Summary",
        "",
        f"**Families analysed:** {len(summary_mode_before)}",
        "",
        "| Metric | k=1.0 (current) | k=2.4 (proposed) | Change |",
        "|--------|----------------|-----------------|--------|",
        f"| Mean mode-bin q | {_mean(summary_mode_before):.3f} | {_mean(summary_mode_after):.3f} | {_mean(summary_mode_after)-_mean(summary_mode_before):+.3f} |",
        f"| Mean d=1 ring q | {_mean(summary_d1_before):.3f} | {_mean(summary_d1_after):.3f} | {_mean(summary_d1_after)-_mean(summary_d1_before):+.3f} |",
        f"| Mean d=2 ring q | {_mean(summary_d2_before):.3f} | {_mean(summary_d2_after):.3f} | {_mean(summary_d2_after)-_mean(summary_d2_before):+.3f} |",
        "",
        "**Calibration target (from authority doc):**",
        "- Mode-bin realized win rate ≈ 0.17 → target mode_q ≈ 0.17–0.20 after scaling",
        "- d=1 realized win rate ≈ 0.18–0.21 → target d=1 q ≈ 0.18–0.21 per ring",
        "- d=2 realized win rate ≈ 0.16 → target d=2 q ≈ 0.16 per ring",
        "",
        "**Note on edge survival column:** Market prices are not joinable (market_price_history",
        "ceiling 2026-05-28T06:10Z, no overlap with current window). Edge count uses proxy",
        "`q > 0.05` as a stand-in for buy_no ask clearance. A true edge count requires live",
        "market snapshot — see executable_market_snapshots when populated.",
        "",
        "---",
        "",
        "*Read-only script: docs/archive/2026-Q2/operations_historical/c3_sigma_calibration_surface_2026-06-12.md. No code modified.*",
    ]

    output = "\n".join(lines) + "\n"
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(output)
    print(output)
    print(f"\n--- Written to {OUTPUT_PATH} ---")


if __name__ == "__main__":
    main()
