#!/usr/bin/env python3
# Created: 2026-06-02
# Last reused/audited: 2026-06-02
# Authority basis: EMOS shadow-ledger task; PIECE 3 spec.
#   Reads state/emos_shadow_ledger.jsonl + zeus-world.db (read-only).
#   Live truth: observation_instants.running_max (max over the day, WU station).
#   Metrics: Brier score + log-score on realized bin, raw vs emos, per-city + aggregate.
#   LIVE TRUTH ONLY — no ERA5/online fetches.
"""Score EMOS shadow-ledger predictions against live-truth settlement.

Usage:
    python scripts/score_emos_forward.py

For each (city, target_date) in the ledger whose date is settled (past
today), fetches the live-truth daily maximum from observation_instants
and evaluates raw_q and emos_q against the outcome.

If too few settled rows exist, reports counts and exits cleanly.
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from math import log

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(REPO, "state", "emos_shadow_ledger.jsonl")
WORLD = os.path.join(REPO, "state", "zeus-world.db")

TODAY = date.today()
LOG_CLIP = 1e-9  # floor for log-score to avoid -inf


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_ledger():
    """Load all rows from emos_shadow_ledger.jsonl. Returns list of dicts."""
    rows = []
    if not os.path.exists(LEDGER):
        return rows
    with open(LEDGER, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"  [WARN] ledger line {lineno} JSON parse error: {exc}")
    return rows


def _live_truth_by_city_date(cities_dates: set) -> dict:
    """Fetch the live-truth daily-max for each (city, target_date) pair.

    Returns dict keyed by (city, target_date) → float | None.
    Queries observation_instants.running_max max over the target day.
    Authority: WU station readings ingested into zeus-world.db.
    """
    result = {}
    if not cities_dates:
        return result
    if not os.path.exists(WORLD):
        print(f"  [ERROR] zeus-world.db not found at {WORLD}")
        return result
    try:
        conn = sqlite3.connect(f"file:{WORLD}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        # Check the table exists
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "observation_instants" not in tables:
            print("  [WARN] observation_instants table not found in zeus-world.db")
            conn.close()
            return result
        for city, target_date in cities_dates:
            try:
                row = conn.execute(
                    """
                    SELECT MAX(running_max) AS daily_max, temp_unit
                    FROM observation_instants
                    WHERE city = ? AND target_date = ?
                      AND running_max IS NOT NULL
                      AND COALESCE(authority, 'UNVERIFIED') != 'UNVERIFIED'
                    """,
                    (city, target_date),
                ).fetchone()
                if row and row["daily_max"] is not None:
                    result[(city, target_date)] = (float(row["daily_max"]), row["temp_unit"] or "C")
                else:
                    result[(city, target_date)] = None
            except Exception as exc:
                print(f"  [WARN] truth query failed for ({city}, {target_date}): {exc}")
                result[(city, target_date)] = None
        conn.close()
    except Exception as exc:
        print(f"  [ERROR] Failed to open zeus-world.db: {exc}")
    return result


def _to_bin_unit(value_raw: float, raw_unit: str, target_unit: str) -> float:
    """Convert a temperature value to target_unit ('C' or 'F')."""
    if raw_unit == target_unit:
        return value_raw
    if raw_unit == "C" and target_unit == "F":
        return value_raw * 9.0 / 5.0 + 32.0
    if raw_unit == "F" and target_unit == "C":
        return (value_raw - 32.0) * 5.0 / 9.0
    return value_raw  # unknown unit, pass through


def _bin_contains(truth: float, bin_low, bin_high) -> bool:
    """True if truth falls in [low, high).  Open shoulders: low=None → -inf, high=None → +inf."""
    lo_ok = (bin_low is None) or (truth >= bin_low)
    hi_ok = (bin_high is None) or (truth < bin_high)
    return lo_ok and hi_ok


def _safe_log(q: float) -> float:
    return log(max(q, LOG_CLIP))


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("EMOS Forward Scoring  —  raw ensemble vs EMOS calibrator")
    print("=" * 70)
    print()

    # 1. Load ledger
    all_rows = _load_ledger()
    if not all_rows:
        print("Ledger is empty (0 rows).  Enable the flag and collect data first.")
        print(f"  Ledger path: {LEDGER}")
        print("  Flag: settings['edli_v1']['edli_emos_shadow_ledger_enabled'] = true")
        return

    total_rows = len(all_rows)
    print(f"Ledger rows: {total_rows}")

    # 2. Identify settled (city, target_date) pairs — target_date < TODAY
    settled_rows = [
        r for r in all_rows
        if r.get("target_date") and r["target_date"] < str(TODAY)
    ]
    unsettled_count = total_rows - len(settled_rows)
    print(f"  settled rows (target_date < {TODAY}): {len(settled_rows)}")
    print(f"  unsettled rows (future / today):    {unsettled_count}")
    print()

    if not settled_rows:
        print("No settled rows yet — check back after target dates have passed.")
        print(f"  Earliest target_date in ledger: "
              f"{min(r.get('target_date','?') for r in all_rows)}")
        return

    # 3. Fetch live truth for settled pairs
    settled_pairs = {(r["city"], r["target_date"]) for r in settled_rows}
    truth_map = _live_truth_by_city_date(settled_pairs)

    pairs_with_truth = sum(1 for v in truth_map.values() if v is not None)
    print(f"  distinct (city, date) pairs:  {len(settled_pairs)}")
    print(f"  with live-truth in obs table: {pairs_with_truth}")
    print()

    if pairs_with_truth == 0:
        print("No live truth available in observation_instants for settled dates.")
        print("Either the WU data hasn't been imported yet or the authority filter is blocking it.")
        return

    # 4. Score rows
    # Group rows by family (city, target_date, unique bins identified by family/condition)
    # For each row compute outcome = 1 if realized bin, else 0.
    # Use integer rounding: truth is rounded to nearest integer in bin_unit.
    per_city: dict[str, dict] = defaultdict(lambda: {
        "raw_brier": [], "emos_brier": [],
        "raw_logprob": [], "emos_logprob": [],
        "raw_q_win": [], "emos_q_win": [],
        "rows_scored": 0, "rows_skipped": 0,
    })
    agg = {
        "raw_brier": [], "emos_brier": [],
        "raw_logprob": [], "emos_logprob": [],
        "raw_q_win": [], "emos_q_win": [],
    }
    rows_scored = 0
    rows_skipped = 0
    rows_no_emos = 0

    for row in settled_rows:
        city = row.get("city", "")
        target_date = row.get("target_date", "")
        truth_entry = truth_map.get((city, target_date))
        if truth_entry is None:
            rows_skipped += 1
            per_city[city]["rows_skipped"] += 1
            continue

        truth_raw, truth_unit = truth_entry
        bin_unit = row.get("bin_unit", "C")
        # Convert truth to bin_unit for comparison
        truth = _to_bin_unit(truth_raw, truth_unit, bin_unit)
        # Integer rounding (WU settlement)
        truth_rounded = round(truth)

        bin_low = row.get("bin_low")
        bin_high = row.get("bin_high")
        raw_q = row.get("raw_q")
        emos_q = row.get("emos_q")

        if raw_q is None:
            rows_skipped += 1
            per_city[city]["rows_skipped"] += 1
            continue
        if emos_q is None:
            rows_no_emos += 1

        outcome = 1.0 if _bin_contains(float(truth_rounded), bin_low, bin_high) else 0.0

        raw_brier = (float(raw_q) - outcome) ** 2
        per_city[city]["raw_brier"].append(raw_brier)
        agg["raw_brier"].append(raw_brier)

        if outcome == 1.0:
            per_city[city]["raw_q_win"].append(float(raw_q))
            agg["raw_q_win"].append(float(raw_q))

        if emos_q is not None:
            emos_brier = (float(emos_q) - outcome) ** 2
            per_city[city]["emos_brier"].append(emos_brier)
            agg["emos_brier"].append(emos_brier)
            if outcome == 1.0:
                per_city[city]["emos_q_win"].append(float(emos_q))
                agg["emos_q_win"].append(float(emos_q))
                per_city[city]["emos_logprob"].append(_safe_log(float(emos_q)))
                agg["emos_logprob"].append(_safe_log(float(emos_q)))

        if outcome == 1.0:
            per_city[city]["raw_logprob"].append(_safe_log(float(raw_q)))
            agg["raw_logprob"].append(_safe_log(float(raw_q)))

        rows_scored += 1
        per_city[city]["rows_scored"] += 1

    print(f"Rows scored:          {rows_scored}")
    print(f"Rows skipped (no truth): {rows_skipped}")
    print(f"Rows with raw_q only (emos_q=None, served=raw/missing): {rows_no_emos}")
    print()

    if rows_scored == 0:
        print("0 rows scored — cannot compute metrics yet.")
        return

    # 5. Aggregate metrics
    def _mean(lst):
        return sum(lst) / len(lst) if lst else None

    def _fmt(v):
        return f"{v:.6f}" if v is not None else "N/A"

    print("-" * 70)
    print("AGGREGATE METRICS  (raw vs emos, over all scored bins)")
    print("-" * 70)
    raw_brier_agg = _mean(agg["raw_brier"])
    emos_brier_agg = _mean(agg["emos_brier"]) if agg["emos_brier"] else None
    raw_log_agg = _mean(agg["raw_logprob"])
    emos_log_agg = _mean(agg["emos_logprob"]) if agg["emos_logprob"] else None
    raw_q_win_agg = _mean(agg["raw_q_win"])
    emos_q_win_agg = _mean(agg["emos_q_win"]) if agg["emos_q_win"] else None

    print(f"  Brier score  raw:  {_fmt(raw_brier_agg)}")
    print(f"  Brier score  emos: {_fmt(emos_brier_agg)}", end="")
    if raw_brier_agg is not None and emos_brier_agg is not None:
        delta = raw_brier_agg - emos_brier_agg
        print(f"   (improvement: {delta:+.6f})", end="")
    print()
    print(f"  Log-score    raw:  {_fmt(raw_log_agg)}")
    print(f"  Log-score    emos: {_fmt(emos_log_agg)}", end="")
    if raw_log_agg is not None and emos_log_agg is not None:
        print(f"   (improvement: {emos_log_agg - raw_log_agg:+.6f})", end="")
    print()
    print(f"  Mean q on winning bin  raw:  {_fmt(raw_q_win_agg)}")
    print(f"  Mean q on winning bin  emos: {_fmt(emos_q_win_agg)}")
    print()

    # 6. Per-city breakdown
    print("-" * 70)
    print("PER-CITY BREAKDOWN")
    print("-" * 70)
    print(f"  {'City':<20} {'Rows':>5} {'RawBrier':>10} {'EmosBrier':>10} {'Delta':>10}")
    print(f"  {'-'*20} {'-'*5} {'-'*10} {'-'*10} {'-'*10}")
    for city in sorted(per_city.keys()):
        d = per_city[city]
        n = d["rows_scored"]
        if n == 0:
            continue
        rb = _mean(d["raw_brier"])
        eb = _mean(d["emos_brier"]) if d["emos_brier"] else None
        delta_str = f"{rb - eb:+.6f}" if (rb is not None and eb is not None) else "N/A"
        print(f"  {city:<20} {n:>5} {_fmt(rb):>10} {_fmt(eb):>10} {delta_str:>10}")
    print()
    print("Done.")


if __name__ == "__main__":
    main()
