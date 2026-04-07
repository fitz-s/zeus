"""Phase 0 Baseline Experiment: GO/NO-GO gate for Zeus.

Question: Does structural mispricing exist in Polymarket weather markets?

Approach:
1. Build city×season climatological distributions from observations
2. Parse range_label text to extract bin boundaries (range_low/range_high are all NULL)
3. For multi-bin markets (11 bins): full favorite-longshot bias analysis
4. For single-bin markets: threshold probability vs outcome analysis
5. Quantify: shoulder win rates vs climatological probability

GO criteria (either suffices):
  a) Sharpe >= 0.5 on climatology-based strategy (requires price data — limited)
  b) Shoulder bins systematically overpriced > 2× vs climatological frequency

Data discovery: token_price_log covers only 2026-03-28 to 2026-04-15 (no historical).
All range_low/range_high in market_events are NULL — parsed from label text.
"""

import json
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_shared_connection as get_connection


# --- City name normalization ---
CITY_ALIASES = {
    "LA": "Los Angeles",
    "SF": "San Francisco",
}


# --- Label parsing ---

def parse_range_label(label: str) -> tuple[float | None, float | None, str]:
    """Parse a Polymarket range label into (low, high, unit).

    Returns (low, high, unit) where:
    - low=None means open lower bound (shoulder-low bin)
    - high=None means open upper bound (shoulder-high bin)
    - For point bins like "4°C": low=4, high=4
    - unit is "F" or "C"

    Label formats:
    - "49°F or below", "43 °F or below", "16°F or lower" → (None, 49, "F")
    - "68°F or higher", "18°C or higher" → (68, None, "F")
    - "50–51 °F", "25–26°F", "50-51°F" → (50, 51, "F")
    - "4°C", "-2°C" → (4, 4, "C")
    """
    label = label.strip()

    # Normalize: replace en-dash with hyphen
    label = label.replace("–", "-")

    # Detect unit
    if "°F" in label or "°f" in label:
        unit = "F"
    elif "°C" in label or "°c" in label:
        unit = "C"
    else:
        # Try bare number range like "10-12"
        m = re.match(r"^(-?\d+\.?\d*)\s*-\s*(-?\d+\.?\d*)", label)
        if m:
            return float(m.group(1)), float(m.group(2)), "F"  # Assume F for bare numbers
        return None, None, "?"

    # Floor bin: "X°F or below" / "X°C or below" / "X°F or lower"
    m = re.match(r"(-?\d+\.?\d*)\s*°[FfCc]\s+or\s+(below|lower)", label)
    if m:
        return None, float(m.group(1)), unit

    # Ceiling bin: "X°F or higher" / "X°C or higher"
    m = re.match(r"(-?\d+\.?\d*)\s*°[FfCc]\s+or\s+(higher|above|more)", label)
    if m:
        return float(m.group(1)), None, unit

    # Range bin: "50-51 °F" / "25-26°F" (with or without space before unit)
    m = re.match(r"(-?\d+\.?\d*)\s*-\s*(-?\d+\.?\d*)\s*°?[FfCc]?", label)
    if m:
        return float(m.group(1)), float(m.group(2)), unit

    # Point bin: "4°C" / "-2°C"
    m = re.match(r"(-?\d+\.?\d*)\s*°[FfCc]", label)
    if m:
        val = float(m.group(1))
        return val, val, unit

    return None, None, "?"


@dataclass
class Bin:
    range_label: str
    low: float | None  # None = open lower bound
    high: float | None  # None = open upper bound
    unit: str
    won: bool

    @property
    def is_shoulder_low(self) -> bool:
        return self.low is None

    @property
    def is_shoulder_high(self) -> bool:
        return self.high is None

    @property
    def is_shoulder(self) -> bool:
        return self.is_shoulder_low or self.is_shoulder_high

    @property
    def is_point(self) -> bool:
        return (self.low is not None and self.high is not None
                and self.low == self.high)

    @property
    def bin_type(self) -> str:
        if self.is_shoulder:
            return "shoulder"
        return "center"


def _season_from_date(date_str: str) -> str:
    month = int(date_str.split("-")[1])
    if month in (12, 1, 2):
        return "DJF"
    elif month in (3, 4, 5):
        return "MAM"
    elif month in (6, 7, 8):
        return "JJA"
    else:
        return "SON"


# --- Climatology builder ---

def build_climatology(conn: sqlite3.Connection) -> dict[str, list[float]]:
    """Build city×season temperature distributions from daily observations.

    Returns dict of "city_SEASON" → list of daily high temps (native units).
    Uses source priority: openmeteo > iem_asos > noaa_ghcnd > meteostat > wu.
    """
    rows = conn.execute("""
        SELECT city, target_date, source, high_temp, unit
        FROM observations
        WHERE high_temp IS NOT NULL
        ORDER BY city, target_date,
            CASE source
                WHEN 'openmeteo_archive' THEN 1
                WHEN 'iem_asos' THEN 2
                WHEN 'noaa_cdo_ghcnd' THEN 3
                WHEN 'meteostat_daily_max' THEN 4
                WHEN 'wu_daily_observed' THEN 5
                ELSE 6
            END
    """).fetchall()

    seen = set()
    clim: dict[str, list[float]] = defaultdict(list)

    for r in rows:
        key = (r["city"], r["target_date"])
        if key in seen:
            continue
        seen.add(key)

        city = r["city"]
        season = _season_from_date(r["target_date"])
        bucket = f"{city}_{season}"
        clim[bucket].append(float(r["high_temp"]))

    return dict(clim)


def compute_clim_probability(
    temps: list[float], low: float | None, high: float | None
) -> float:
    """Fraction of historical temps that fall in [low, high].

    WU settles on integer-rounded values, so we round for bin assignment.
    low=None → open lower bound ("X or below")
    high=None → open upper bound ("X or higher")
    For point bins (°C): "4°C" means the integer rounds to 4
    """
    arr = np.array(temps)
    arr_int = np.round(arr).astype(int)

    if low is None and high is not None:
        # Shoulder low: "X or below"
        count = np.sum(arr_int <= int(high))
    elif high is None and low is not None:
        # Shoulder high: "X or higher"
        count = np.sum(arr_int >= int(low))
    elif low is not None and high is not None:
        if low == high:
            # Point bin: exactly this integer
            count = np.sum(arr_int == int(low))
        else:
            # Range bin: [low, high]
            count = np.sum((arr_int >= int(low)) & (arr_int <= int(high)))
    else:
        return 0.0  # Both None → unparseable, skip

    return float(count) / len(arr) if len(arr) > 0 else 0.0


# --- Market structure ---

def get_market_bins(conn: sqlite3.Connection, city: str, target_date: str) -> list[Bin]:
    """Get parsed bin structure for a specific market."""
    # Normalize city name for query
    query_cities = [city]
    for alias, canonical in CITY_ALIASES.items():
        if city == canonical:
            query_cities.append(alias)
        elif city == alias:
            query_cities.append(canonical)

    placeholders = ",".join(["?"] * len(query_cities))
    rows = conn.execute(f"""
        SELECT range_label, outcome
        FROM market_events
        WHERE city IN ({placeholders}) AND target_date = ?
    """, [*query_cities, target_date]).fetchall()

    if not rows:
        return []

    bins = []
    for r in rows:
        low, high, unit = parse_range_label(r["range_label"])
        if unit == "?":
            continue  # Skip unparseable labels
        bins.append(Bin(
            range_label=r["range_label"],
            low=low, high=high, unit=unit,
            won=(r["outcome"] == "yes"),
        ))

    return bins


# --- Main analysis ---

def run_baseline() -> dict:
    """Run the baseline experiment. Returns results dict."""
    conn = get_connection()

    print("Building climatological distributions...")
    clim = build_climatology(conn)
    print(f"  Built {len(clim)} city×season buckets")
    for k, v in sorted(clim.items()):
        print(f"    {k}: {len(v)} days, range [{min(v):.1f}, {max(v):.1f}]")

    # Get all settlements
    settlements = conn.execute("""
        SELECT city, target_date, winning_bin, settlement_value
        FROM settlements
        ORDER BY target_date
    """).fetchall()
    print(f"\nTotal settlements: {len(settlements)}")

    # Accumulators
    multi_bin = defaultdict(lambda: {
        "n": 0, "wins": 0, "p_clim_values": [],
        "p_clim_when_won": [], "p_clim_when_lost": []
    })
    single_bin_data = []  # (p_clim, outcome, city) for single-bin markets
    results_by_city = defaultdict(lambda: {
        "n_markets": 0, "n_multi": 0, "n_single": 0,
        "shoulder_wins": 0, "shoulder_total": 0,
        "center_wins": 0, "center_total": 0,
        "shoulder_p_clim": [], "center_p_clim": [],
    })
    calibration_data = []  # (p_clim, outcome) for reliability diagram

    skipped = 0
    no_bins = 0
    parse_fails = 0

    for s in settlements:
        city = s["city"]
        date = s["target_date"]
        season = _season_from_date(date)
        bucket = f"{city}_{season}"

        if bucket not in clim or len(clim[bucket]) < 30:
            skipped += 1
            continue

        bins = get_market_bins(conn, city, date)
        if not bins:
            no_bins += 1
            continue

        temps = clim[bucket]
        city_stats = results_by_city[city]
        city_stats["n_markets"] += 1

        is_multi = len(bins) >= 5  # Multi-bin structured market

        if is_multi:
            city_stats["n_multi"] += 1
        else:
            city_stats["n_single"] += 1

        for b in bins:
            p_clim = compute_clim_probability(temps, b.low, b.high)

            if is_multi:
                bt = b.bin_type
                stats = multi_bin[bt]
                stats["n"] += 1
                stats["p_clim_values"].append(p_clim)

                if b.won:
                    stats["wins"] += 1
                    stats["p_clim_when_won"].append(p_clim)
                else:
                    stats["p_clim_when_lost"].append(p_clim)

                # City level
                if b.is_shoulder:
                    city_stats["shoulder_total"] += 1
                    city_stats["shoulder_p_clim"].append(p_clim)
                    if b.won:
                        city_stats["shoulder_wins"] += 1
                else:
                    city_stats["center_total"] += 1
                    city_stats["center_p_clim"].append(p_clim)
                    if b.won:
                        city_stats["center_wins"] += 1
            else:
                # Single-bin market
                single_bin_data.append((p_clim, 1 if b.won else 0, city))

            calibration_data.append((p_clim, 1 if b.won else 0))

    conn.close()

    print(f"  Analyzed: {sum(c['n_markets'] for c in results_by_city.values())} markets")
    print(f"  Skipped (insufficient climatology): {skipped}")
    print(f"  No bin data found: {no_bins}")

    # --- Results ---
    results = {
        "n_settlements_analyzed": sum(c["n_markets"] for c in results_by_city.values()),
        "n_skipped": skipped,
        "n_no_bins": no_bins,
        "multi_bin_analysis": {},
        "single_bin_analysis": {},
        "city_analysis": {},
        "calibration": {},
        "go_decision": None,
    }

    print("\n" + "=" * 70)
    print("BASELINE EXPERIMENT RESULTS")
    print("=" * 70)

    # 1. Multi-bin market analysis (structured 11-bin markets)
    print("\n--- Multi-Bin Markets: Shoulder vs Center ---")
    n_multi = sum(c["n_multi"] for c in results_by_city.values())
    print(f"Markets with ≥5 bins: {n_multi}")

    if n_multi > 0:
        print(f"\n{'Type':<12} {'N bins':>8} {'Wins':>6} {'Win%':>7} "
              f"{'Avg P_clim':>10} {'Win/P_clim':>10}")
        print("-" * 60)

        for bt in ["shoulder", "center"]:
            s = multi_bin[bt]
            if s["n"] == 0:
                continue
            win_rate = s["wins"] / s["n"]
            avg_p = np.mean(s["p_clim_values"]) if s["p_clim_values"] else 0
            ratio = win_rate / avg_p if avg_p > 0 else float("inf")

            results["multi_bin_analysis"][bt] = {
                "n": s["n"], "wins": s["wins"],
                "win_rate": round(win_rate, 4),
                "avg_p_clim": round(float(avg_p), 4),
                "ratio": round(ratio, 3),
            }
            print(f"{bt:<12} {s['n']:>8} {s['wins']:>6} {win_rate:>6.1%} "
                  f"{avg_p:>10.4f} {ratio:>10.3f}")

        # FLB quantification
        print("\n--- Favorite-Longshot Bias Quantification ---")
        sh = multi_bin["shoulder"]
        ct = multi_bin["center"]
        if sh["n"] > 0 and ct["n"] > 0:
            sh_wr = sh["wins"] / sh["n"]
            ct_wr = ct["wins"] / ct["n"]
            sh_avg_p = np.mean(sh["p_clim_values"])
            ct_avg_p = np.mean(ct["p_clim_values"])

            print(f"Shoulder bins: win {sh_wr:.1%}, P_clim avg = {sh_avg_p:.4f}")
            print(f"  → Market must price shoulders > {sh_wr:.4f} for FLB")
            print(f"  → If market prices at 2× P_clim = {2*sh_avg_p:.4f}, "
                  f"overpricing ratio = {sh_wr / (2*sh_avg_p):.2f}× vs win rate")
            print(f"Center bins: win {ct_wr:.1%}, P_clim avg = {ct_avg_p:.4f}")
            print(f"  → Center wins at {ct_wr/ct_avg_p:.1f}× their climatological rate")
            print(f"  → Center bins are UNDERPRICED if market < {ct_wr:.4f}")

            if sh["p_clim_when_won"]:
                print(f"\nShoulder winners P_clim: "
                      f"mean={np.mean(sh['p_clim_when_won']):.4f}, "
                      f"median={np.median(sh['p_clim_when_won']):.4f}")
            if sh["p_clim_when_lost"]:
                print(f"Shoulder losers P_clim: "
                      f"mean={np.mean(sh['p_clim_when_lost']):.4f}, "
                      f"median={np.median(sh['p_clim_when_lost']):.4f}")

    # 2. Single-bin market analysis
    print("\n--- Single-Bin Threshold Markets ---")
    if single_bin_data:
        sb_arr = np.array([(p, o) for p, o, _ in single_bin_data])
        n_single = len(sb_arr)
        sb_wins = int(sb_arr[:, 1].sum())
        sb_win_rate = sb_arr[:, 1].mean()
        sb_avg_p = sb_arr[:, 0].mean()
        ratio = sb_win_rate / sb_avg_p if sb_avg_p > 0 else 0

        print(f"Total single-bin records: {n_single}")
        print(f"Win rate: {sb_win_rate:.1%} (outcomes = 'yes')")
        print(f"Avg climatological probability: {sb_avg_p:.4f}")
        print(f"Ratio (actual/expected): {ratio:.3f}")

        results["single_bin_analysis"] = {
            "n": n_single, "wins": sb_wins,
            "win_rate": round(float(sb_win_rate), 4),
            "avg_p_clim": round(float(sb_avg_p), 4),
            "ratio": round(ratio, 3),
        }

    # 3. Per-city analysis
    print("\n--- Per-City Analysis (multi-bin markets only) ---")
    print(f"{'City':<15} {'Multi':>5} {'ShWin%':>7} {'ShP_clim':>9} "
          f"{'CtrWin%':>8} {'CtrP_clim':>9}")
    print("-" * 60)

    for city in sorted(results_by_city.keys()):
        cs = results_by_city[city]
        if cs["n_multi"] == 0:
            continue
        sh_wr = cs["shoulder_wins"] / cs["shoulder_total"] if cs["shoulder_total"] > 0 else 0
        sh_pc = float(np.mean(cs["shoulder_p_clim"])) if cs["shoulder_p_clim"] else 0
        ct_wr = cs["center_wins"] / cs["center_total"] if cs["center_total"] > 0 else 0
        ct_pc = float(np.mean(cs["center_p_clim"])) if cs["center_p_clim"] else 0

        results["city_analysis"][city] = {
            "n_multi": cs["n_multi"],
            "shoulder_win_rate": round(sh_wr, 4),
            "shoulder_p_clim": round(sh_pc, 4),
            "center_win_rate": round(ct_wr, 4),
            "center_p_clim": round(ct_pc, 4),
        }
        print(f"{city:<15} {cs['n_multi']:>5} {sh_wr:>6.1%} {sh_pc:>9.4f} "
              f"{ct_wr:>7.1%} {ct_pc:>9.4f}")

    # 4. Calibration reliability diagram
    print("\n--- Climatology Calibration Reliability ---")
    cal_arr = np.array(calibration_data)
    if len(cal_arr) > 0:
        edges = [0, 0.01, 0.03, 0.05, 0.10, 0.15, 0.25, 0.50, 1.01]
        print(f"{'P_clim bucket':<16} {'N':>7} {'Actual%':>8} {'Expected':>9} {'Ratio':>7}")
        print("-" * 50)

        for i in range(len(edges) - 1):
            mask = (cal_arr[:, 0] >= edges[i]) & (cal_arr[:, 0] < edges[i + 1])
            n = int(mask.sum())
            if n < 5:
                continue
            actual = float(cal_arr[mask, 1].mean())
            expected = float(cal_arr[mask, 0].mean())
            ratio = actual / expected if expected > 0 else float("inf")

            bk = f"[{edges[i]:.2f}, {edges[i+1]:.2f})"
            print(f"{bk:<16} {n:>7} {actual:>7.1%} {expected:>9.4f} {ratio:>7.2f}")
            results["calibration"][bk] = {
                "n": n, "actual": round(actual, 4),
                "expected": round(expected, 4), "ratio": round(ratio, 2)
            }

    # 5. GO/NO-GO
    print("\n" + "=" * 70)
    print("GO/NO-GO DECISION")
    print("=" * 70)

    go_reasons = []
    nogo_reasons = []

    # Condition b: FLB analysis
    sh = multi_bin["shoulder"]
    ct = multi_bin["center"]

    if sh["n"] > 0:
        sh_wr = sh["wins"] / sh["n"]
        sh_avg_p = float(np.mean(sh["p_clim_values"]))

        if sh_avg_p > 0 and sh_wr < sh_avg_p:
            go_reasons.append(
                f"SHOULDER OVERPRICING: Shoulder bins win at {sh_wr:.1%} but "
                f"climatology gives them {sh_avg_p:.1%} probability. "
                f"Any market price > {sh_wr:.4f} is overpricing."
            )
        elif sh_avg_p > 0:
            # Shoulder wins MORE than climatology predicts
            go_reasons.append(
                f"Shoulder win rate ({sh_wr:.1%}) >= climatological ({sh_avg_p:.1%}). "
                f"FLB may still exist in pricing even if win rates are calibrated."
            )

    if ct["n"] > 0:
        ct_wr = ct["wins"] / ct["n"]
        ct_avg_p = float(np.mean(ct["p_clim_values"]))
        if ct_avg_p > 0 and ct_wr / ct_avg_p > 1.5:
            go_reasons.append(
                f"CENTER UNDERPRICING: Center bins win at {ct_wr:.1%} "
                f"but climatology predicts only {ct_avg_p:.1%} — "
                f"ratio {ct_wr/ct_avg_p:.1f}×. Market underprices center bins."
            )

    # Check single-bin data
    if single_bin_data:
        sb_arr = np.array([(p, o) for p, o, _ in single_bin_data])
        sb_wr = float(sb_arr[:, 1].mean())
        sb_p = float(sb_arr[:, 0].mean())
        if sb_p > 0 and abs(sb_wr - sb_p) > 0.05:
            go_reasons.append(
                f"Single-bin markets show {sb_wr:.1%} win rate vs "
                f"{sb_p:.1%} climatological expectation — "
                f"mispricing signal present across {len(sb_arr)} observations."
            )

    # Sample size concern
    n_multi = sum(c["n_multi"] for c in results_by_city.values())
    if n_multi < 20:
        nogo_reasons.append(
            f"Only {n_multi} multi-bin markets available — small sample. "
            f"Need more structured market history for confidence."
        )

    if go_reasons and not nogo_reasons:
        decision = "GO"
    elif go_reasons and nogo_reasons:
        decision = "CONDITIONAL GO"
    else:
        decision = "NO-GO"

    results["go_decision"] = decision
    results["go_reasons"] = go_reasons
    results["nogo_reasons"] = nogo_reasons

    print(f"\nDecision: {decision}")
    for r in go_reasons:
        print(f"  [GO] {r}")
    for r in nogo_reasons:
        print(f"  [CONCERN] {r}")

    print("\n--- Data Limitations ---")
    print(f"  Multi-bin markets: {n_multi} (from structured 11-bin grid)")
    print(f"  Single-bin markets: {len(single_bin_data)} (binary threshold)")
    print(f"  No historical market prices for Sharpe calculation")
    print(f"  Climatology buckets: {len(clim)}")

    return results


if __name__ == "__main__":
    results = run_baseline()
    output_path = PROJECT_ROOT / "state" / "baseline_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {output_path}")
