# Created: 2026-08-24
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 10. Runs the two-gate promotion evaluator
#   (src/analysis/promotion_gates.py) against live data.
"""Read-only two-gate capital-promotion evaluator report.

read_only_ro_uri: opens zeus-world.db via file:...?mode=ro&immutable=0 uri;
SELECT-only over settlement_attribution; the ONLY write this script ever
performs is a formal Gate-B ledger entry (state/promotion_gates_ledger.json,
atomic write) recorded when a formal (non---dry-run) Gate-B evaluation
completes — never a DB write, never a live decision.

ANALYTICS ONLY. Nothing here is imported by the live entry path (entries are
paused anyway per the reversal-plan status ledger); the Tier-1 sizing formula
this reports on is a pure function with no live caller.

Gate A reuses scripts/calibrator_walkforward_report.py's extraction +
walk-forward machinery (already tested; item 9) rather than re-deriving
r_hat here. Gate B's loader looks for a ``tier0_flagged`` marker column on
settlement_attribution; as of this commit no live writer populates it (Tier-0
admission, src/strategy/tier0_policy.py, is pure decision logic with zero DB
writes) — the loader returns an empty sample rather than guessing a proxy
predicate, and this script prints "no tier0-settled sample yet" cleanly.

--dry-run prints Gate A/B evidence WITHOUT recording a formal Gate-B
evaluation to the ledger. Repeated dashboard viewing of Gate B MUST use
--dry-run: a formal (non---dry-run) run is a one-shot alpha-spending event
per preregistration_version (see
docs/operations/current/plans/tier0_selection_lift_preregistration_2026-08-24.md,
"Frozen analysis choices" #3) and a second one is refused.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

from scripts.calibrator_walkforward_report import build_walk_forward_rows, load_rows as load_calibrator_rows
from scripts.scoreboard_panels import open_ro
from src.calibration.market_anchored_residual import walk_forward
from src.decision_kernel.canonicalization import stable_hash
from src.analysis.promotion_gates import (
    DEFAULT_LEDGER_PATH,
    GateARow,
    GateBRow,
    SecondFormalEvaluationRefused,
    SelectionLiftDecision,
    evaluate_gate_a,
    evaluate_gate_b,
    record_gate_b_formal_evaluation,
)

# The preregistration document's own version tag (its filename date). A
# future amendment would freeze a new file (per that document's own
# amendment rule) and this default would need to change with it — never
# silently reused across an amendment.
DEFAULT_PREREGISTRATION_VERSION = "tier0_selection_lift_preregistration_2026-08-24"

TIER0_MARKER_COLUMN = "tier0_flagged"


# ---------------------------------------------------------------------------
# Gate A loader — reuses the item-9 calibrator walk-forward machinery.
# ---------------------------------------------------------------------------


def load_gate_a_rows(conn: sqlite3.Connection) -> tuple[list[GateARow], dict[str, Any]]:
    raw_rows = load_calibrator_rows(conn)
    wf_rows, context, unparsable_lead = build_walk_forward_rows(raw_rows)
    y_by_row = {r["attribution_id"]: int(r["settled_in_bin"]) for r in raw_rows}
    result = walk_forward(wf_rows)

    gate_a_rows: list[GateARow] = []
    for pred in result.predictions:
        if pred.r_hat is None:
            continue
        ctx = context.get(pred.row_id)
        if ctx is None:
            continue
        gate_a_rows.append(
            GateARow(
                row_id=pred.row_id,
                p0=pred.p0,
                q_raw=pred.q_raw,
                r_hat=pred.r_hat,
                y=y_by_row[pred.row_id],
                city=ctx["city"],
                target_date=ctx["target_date"],
            )
        )
    coverage = {
        "n_settlement_attribution_usable": len(raw_rows),
        "n_walk_forward_excluded": result.n_excluded_total,
        "excluded_reasons": dict(result.excluded_reasons),
        "unparsable_lead": dict(unparsable_lead),
        "lambda_selected": result.lambda_selected,
    }
    return gate_a_rows, coverage


# ---------------------------------------------------------------------------
# Gate B loader — Tier-0-flagged settled positions.
# ---------------------------------------------------------------------------


def load_gate_b_rows(conn: sqlite3.Connection) -> tuple[list[GateBRow], dict[str, Any]]:
    """Tier-0-flagged settled positions for Gate B.

    Fails soft to an empty sample (never a guessed proxy predicate) when the
    ``tier0_flagged`` marker column is absent from settlement_attribution —
    true as of this commit, since Tier-0 admission has no DB writer yet.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(settlement_attribution)").fetchall()}
    coverage: dict[str, Any] = {
        "tier0_marker_column_present": TIER0_MARKER_COLUMN in cols,
        "n_tier0_flagged_rows": 0,
        "excluded_missing_avg_fill_price": 0,
        "excluded_missing_settled_in_bin": 0,
    }
    if TIER0_MARKER_COLUMN not in cols:
        return [], coverage

    rows = conn.execute(
        f"""
        SELECT attribution_id, city, target_date, avg_fill_price, settled_in_bin
        FROM settlement_attribution
        WHERE {TIER0_MARKER_COLUMN} = 1
        """
    ).fetchall()
    coverage["n_tier0_flagged_rows"] = len(rows)

    out: list[GateBRow] = []
    for r in rows:
        if r["avg_fill_price"] is None:
            coverage["excluded_missing_avg_fill_price"] += 1
            continue
        if r["settled_in_bin"] is None:
            coverage["excluded_missing_settled_in_bin"] += 1
            continue
        out.append(
            GateBRow(
                row_id=r["attribution_id"],
                p_fill=float(r["avg_fill_price"]),
                y=int(r["settled_in_bin"]),
                city=r["city"],
                target_date=r["target_date"],
            )
        )
    return out, coverage


def load_selection_lift_decision(conn: sqlite3.Connection) -> SelectionLiftDecision:
    """Dependency contract: src/analysis/selection_lift.py (built by a
    sibling implementer against the preregistration) is expected to expose
    ``decision_for_gate_b(conn) -> SelectionLiftDecision``-shaped evidence.
    Absent that module (not yet landed as of this commit), Gate B's
    component 2 fails closed — never treated as optimistically passed.
    """
    try:
        from src.analysis import selection_lift  # type: ignore[import-not-found]
    except ImportError:
        return SelectionLiftDecision(
            reached_positive_lcb_branch=False,
            n_qualifying_clusters=0,
            detail="src.analysis.selection_lift not available yet (sibling dependency) -- Gate B component 2 = NOT_REACHED",
        )
    loader = getattr(selection_lift, "decision_for_gate_b", None)
    if loader is None:
        return SelectionLiftDecision(
            reached_positive_lcb_branch=False,
            n_qualifying_clusters=0,
            detail="src.analysis.selection_lift present but has no decision_for_gate_b(conn) -- Gate B component 2 = NOT_REACHED",
        )
    return loader(conn)


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------


def _fmt(v: Any, digits: int = 5) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def render_gate_a_report(result, coverage: dict[str, Any]) -> str:
    lines = [
        "## GATE A -- probability use (non-inferiority of r_hat vs p0)",
        f"delta_a={result.delta_a} catastrophic_margin={result.catastrophic_margin} "
        f"min_clusters_for_bucket_check={result.min_clusters_for_bucket_check}",
        f"coverage: n_settlement_attribution_usable={coverage['n_settlement_attribution_usable']} "
        f"n_walk_forward_excluded={coverage['n_walk_forward_excluded']} "
        f"excluded_reasons={coverage['excluded_reasons']} lambda_selected={coverage['lambda_selected']}",
        "",
        f"n={result.n} clusters(city-date)={result.n_clusters_city_date} clusters(date)={result.n_clusters_date}",
        f"mean(d)={_fmt(result.mean_d)} se(city-date)={_fmt(result.se_city_date)} "
        f"se(date)={_fmt(result.se_date)} se_gate={_fmt(result.se_gate)}",
        f"upper_bound_pooled={_fmt(result.upper_bound_pooled)} non_inferiority_pass={result.non_inferiority_pass}",
        "",
        "| bucket | n | clusters(city-date) | checked | mean(d) | upper_bound | breached |",
        "|---|---|---|---|---|---|---|",
    ]
    for bc in result.bucket_checks:
        lines.append(
            f"| {bc.bucket} | {bc.n} | {bc.n_clusters_city_date} | {bc.checked} | "
            f"{_fmt(bc.mean_d)} | {_fmt(bc.upper_bound)} | {bc.breached} |"
        )
    lines.append("")
    lines.append(f"VERDICT: {result.verdict.value}")
    return "\n".join(lines)


def render_gate_b_report(result, coverage: dict[str, Any], lift: SelectionLiftDecision) -> str:
    lines = [
        "## GATE B -- capital use",
        f"coverage: tier0_marker_column_present={coverage['tier0_marker_column_present']} "
        f"n_tier0_flagged_rows={coverage['n_tier0_flagged_rows']} "
        f"excluded_missing_avg_fill_price={coverage['excluded_missing_avg_fill_price']} "
        f"excluded_missing_settled_in_bin={coverage['excluded_missing_settled_in_bin']}",
        "",
        f"n={result.n} clusters(city-date)={result.n_clusters_city_date} clusters(date)={result.n_clusters_date}",
        f"mean(y-p_fill)={_fmt(result.mean_residual)} se(city-date)={_fmt(result.se_city_date)} "
        f"se(date)={_fmt(result.se_date)} se_gate={_fmt(result.se_gate)}",
        f"lower_bound={_fmt(result.lower_bound)} fill_residual_pass={result.fill_residual_pass}",
        f"selection_lift_pass={result.selection_lift_pass} detail={lift.detail} "
        f"n_qualifying_clusters={lift.n_qualifying_clusters}",
        "",
        f"VERDICT: {result.verdict.value}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Repo root (DB/ledger paths are relative to this).")
    parser.add_argument("--world", default="state/zeus-world.db", help="World DB path (relative to --root).")
    parser.add_argument("--gate", choices=["A", "B", "ALL"], default="ALL")
    parser.add_argument(
        "--preregistration-version",
        default=DEFAULT_PREREGISTRATION_VERSION,
        help="Alpha-spending unit for the Gate-B ledger (the frozen preregistration doc's own version tag).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evidence only -- never records a formal Gate-B evaluation. Repeated dashboard viewing MUST use this flag.",
    )
    parser.add_argument(
        "--ledger-path",
        default=None,
        help="Override the Gate-B ledger file path (tests only; default is state/promotion_gates_ledger.json under --root).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    root = Path(args.root)
    world_path = root / args.world
    ledger_path = Path(args.ledger_path) if args.ledger_path else root / DEFAULT_LEDGER_PATH

    conn = open_ro(world_path)
    try:
        output_lines: list[str] = []

        if args.gate in ("A", "ALL"):
            gate_a_rows, coverage_a = load_gate_a_rows(conn)
            result_a = evaluate_gate_a(gate_a_rows)
            output_lines.append(render_gate_a_report(result_a, coverage_a))
            output_lines.append("")

        if args.gate in ("B", "ALL"):
            gate_b_rows, coverage_b = load_gate_b_rows(conn)
            if not gate_b_rows:
                output_lines.append("## GATE B -- capital use")
                output_lines.append("no tier0-settled sample yet")
                output_lines.append(f"coverage: {coverage_b}")
            else:
                lift = load_selection_lift_decision(conn)
                result_b = evaluate_gate_b(gate_b_rows, selection_lift=lift)
                output_lines.append(render_gate_b_report(result_b, coverage_b, lift))
                if args.dry_run:
                    output_lines.append("(--dry-run: evidence only, no ledger write)")
                else:
                    sample_hash = stable_hash(sorted(r.row_id for r in gate_b_rows))
                    try:
                        entry = record_gate_b_formal_evaluation(
                            preregistration_version=args.preregistration_version,
                            sample_identity_hash=sample_hash,
                            verdict=result_b.verdict.value,
                            path=ledger_path,
                        )
                        output_lines.append(
                            f"ledger: recorded formal evaluation at {entry.evaluated_at} "
                            f"(sample_identity_hash={entry.sample_identity_hash})"
                        )
                    except SecondFormalEvaluationRefused as exc:
                        output_lines.append(f"ledger: REFUSED -- {exc}")

        output_lines.append("")
        output_lines.append(
            f"tier1_sizing_fraction reference: f=min({0.0025}, 0.25*max(0,(r_L-p_fill)/(1-p_fill))) "
            "-- pure function, NOT wired into the live entry path; see "
            "src.analysis.promotion_gates.tier1_sizing_fraction"
        )
        print("\n".join(output_lines))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
