# Created: 2026-08-24
# Last reused or audited: 2026-08-24
# Authority basis: docs/operations/current/plans/tier0_selection_lift_preregistration_2026-08-24.md
#   (FROZEN) — reversal_plan_tier0_2026-08-24.md item 7.
"""Read-only Tier-0 ordinal selection-lift report (preregistered test).

ANALYTICS ONLY. Opens state/zeus_trades.db strictly read-only (sqlite3 URI
``mode=ro&immutable=0``, matching scripts/scoreboard_panels.py's open_ro).
Never writes to any DB, never authorizes a live decision.

INTERFACE CONTRACT — Item 3 (decision certificate candidate-set provenance,
reversal_plan_tier0_2026-08-24.md) landed the table below on the TRADE DB
(state/zeus_trades.db), not world.db: its sole writer,
src.engine.global_batch_runtime._persist_tier0_candidate_set, shares the
exact trade connection/transaction as the existing global auction receipt
write (K1/INV-37 single-DB write). The ``--world`` flag below predates that
landing (written when the table's eventual home was still undecided) and
keeps its name for CLI/test compatibility, but its DEFAULT now points at the
trade DB where the data actually lives; pass a different path explicitly if
you need to point elsewhere.

  Table ``tier0_candidate_set_provenance`` — schema:
  src/state/schema/tier0_candidate_set_provenance_schema.py — one row per
  considered candidate:

    city_date_group_id TEXT  -- groups rows into one opportunity set (one
                                 live Tier-0 auction decision)
    city TEXT, target_date TEXT
    candidate_id TEXT, side TEXT, p0 REAL, lead_bucket TEXT,
    eligible INTEGER (0/1), selected INTEGER (0/1),
    market_key TEXT   -- family/market identity for duplicate/complement
                          collapse; see src/analysis/selection_lift.py
    settled_y INTEGER (0/1) or NULL  -- side settlement; NULL if unsettled

  Closest existing real plumbing (found during discovery, NOT used here):
  decision_certificates(certificate_type='ActionableTradeCertificate') has a
  semantic_key already keyed by (event_id, candidate_id) — src/decision_
  kernel/certificates/action.py — which is a promising home for Item 3.
  Its current payload (verified against src/decision_kernel/verifier.py
  2026-08-24) carries no p0/eligible/selected/lead_bucket/market_key field,
  so parsing it here would be guessing at an unlanded shape rather than
  reading a documented contract, and is deliberately not done.

If ``tier0_candidate_set_provenance`` does not exist, this script prints
"provenance table absent — 0 observations" and exits 0 (no crash — the
table's absence is the expected, correct state of the world today).

Evaluation lock (frozen doc, "Stopping rule"): a p-value is NEVER printed
below 100 qualifying observations. The only exception is --pilot-power-check,
which prints ONLY the cluster (city-date) variance of the first 30 observations
— report-only, per the frozen doc's explicit power-check clause; it is never a
stopping trigger and never discloses p or a decision verdict.
"""
from __future__ import annotations

import argparse
import statistics
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts.scoreboard_panels import open_ro
from src.analysis.selection_lift import (
    Candidate,
    OpportunitySet,
    STOPPING_COUNT,
    build_observations,
    city_date_bootstrap_ci,
    date_block_sensitivity,
    evaluation_is_locked,
    governing_ci,
    permutation_test,
)

# Contract table name Item 3 is expected to create. See module docstring.
CANDIDATE_SET_TABLE = "tier0_candidate_set_provenance"

# Frozen seed for report reproducibility (the module requires an explicit
# seed on every call; this is the CLI's one fixed choice, never wall-clock).
# --seed overrides it for research/ad-hoc runs; the CANONICAL evaluation run
# for the preregistered stopping-count report must use the default.
DEFAULT_SEED = 20260824

_PILOT_POWER_CHECK_N = 30


# ---------------------------------------------------------------------------
# Loading (interface contract; see module docstring).
# ---------------------------------------------------------------------------


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def load_opportunity_sets(
    conn: sqlite3.Connection, *, table_name: str = CANDIDATE_SET_TABLE
) -> tuple[list[OpportunitySet], dict[str, int]]:
    """Load opportunity sets per the documented interface contract.

    Returns ([], {"provenance_table_absent": 1}) when ``table_name`` does
    not exist — the caller prints the clean fallback message and exits 0.
    """
    coverage: dict[str, int] = defaultdict(int)
    if not _table_exists(conn, table_name):
        coverage["provenance_table_absent"] = 1
        return [], dict(coverage)

    rows = conn.execute(
        f"""
        SELECT city_date_group_id, city, target_date, candidate_id, side, p0,
               lead_bucket, eligible, selected, market_key, settled_y
        FROM {table_name}
        """
    ).fetchall()

    groups: dict[str, list[Candidate]] = defaultdict(list)
    group_city_date: dict[str, tuple[str, str]] = {}
    for r in rows:
        gid = r["city_date_group_id"]
        group_city_date[gid] = (r["city"], r["target_date"])
        groups[gid].append(
            Candidate(
                id=r["candidate_id"],
                side=r["side"],
                p0=r["p0"],
                lead_bucket=r["lead_bucket"],
                eligible=bool(r["eligible"]),
                selected=bool(r["selected"]),
                y=(None if r["settled_y"] is None else int(r["settled_y"])),
                market_key=r["market_key"],
            )
        )

    opp_sets = [
        OpportunitySet(city=group_city_date[gid][0], date=group_city_date[gid][1], candidates=tuple(cands))
        for gid, cands in sorted(groups.items())
    ]
    return opp_sets, dict(coverage)


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------


def _fmt(v: Any, digits: int = 4) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def render_report(
    *,
    observations,
    coverage: dict[str, int],
    n_perm: int,
    n_boot: int,
    seed: int,
    pilot_power_check: bool,
) -> str:
    lines: list[str] = ["# Tier-0 ordinal selection-lift report (preregistered test)", ""]
    n = len(observations)
    lines.append(f"n observations = {n}")
    lines.append(f"coverage: {dict(sorted(coverage.items()))}")
    lines.append("")

    if pilot_power_check:
        sample = observations[:_PILOT_POWER_CHECK_N]
        lines.append(
            "## Pilot power check (report-only; frozen doc §power-check clause — NEVER a stopping trigger)"
        )
        if len(sample) < 2:
            lines.append(f"n={len(sample)} insufficient for a variance estimate (need >=2)")
        else:
            var_l = statistics.variance(o.L for o in sample)
            lines.append(f"n={len(sample)} cluster (city-date) variance of L = {_fmt(var_l, 6)}")
        return "\n".join(lines)

    if evaluation_is_locked(n):
        lines.append(
            f"accruing — evaluation locked until {STOPPING_COUNT} observations "
            f"(n={n}); no p-value printed (frozen doc §Stopping rule). "
            "Pass --pilot-power-check for a report-only variance check."
        )
        return "\n".join(lines)

    perm = permutation_test(observations, n_perm=n_perm, seed=seed)
    cd_ci = city_date_bootstrap_ci(observations, n_boot=n_boot, seed=seed)
    db_ci = date_block_sensitivity(observations, n_boot=n_boot, seed=seed)
    governing = governing_ci(cd_ci, db_ci)

    lines.append(f"mean(L) = {_fmt(perm.observed_statistic)}")
    lines.append(f"permutation p (two-sided, n_perm={n_perm}, seed={seed}) = {_fmt(perm.p_value, 4)}")
    lines.append(
        f"city-date CI (95%, n_boot={n_boot}, seed={seed}): "
        f"[{_fmt(cd_ci.lower)}, {_fmt(cd_ci.upper)}] (n_blocks={cd_ci.n_blocks})"
    )
    lines.append(
        f"date-block CI (95%, n_boot={n_boot}, seed={seed}): "
        f"[{_fmt(db_ci.lower)}, {_fmt(db_ci.upper)}] (n_blocks={db_ci.n_blocks})"
    )
    governing_label = "date-block" if governing is db_ci else "city-date"
    lines.append(
        f"GOVERNING (larger) uncertainty: {governing_label} "
        f"[{_fmt(governing.lower)}, {_fmt(governing.upper)}]"
    )
    lines.append("")

    lines.append("## Decision rule (frozen doc, verbatim adoption)")
    if governing.lower > 0:
        lines.append(
            "point estimate positive, LOWER bound of the governing 95% CI is positive "
            "-> ordinal selection is ELIGIBLE for the Gate-B capital-use evaluation "
            "(not sufficient alone)."
        )
    elif governing.point_estimate > 0:
        lines.append(
            "point estimate positive, governing interval crosses zero -> remain Tier 0, "
            "keep accruing."
        )
    else:
        lines.append(
            "point estimate <= 0 at the stopping count -> the q-based ordinal selector is "
            "retired from Tier-0 admission; replace with the simplest market-only comparator "
            "(cheapest eligible claim per cluster) and re-preregister."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Repo root (DB path is relative to this).")
    parser.add_argument(
        "--world",
        default="state/zeus_trades.db",
        help=(
            "DB path holding tier0_candidate_set_provenance, relative to --root "
            "(flag name kept for compatibility; the table lives on the trade DB "
            "per K1/INV-37, not world.db -- see module docstring)."
        ),
    )
    parser.add_argument("--n-perm", type=int, default=10000, help="Permutation count (default 10000).")
    parser.add_argument("--n-boot", type=int, default=10000, help="Bootstrap draw count (default 10000).")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"RNG seed (default {DEFAULT_SEED}).")
    parser.add_argument(
        "--pilot-power-check",
        action="store_true",
        help="Print ONLY the cluster variance of the first 30 observations (report-only; never a stopping trigger).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    root = Path(args.root)
    world_path = root / args.world

    conn = open_ro(world_path)
    try:
        opp_sets, load_coverage = load_opportunity_sets(conn)
    finally:
        conn.close()

    if load_coverage.get("provenance_table_absent"):
        print("provenance table absent — 0 observations")
        return 0

    result = build_observations(opp_sets)
    coverage = dict(load_coverage)
    coverage.update(result.coverage)

    print(
        render_report(
            observations=result.observations,
            coverage=coverage,
            n_perm=args.n_perm,
            n_boot=args.n_boot,
            seed=args.seed,
            pilot_power_check=args.pilot_power_check,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
