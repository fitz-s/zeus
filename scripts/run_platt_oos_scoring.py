#!/usr/bin/env python3
# Created: 2026-05-30
# Last reused or audited: 2026-05-30 (writer-lock allowlist fix: raw sqlite3.connect →
#   get_forecasts_connection_read_only so pytest collection is not blocked by the antibody).
# Authority basis: STAT_WAVE_REPORT_AND_PLATT_TASK_SPEC_2026-05-29.md Part 2 §2.3/§2.4/§2.5 P5/§2.6.
#   Frozen p_raw_domain_hash = deabf8f64bde27b7 (from src.calibration.ens_error_model.current_gate_set_hash()).
#   Score-only: no live calibration mutation, no PROMOTE writes.
#   Candidate generation: global fit on calibration_pairs (p_raw scalar → outcome) using
#   ExtendedPlattCalibrator, yielding unclamped + clamped(2.0) + shrinkage(A=0.5) candidates.
#   Scoring: full-chain OOS via score_platt_candidates.run_platt_scoring, which uses
#   members_json + SettlementSemantics to reconstruct p_raw vector per decision group.
#   Decision: PROMOTE/IDENTITY/INSUFFICIENT_N per gate (>=2/3 proper scores + LCB>0 + BH-FDR +
#   no catastrophe + slope fuse). Flagged PROMOTE candidates are for operator review only.
"""Run identity-vs-Platt OOS scoring for the 8 HIGH cities.

Score-only: reads state/zeus-forecasts.db (read-only), writes nothing to any DB.
Output: JSON + summary table to stdout and /tmp/platt_oos_results.json.

Usage:
    python scripts/run_platt_oos_scoring.py [--city CITY] [--limit N]
    python scripts/run_platt_oos_scoring.py --city "Hong Kong"

Output:
    /tmp/platt_oos_results.json   — full per-city JSON
    stdout                        — summary table
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional

os.environ.setdefault("ZEUS_MODE", "paper")

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import numpy as np

from src.calibration.ens_error_model import current_gate_set_hash
from src.calibration.platt import ExtendedPlattCalibrator
from src.calibration.platt_oos_resolver import PlattCandidate, make_clamped_candidate
from src.config import cities_by_name
from src.state.db import get_forecasts_connection, get_forecasts_connection_read_only

# score_platt_candidates lives in scripts/ (same dir as this file)
from score_platt_candidates import run_platt_scoring  # noqa: E402

P_RAW_DOMAIN_HASH = current_gate_set_hash()
assert P_RAW_DOMAIN_HASH == "deabf8f64bde27b7", (
    f"Domain hash changed: {P_RAW_DOMAIN_HASH!r} != 'deabf8f64bde27b7'. "
    "Update the assertion or re-verify the frozen hash."
)

# The 8 HIGH cities — canonical DB names (settlement_unit C or F per city object).
HIGH_CITIES: list[str] = [
    "Hong Kong", "London", "Miami", "NYC", "Paris", "Seoul", "Shanghai", "Tokyo"
]

# Minimum decision groups for a meaningful OOS run.
MIN_OOS_DECISION_GROUPS = 5


# ---------------------------------------------------------------------------
# Data fetch: one row per settlement event (decision_group) with members_json
# ---------------------------------------------------------------------------

def _fetch_scoring_rows(city_name: str, *, conn: sqlite3.Connection, limit: Optional[int] = None) -> list[dict]:
    """Fetch one row per settlement event for OOS scoring.

    Each row carries:
      - decision_group_id  : fold-block key
      - target_date        : date string (fallback fold key)
      - settlement_value_c : settled value in the city's native unit (C or F)
      - members_json       : JSON array of ensemble member maxes (native unit)
      - members_unit       : city.settlement_unit (injected from City object)
      - lead_days          : forecast lead from the first matching calibration_pair row
    """
    city = cities_by_name.get(city_name)
    if city is None:
        print(f"[WARN] City not found in cities_by_name: {city_name!r}", file=sys.stderr)
        return []

    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    rows = conn.execute(f"""
        SELECT
            cp.decision_group_id,
            cp.target_date,
            cp.settlement_value   AS settlement_value_c,
            cp.lead_days,
            es.members_json
        FROM calibration_pairs cp
        JOIN ensemble_snapshots es ON cp.snapshot_id = es.snapshot_id
        WHERE cp.city = ?
          AND cp.temperature_metric = 'high'
          AND cp.training_allowed = 1
          AND cp.authority = 'VERIFIED'
          AND es.members_json IS NOT NULL
          AND cp.decision_group_id IS NOT NULL
          AND cp.decision_group_id != ''
        GROUP BY cp.decision_group_id
        ORDER BY cp.target_date DESC
        {limit_clause}
    """, (city_name,)).fetchall()

    result = []
    for row in rows:
        try:
            # Validate members_json is parseable
            members = json.loads(row[4])
            if not members:
                continue
            result.append({
                "decision_group_id": str(row[0]),
                "target_date": str(row[1]),
                "settlement_value_c": float(row[2]),
                "lead_days": float(row[3]),
                "members_json": row[4],
                "members_unit": city.settlement_unit,
            })
        except Exception:
            continue
    return result


# ---------------------------------------------------------------------------
# Candidate generation: global fit on calibration_pairs scalar p_raw / outcome
# ---------------------------------------------------------------------------

def _fetch_fit_pairs(city_name: str, *, conn: sqlite3.Connection) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Fetch (p_raw, lead_days, outcomes) scalars for global Platt fit.

    One row per calibration_pair (one per bin per settlement event). This is
    the same data refit_platt.py uses to fit the production Platt model.
    """
    rows = conn.execute("""
        SELECT p_raw, lead_days, outcome
        FROM calibration_pairs
        WHERE city = ?
          AND temperature_metric = 'high'
          AND training_allowed = 1
          AND authority = 'VERIFIED'
          AND p_raw IS NOT NULL
          AND decision_group_id IS NOT NULL
          AND decision_group_id != ''
    """, (city_name,)).fetchall()

    if not rows:
        return np.array([]), np.array([]), np.array([])

    p_raw = np.array([r[0] for r in rows], dtype=float)
    lead_days = np.array([r[1] for r in rows], dtype=float)
    outcomes = np.array([r[2] for r in rows], dtype=float)
    return p_raw, lead_days, outcomes


def _build_candidates(
    city_name: str,
    *,
    conn: sqlite3.Connection,
) -> tuple[list[PlattCandidate], dict]:
    """Fit Platt on all pairs globally; return candidate set + fit metadata.

    Candidates produced:
      1. fit_raw       — unclamped fit (A, B, C) as-is
      2. fit_clamped   — same fit but A clamped to A_MAX_FUSE=2.0
      3. shrinkage_0p5 — fixed A=0.5 (shrunken slope toward null), B/C from fit
    """
    p_raw, lead_days, outcomes = _fetch_fit_pairs(city_name, conn=conn)
    if len(p_raw) == 0:
        return [], {"n_pairs": 0, "fit_ok": False}

    try:
        cal = ExtendedPlattCalibrator()
        cal.fit(p_raw, lead_days, outcomes)
        A, B, C = float(cal.A), float(cal.B), float(cal.C)
    except Exception as exc:
        print(f"[WARN] {city_name}: Platt global fit failed: {exc}", file=sys.stderr)
        return [], {"n_pairs": len(p_raw), "fit_ok": False, "error": str(exc)}

    meta = {
        "n_pairs": len(p_raw),
        "fit_ok": True,
        "A": A,
        "B": B,
        "C": C,
    }

    raw_candidate = PlattCandidate(name="fit_raw", A=A, B=B, C=C)
    clamped = make_clamped_candidate(raw_candidate, cap=2.0)
    shrinkage = PlattCandidate(name="shrinkage_A0p5", A=0.5, B=B, C=C)

    return [raw_candidate, clamped, shrinkage], meta


# ---------------------------------------------------------------------------
# Per-city scoring
# ---------------------------------------------------------------------------

def score_city(
    city_name: str,
    *,
    conn: sqlite3.Connection,
    limit: Optional[int] = None,
) -> dict:
    """Run full-chain OOS scoring for one city. Score-only — no DB writes."""
    city = cities_by_name.get(city_name)
    if city is None:
        return {
            "city": city_name,
            "decision": "ERROR",
            "reason": f"city not found in cities_by_name",
            "n_scoring_rows": 0,
        }

    # Fetch scoring rows (decision-group level with members_json)
    scoring_rows = _fetch_scoring_rows(city_name, conn=conn, limit=limit)
    n_scoring = len(scoring_rows)

    # Count unique decision groups for maturity check
    n_groups = len({r["decision_group_id"] for r in scoring_rows})

    if n_scoring < MIN_OOS_DECISION_GROUPS or n_groups < MIN_OOS_DECISION_GROUPS:
        return {
            "city": city_name,
            "decision": "INSUFFICIENT_N",
            "reason": f"Only {n_groups} decision groups (min {MIN_OOS_DECISION_GROUPS}); n_scoring_rows={n_scoring}",
            "n_scoring_rows": n_scoring,
            "n_decision_groups": n_groups,
        }

    print(f"[INFO] {city_name}: {n_scoring} scoring rows, {n_groups} decision groups", file=sys.stderr)

    # Build candidates (global fit)
    candidates, fit_meta = _build_candidates(city_name, conn=conn)
    if not fit_meta.get("fit_ok"):
        return {
            "city": city_name,
            "decision": "ERROR",
            "reason": f"global fit failed: {fit_meta.get('error', 'no pairs')}",
            "n_scoring_rows": n_scoring,
            "fit_meta": fit_meta,
        }

    print(
        f"[INFO] {city_name}: fit A={fit_meta['A']:+.3f} B={fit_meta['B']:+.3f} "
        f"C={fit_meta['C']:+.3f} n_pairs={fit_meta['n_pairs']}",
        file=sys.stderr,
    )

    try:
        result = run_platt_scoring(
            scoring_rows,
            city,
            target_product="mx2t3",
            candidates=candidates,
            k_folds=3,
            p_raw_domain_hash=P_RAW_DOMAIN_HASH,
            override_fuse=False,
        )
        result["city"] = city_name
        result["n_scoring_rows"] = n_scoring
        result["n_decision_groups"] = n_groups
        result["fit_meta"] = fit_meta
        return result
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        print(f"[ERROR] {city_name}: scoring failed: {exc}\n{tb}", file=sys.stderr)
        return {
            "city": city_name,
            "decision": "ERROR",
            "reason": str(exc),
            "n_scoring_rows": n_scoring,
            "fit_meta": fit_meta,
        }


# ---------------------------------------------------------------------------
# Summary table printer
# ---------------------------------------------------------------------------

def _fmt_score(v) -> str:
    if v is None:
        return "  N/A  "
    try:
        return f"{float(v):+.4f}"
    except Exception:
        return str(v)


def print_summary_table(results: dict[str, dict]) -> None:
    """Print a human-readable decision table to stdout."""
    header = (
        f"{'City':<14} {'N_grps':>7} {'Decision':<14} "
        f"{'id_ll':>8} {'cand_ll':>8} {'id_rps':>8} {'cand_rps':>8} "
        f"{'id_br':>8} {'cand_br':>8} {'LCB':>8} "
        f"{'beats':>6} {'FitA':>7}"
    )
    print()
    print("=" * len(header))
    print("PLATT OOS SCORING RESULTS  (p_raw_domain_hash=deabf8f64bde27b7)")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    for city_name in HIGH_CITIES:
        r = results.get(city_name)
        if r is None:
            print(f"{city_name:<14}  (no result)")
            continue

        decision = r.get("decision", "?")
        n_grps = r.get("n_decision_groups", r.get("n_scoring_rows", "?"))
        chosen = r.get("chosen") or "identity"
        fit_A = r.get("fit_meta", {}).get("A")
        fit_A_str = f"{fit_A:+.3f}" if fit_A is not None else "  N/A"

        id_metrics = r.get("identity_metrics") or {}
        cand_metrics = (r.get("candidate_metrics") or {}).get(chosen) or {}
        lcb = (r.get("improvement_lcb") or {}).get(chosen)
        beats = (r.get("beats_identity_count") or {}).get(chosen)

        print(
            f"{city_name:<14} {str(n_grps):>7} {decision:<14} "
            f"{_fmt_score(id_metrics.get('logloss')):>8} "
            f"{_fmt_score(cand_metrics.get('logloss')):>8} "
            f"{_fmt_score(id_metrics.get('rps')):>8} "
            f"{_fmt_score(cand_metrics.get('rps')):>8} "
            f"{_fmt_score(id_metrics.get('brier')):>8} "
            f"{_fmt_score(cand_metrics.get('brier')):>8} "
            f"{_fmt_score(lcb):>8} "
            f"{str(beats) if beats is not None else 'N/A':>6} "
            f"{fit_A_str:>7}"
        )

    print("-" * len(header))
    print()

    # Flag any genuine PROMOTE candidates
    promote_cities = [c for c, r in results.items() if (r or {}).get("decision") == "PROMOTE"]
    if promote_cities:
        print("*** FLAGGED FOR OPERATOR REVIEW (PROMOTE) ***")
        for c in promote_cities:
            r = results[c]
            chosen = r.get("chosen")
            lcb = (r.get("improvement_lcb") or {}).get(chosen)
            beats = (r.get("beats_identity_count") or {}).get(chosen)
            print(
                f"  {c}: candidate={chosen!r} beats={beats}/3 "
                f"LCB={lcb:+.4f} | {r.get('reason', '')}"
            )
        print()
    else:
        print("No PROMOTE candidates. Default: identity for all cities.")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run identity-vs-Platt OOS scoring for the 8 HIGH cities. "
            "Score-only — reads zeus-forecasts.db, writes nothing to any DB."
        )
    )
    parser.add_argument(
        "--city",
        default=None,
        help="Score only one city (default: all 8 HIGH cities).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of scoring rows per city (for testing; default: no limit).",
    )
    parser.add_argument(
        "--out",
        default="/tmp/platt_oos_results.json",
        help="Output JSON file path (default: /tmp/platt_oos_results.json).",
    )
    args = parser.parse_args()

    cities = [args.city] if args.city else HIGH_CITIES
    unknown = [c for c in cities if c not in HIGH_CITIES]
    if unknown:
        print(f"ERROR: unknown city {unknown!r}; valid: {HIGH_CITIES}", file=sys.stderr)
        return 1

    print(f"[INFO] p_raw_domain_hash: {P_RAW_DOMAIN_HASH}", file=sys.stderr)
    print(f"[INFO] Cities: {cities}", file=sys.stderr)
    print(f"[INFO] Row limit per city: {args.limit or 'none'}", file=sys.stderr)

    # Read-only connection (forecasts DB holds calibration_pairs + ensemble_snapshots).
    # Canonical connection helper (write_class=None → read-only) so this script
    # stays inside the writer-lock allowlist and does not block pytest collection.
    conn = get_forecasts_connection_read_only()
    conn.row_factory = sqlite3.Row

    results: dict[str, dict] = {}
    try:
        for city_name in cities:
            print(f"\n[SCORING] {city_name} ...", file=sys.stderr)
            result = score_city(city_name, conn=conn, limit=args.limit)
            results[city_name] = result
            print(
                f"[RESULT] {city_name}: decision={result.get('decision')} "
                f"chosen={result.get('chosen')!r}",
                file=sys.stderr,
            )
    finally:
        conn.close()

    # Write JSON output (safer than reading stdout from long run)
    out_path = Path(args.out)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"[INFO] Full results written to {out_path}", file=sys.stderr)

    # Print summary table
    print_summary_table(results)

    return 0


if __name__ == "__main__":
    sys.exit(main())
