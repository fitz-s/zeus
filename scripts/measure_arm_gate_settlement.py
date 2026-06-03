# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: arm-gate MEASURE step, GOAL live profit >51% after-cost settlement
#   win-rate (GOAL#36 / project_live_goal_2026_06_03.md); settlement truth =
#   zeus-forecasts.db.settlement_outcomes WHERE authority='VERIFIED'.
#
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=never
# Purpose: Reproducible after-cost settlement win-rate measurement for EDLI shadow
#   positions, split by ALL and gate-PASS (mainstream_agreement_pass=1) cohorts.
#   Computes the ARM verdict: ELIGIBLE only if gate-PASS win_rate stably >51% after
#   cost, >=2sigma above breakeven, n>=20 pooled / n>=5 per city.
#   ANTI-FABRICATION: prints NULL/INSUFFICIENT verdict if gate-PASS cohort is empty.
# Reuse: re-run after each new batch of VERIFIED settlements lands. Gate-PASS receipts
#   must post-date gate deployment (commit fe53b00c98, ~2026-06-03). Inspect the
#   printed "gate-PASS overlap" count; if still 0, verdict stays INSUFFICIENT.
#
"""Measure after-cost settlement win-rate for EDLI shadow positions.

Direction Law (must hold exactly):
    buy_yes on bin B: WIN iff settlement lands IN bin B  (settled_bin == B)
    buy_no  on bin B: WIN iff settlement does NOT land in bin B  (settled_bin != B)

Bin matching:
    The traded bin is extracted from receipt_json["bin_label"] using
    src.data.market_scanner._parse_temp_range which returns (lo, hi).
    The settlement value comes from settlement_outcomes.settlement_value
    (already WMO half-up rounded, integer precision).
    Bin containment: lo <= settlement_value <= hi (float-tolerant).
    Shoulder bins: lo=None means settlement_value <= hi; hi=None means >= lo.
    Unit: extracted from the bin_label text (°C or °F). Must match
    settlement_outcomes.settlement_unit. Mismatch rows are EXCLUDED with a warning.

Dedup: one final shadow position per (city, target_date, token_id, direction) —
    latest decision_time kept (same approach as prior measurement).
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import sys
from collections import defaultdict
from typing import Optional

# Ensure the repo root is on sys.path so src.* imports resolve correctly
# when the script is executed directly (python scripts/measure_arm_gate_settlement.py).
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_CANDIDATE = os.path.dirname(_SCRIPT_DIR)
if _ROOT_CANDIDATE not in sys.path:
    sys.path.insert(0, _ROOT_CANDIDATE)

# ---------------------------------------------------------------------------
# Repo root — walk up from __file__ to find the directory that contains
# state/zeus-world.db. Works from worktrees (e.g. /tmp/zeus-armgate) where
# state/ may not exist locally, falling back to the git-tracked worktree's
# common directory (i.e. the real working tree with live DBs).
# ---------------------------------------------------------------------------

def _find_repo_root() -> str:
    """Return the repo root directory that contains state/zeus-world.db.

    Search order:
      1. Walk up from this script's location until state/zeus-world.db found.
      2. Fall back to git rev-parse --show-toplevel (handles worktrees).
    """
    candidate = os.path.dirname(os.path.abspath(__file__))
    while True:
        probe = os.path.join(candidate, "state", "zeus-world.db")
        if os.path.exists(probe):
            return candidate
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent

    # Fallback: ask git for the common .git dir (always points to main worktree's .git)
    try:
        import subprocess
        common_git = subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            text=True,
        ).strip().rstrip("/")
        # common_git is like /repo/.git — strip trailing /.git or .git component
        # to get the working tree root.
        if common_git.endswith("/.git"):
            repo_root = common_git[: -len("/.git")]
        elif common_git.endswith(".git"):
            repo_root = os.path.dirname(common_git)
        else:
            repo_root = os.path.dirname(common_git)
        probe = os.path.join(repo_root, "state", "zeus-world.db")
        if os.path.exists(probe):
            return repo_root
    except Exception:
        pass

    # Last resort: use script location's parent
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_REPO_ROOT = _find_repo_root()
_WORLD_DB = os.path.join(_REPO_ROOT, "state", "zeus-world.db")
_FORECASTS_DB = os.path.join(_REPO_ROOT, "state", "zeus-forecasts.db")


# ---------------------------------------------------------------------------
# Pure win-logic function (importable for unit tests)
# ---------------------------------------------------------------------------

def is_win(direction: str, traded_bin_lo: Optional[float], traded_bin_hi: Optional[float],
           settlement_value: float, tolerance: float = 1e-9) -> bool:
    """Return True iff this shadow position wins under the Direction Law.

    Direction Law:
        buy_yes on bin B: WIN iff settlement lands IN bin B
        buy_no  on bin B: WIN iff settlement does NOT land in bin B

    Args:
        direction: "buy_yes" or "buy_no"
        traded_bin_lo: lower bound of traded bin (None = left-shoulder, i.e. "X or below")
        traded_bin_hi: upper bound of traded bin (None = right-shoulder, i.e. "X or higher")
        settlement_value: the WMO-half-up rounded settlement temperature
        tolerance: float comparison tolerance (default 1e-9)

    Returns:
        True if the position wins, False if it loses.

    Raises:
        ValueError: if direction is not 'buy_yes' or 'buy_no'.

    None handling:
        traded_bin_lo=None, traded_bin_hi set  → left-shoulder bin ("X or below")
        traded_bin_lo set, traded_bin_hi=None  → right-shoulder bin ("X or higher")
        Both None                               → bin parse failed; returns False (cannot
            determine win/loss, must not count as win). Callers should skip such rows
            before calling is_win() to avoid silently dropping valid data.
    """
    # Both None means the bin label failed to parse — cannot evaluate win/loss.
    if traded_bin_lo is None and traded_bin_hi is None:
        return False

    # Determine whether settlement lands in the traded bin
    if traded_bin_lo is None:
        # Left-shoulder: "X or below" — settlement_value <= hi
        in_bin = settlement_value <= traded_bin_hi + tolerance
    elif traded_bin_hi is None:
        # Right-shoulder: "X or higher" — settlement_value >= lo
        in_bin = settlement_value >= traded_bin_lo - tolerance
    else:
        # Point or bounded range
        in_bin = (traded_bin_lo - tolerance) <= settlement_value <= (traded_bin_hi + tolerance)

    if direction == "buy_yes":
        return in_bin
    elif direction == "buy_no":
        return not in_bin
    else:
        raise ValueError(f"Unknown direction: {direction!r}. Expected 'buy_yes' or 'buy_no'.")


# ---------------------------------------------------------------------------
# Bin label parsing helpers
# ---------------------------------------------------------------------------

def _parse_temp_range_local(label: str) -> Optional[tuple[Optional[float], Optional[float]]]:
    """Parse (lo, hi) from a market bin_label string.

    Delegates to src.data.market_scanner._parse_temp_range.
    Returns None if parse fails.
    """
    try:
        from src.data.market_scanner import _parse_temp_range
        return _parse_temp_range(label)
    except Exception:
        return None


def _extract_unit(label: str) -> Optional[str]:
    """Extract unit letter ('C' or 'F') from a bin_label string."""
    m = re.search(r"\d+°([CF])", label)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_deduped_receipts(world_db: str) -> list[dict]:
    """Load edli_no_submit_receipts, deduped to one final position per
    (city, target_date, token_id, direction) — latest decision_time kept.
    Returns a list of dicts with parsed fields.
    """
    conn = sqlite3.connect(f"file:{world_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT receipt_id, token_id, direction, c_fee_adjusted,
               mainstream_agreement_pass, decision_time, receipt_json
        FROM edli_no_submit_receipts
        ORDER BY decision_time DESC
    """)

    seen: dict[tuple, dict] = {}  # key -> row dict (latest decision wins)
    for row in cur.fetchall():
        d = dict(row)
        rj = json.loads(d["receipt_json"])
        city = rj.get("city")
        target_date = rj.get("target_date")
        token_id = d.get("token_id") or rj.get("token_id")
        direction = d["direction"]
        bin_label = rj.get("bin_label", "")
        metric = rj.get("metric", "high")

        key = (city, target_date, token_id, direction)
        if key not in seen:
            parsed = _parse_temp_range_local(bin_label)
            unit = _extract_unit(bin_label)
            seen[key] = {
                "city": city,
                "target_date": target_date,
                "metric": metric,
                "direction": direction,
                "c_fee_adjusted": d["c_fee_adjusted"],
                "mainstream_agreement_pass": d["mainstream_agreement_pass"],
                "decision_time": d["decision_time"],
                "bin_label": bin_label,
                "traded_lo": parsed[0] if parsed else None,
                "traded_hi": parsed[1] if parsed else None,
                "unit": unit,
            }

    conn.close()
    return list(seen.values())


def _load_settlements(forecasts_db: str) -> dict[tuple, dict]:
    """Load VERIFIED settlement_outcomes, keyed by (city, target_date, temperature_metric)."""
    conn = sqlite3.connect(f"file:{forecasts_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT city, target_date, temperature_metric,
               settlement_value, settlement_unit, winning_bin
        FROM settlement_outcomes
        WHERE authority = 'VERIFIED'
    """)
    result: dict[tuple, dict] = {}
    for row in cur.fetchall():
        d = dict(row)
        key = (d["city"], d["target_date"], d["temperature_metric"])
        # If duplicate, prefer first (shouldn't happen on VERIFIED)
        if key not in result:
            result[key] = d
    conn.close()
    return result


# ---------------------------------------------------------------------------
# Win-rate computation
# ---------------------------------------------------------------------------

def _compute_rows(receipts: list[dict], settlements: dict) -> list[dict]:
    """Join receipts to settlements and compute win flag per Direction Law.

    Returns list of dicts with: city, target_date, direction, win, price,
    gate_pass, matched_settlement_value, matched_settlement_unit.
    Only rows with a matched VERIFIED settlement are included.
    Unit-mismatch rows are excluded (logged to stderr).
    """
    rows = []
    unit_mismatch = 0

    for r in receipts:
        key = (r["city"], r["target_date"], r["metric"])
        s = settlements.get(key)
        if s is None:
            continue  # no VERIFIED settlement for this (city, date, metric)

        # Unit sanity check: traded bin unit must match settlement unit
        traded_unit = r["unit"]
        settle_unit = s["settlement_unit"]
        if traded_unit is not None and settle_unit is not None:
            if traded_unit.upper() != settle_unit.upper():
                unit_mismatch += 1
                print(
                    f"  [WARN] unit mismatch: city={r['city']} date={r['target_date']} "
                    f"traded_unit={traded_unit} settle_unit={settle_unit} — row EXCLUDED",
                    file=sys.stderr,
                )
                continue

        lo, hi = r["traded_lo"], r["traded_hi"]
        if lo is None and hi is None:
            # parse failure — skip
            continue

        win = is_win(r["direction"], lo, hi, s["settlement_value"])
        rows.append({
            "city": r["city"],
            "target_date": r["target_date"],
            "direction": r["direction"],
            "win": win,
            "price": r["c_fee_adjusted"],
            "gate_pass": r["mainstream_agreement_pass"] == 1,
            "settlement_value": s["settlement_value"],
            "settlement_unit": s["settlement_unit"],
            "traded_lo": lo,
            "traded_hi": hi,
            "bin_label": r["bin_label"],
        })

    if unit_mismatch:
        print(f"  [WARN] {unit_mismatch} rows excluded due to unit mismatch.", file=sys.stderr)

    return rows


def _stats(rows: list[dict], label: str) -> dict:
    """Compute aggregate stats for a list of rows."""
    n = len(rows)
    if n == 0:
        return {"label": label, "n": 0, "win_rate": None, "breakeven": None,
                "roi": None, "mean_ev": None, "se_ev": None, "ev_sigma": None,
                "arm_eligible": False}
    wins = sum(1 for r in rows if r["win"])
    win_rate = wins / n
    avg_price = sum(r["price"] for r in rows) / n
    # After-cost EV per trade: payout if win is (1 - price), loss if lose is (-price)
    # In USDC prediction market: win pays out $1 per share, so net = (1 - price) for win,
    # net = (-price) for loss. ROI = total net / total invested
    total_invested = sum(r["price"] for r in rows)
    total_net = sum((1.0 - r["price"]) if r["win"] else (-r["price"]) for r in rows)
    roi = total_net / total_invested if total_invested > 0 else 0.0

    # Per-trade EV = win_rate - price (in 0-1 USDC space)
    evs = [(1.0 - r["price"]) if r["win"] else (-r["price"]) for r in rows]
    mean_ev = sum(evs) / n
    variance = sum((e - mean_ev) ** 2 for e in evs) / n if n > 1 else 0.0
    se = math.sqrt(variance / n) if n > 1 else float("inf")

    # win_rate vs breakeven (price) in points
    edge_points = win_rate - avg_price
    # sigma above breakeven
    ev_sigma = mean_ev / se if se > 0 else 0.0

    return {
        "label": label,
        "n": n,
        "win_rate": win_rate,
        "breakeven": avg_price,
        "roi": roi,
        "mean_ev": mean_ev,
        "se_ev": se,
        "ev_sigma": ev_sigma,
        "edge_points": edge_points,
        "arm_eligible": False,  # evaluated separately
    }


def _arm_verdict(stats: dict, rows: list[dict], min_n_pooled: int = 20,
                 min_sigma: float = 2.0, min_win_rate: float = 0.51,
                 min_n_per_city: int = 5) -> tuple[bool, str]:
    """Return (arm_eligible, reason) for a gate-PASS cohort.

    Enforces ALL documented ARM criteria (must match script header / PR claim):
        1. Pooled n >= min_n_pooled  (default 20)
        2. win_rate > min_win_rate   (default 51%)
        3. ev_sigma >= min_sigma     (default 2.0)
        4. Every city in the cohort has n >= min_n_per_city (default 5)
           A city with n < 5 is NOT eligible; pooled criteria alone are insufficient.

    Tightening note: the previous version only checked 1-3. Criterion 4 is added here
    to match the documented arm criteria exactly (per script header line 11-12).
    This is an ARM-DECISION tool — a too-lenient verdict is dangerous.
    """
    if stats["n"] == 0:
        return False, "INSUFFICIENT: gate-PASS cohort empty (no overlap with VERIFIED settlements)"
    if stats["n"] < min_n_pooled:
        return False, f"INSUFFICIENT: n={stats['n']} < {min_n_pooled} minimum"
    if stats["win_rate"] is None or stats["win_rate"] <= min_win_rate:
        return False, f"DENIED: win_rate={stats['win_rate']:.3f} not > {min_win_rate}"
    if stats["ev_sigma"] is None or stats["ev_sigma"] < min_sigma:
        return False, f"DENIED: ev_sigma={stats['ev_sigma']:.2f} < {min_sigma:.1f} (insufficient confidence)"

    # Per-city n >= min_n_per_city check (criterion 4 — must all pass)
    by_city: dict[str, int] = defaultdict(int)
    for r in rows:
        by_city[r["city"]] += 1
    thin_cities = sorted(c for c, n in by_city.items() if n < min_n_per_city)
    if thin_cities:
        thin_detail = ", ".join(f"{c}(n={by_city[c]})" for c in thin_cities)
        return False, (
            f"DENIED: per-city n<{min_n_per_city} for {len(thin_cities)} city/cities: "
            f"{thin_detail}. ALL cities must have n>={min_n_per_city} to be ARM-ELIGIBLE."
        )

    return True, (
        f"ELIGIBLE: win_rate={stats['win_rate']:.3f} > {min_win_rate}, "
        f"ev_sigma={stats['ev_sigma']:.2f} >= {min_sigma}, "
        f"all {len(by_city)} cities n>={min_n_per_city}"
    )


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def _fmt_pct(v) -> str:
    return f"{v*100:.1f}%" if v is not None else "  N/A "


def _fmt_f(v, fmt=".4f") -> str:
    return format(v, fmt) if v is not None else "  N/A  "


def _print_stats_row(s: dict) -> None:
    label = s["label"]
    n = s["n"]
    if n == 0:
        print(f"  {label:<30s}  n={n:>5}  [EMPTY — no settled overlap]")
        return
    wr = _fmt_pct(s["win_rate"])
    be = _fmt_pct(s["breakeven"])
    roi = _fmt_pct(s["roi"])
    ev = _fmt_f(s["mean_ev"])
    se = _fmt_f(s["se_ev"])
    sigma = _fmt_f(s.get("ev_sigma"), ".2f")
    edge = _fmt_pct(s.get("edge_points"))
    print(
        f"  {label:<30s}  n={n:>5}  wr={wr}  be={be}  "
        f"roi={roi}  ev={ev}±{se}  σ={sigma}  edge={edge}"
    )


def _print_per_city_table(rows: list[dict], title: str) -> None:
    if not rows:
        print(f"\n  {title}: [empty]")
        return
    print(f"\n  {title} (per city):")
    by_city: dict[str, list] = defaultdict(list)
    for r in rows:
        by_city[r["city"]].append(r)
    for city in sorted(by_city):
        s = _stats(by_city[city], city)
        _print_stats_row(s)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 78)
    print("EDLI ARM-GATE SETTLEMENT WIN-RATE MEASUREMENT")
    print(f"DB sources:  {_WORLD_DB}")
    print(f"             {_FORECASTS_DB}")
    print("=" * 78)

    # --- Load ---
    receipts = _load_deduped_receipts(_WORLD_DB)
    settlements = _load_settlements(_FORECASTS_DB)
    print(f"\nDeduped shadow positions:        {len(receipts):>6}")
    print(f"VERIFIED settlements:            {len(settlements):>6}")

    # --- Join ---
    all_rows = _compute_rows(receipts, settlements)
    gate_rows = [r for r in all_rows if r["gate_pass"]]

    # Coverage summary
    settled_dates = sorted({(r["city"], r["target_date"]) for r in all_rows})
    gate_dates = sorted({(r["city"], r["target_date"]) for r in gate_rows})
    print(f"\nSettled (city,date) pairs in ALL cohort:      {len(settled_dates)}")
    print(f"Settled (city,date) pairs in gate-PASS cohort:{len(gate_dates)}")

    if not gate_rows:
        print("\n" + "!" * 78)
        print("  GATE-PASS COHORT: EMPTY")
        print("  All receipts with mainstream_agreement_pass=1 target dates NOT YET")
        print("  covered by VERIFIED settlements (gate deployed post-settlement cutoff).")
        print("  Dates in gate-PASS receipts:", sorted({r["target_date"] for r in
              [x for x in _load_deduped_receipts(_WORLD_DB) if x["mainstream_agreement_pass"] == 1]}))
        print("  Most recent VERIFIED settlement date:",
              max((s["target_date"] for s in settlements.values()), default="none"))
        print("!" * 78)

    # --- ALL cohort stats ---
    print("\n" + "=" * 78)
    print("COHORT: ALL (ungated, pooled)")
    print("=" * 78)
    all_stats = _stats(all_rows, "ALL pooled")
    _print_stats_row(all_stats)
    _print_per_city_table(all_rows, "ALL")

    # --- Gate-PASS cohort stats ---
    print("\n" + "=" * 78)
    print("COHORT: GATE-PASS (mainstream_agreement_pass=1)")
    print("=" * 78)
    gate_stats = _stats(gate_rows, "GATE-PASS pooled")
    _print_stats_row(gate_stats)
    _print_per_city_table(gate_rows, "GATE-PASS")

    # --- ARM verdict ---
    arm_eligible, arm_reason = _arm_verdict(gate_stats, gate_rows)
    print("\n" + "=" * 78)
    print("ARM VERDICT (gate-PASS cohort, thresholds: win_rate>51%, sigma>=2.0, n>=20, per-city n>=5)")
    print("=" * 78)
    verdict = "ARM: ELIGIBLE" if arm_eligible else "ARM: DENIED/INSUFFICIENT"
    print(f"  {verdict}")
    print(f"  Reason: {arm_reason}")
    print(f"  NOTE: ARM verdict is based ONLY on gate-PASS cohort, not ALL cohort.")
    print(f"        A non-gate-PASS win rate, however high, does NOT satisfy the ARM criterion.")
    print("=" * 78)


if __name__ == "__main__":
    main()
