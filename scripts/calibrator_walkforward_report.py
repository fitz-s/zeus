# Created: 2026-08-24
# Last reused or audited: 2026-08-24
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 9 ("Market-anchored walk-forward calibrator"). Runs the calibrator in
#   src/calibration/market_anchored_residual.py over settlement_attribution and
#   reports paired proper scores of market p0 / raw q / calibrated r_hat.
"""Read-only walk-forward report for the market-anchored residual calibrator.

read_only_ro_uri: opens zeus-world.db via file:...?mode=ro&immutable=0 uri;
SELECT-only over settlement_attribution; never writes to any DB; prints a
markdown report to stdout.

ANALYTICS ONLY. Nothing here is imported by the live entry path (entries are
paused anyway per the reversal-plan status ledger) — this script exists to
answer whether q_raw carries residual information beyond the market price
(beta meaningfully positive) or whether the calibrator collapses to the
market price (beta near 0, per the plan's "parity with market never unlocks
Kelly" law).

p0 caveat (documented per item 9's explicit requirement): p0 here is
settlement_attribution.market_in_bin_prob, which is derived from OUR OWN
fill price, not an independently observed top-of-book quote. It is a proxy
for decision-time market price until item 3 (decision certificate: explicit
p0 provenance) lands. Every number in this report inherits that proxy's
noise.

Decision-date source: decision_posterior_computed_at, NOT an entry command's
created_at. Investigated against the live zeus-world.db (2026-08-24): of the
653 settlement_attribution rows usable for P1 (q_in_bin, market_in_bin_prob,
settled_in_bin all NOT NULL), 613 (93.9%) have decision_posterior_computed_at
populated; a LEFT JOIN to venue_commands.position_id for the same 653 rows
returned ZERO matches — those position_ids simply are not present in this
DB's venue_commands table (many are pre-item-4 "edli*" legacy escrow
positions with no venue_commands row at all in zeus-world.db). An
entry-command-created_at fallback is therefore not viable without a
cross-DB join to zeus_trades.db, which the K1 DB split forbids for this
read-only analytics path. Rows without decision_posterior_computed_at are
excluded from the walk-forward and counted in coverage, never guessed.
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from scripts.scoreboard_panels import clip, clustered_se, cluster_key, month_of, open_ro, q_p_bucket
from src.calibration.market_anchored_residual import (
    LAMBDA_GRID,
    WalkForwardRow,
    lead_bucket_of,
    walk_forward,
)

CHALLENGER_NOTE = (
    "challenger unavailable — artifact never fitted (predeclared #451 sigma "
    "adjustment is out of scope for this pass per plan item 9)"
)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def logloss(y: int, p: float) -> float:
    p_c = clip(p)
    return -(y * math.log(p_c) + (1 - y) * math.log(1.0 - p_c))


# ---------------------------------------------------------------------------
# Extraction.
# ---------------------------------------------------------------------------


def load_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """settlement_attribution rows with the four fields item 9 requires NOT NULL."""
    rows = conn.execute(
        """
        SELECT attribution_id, city, target_date, q_in_bin, market_in_bin_prob,
               settled_in_bin, direction, decision_posterior_computed_at,
               settled_at, graded_at
        FROM settlement_attribution
        WHERE q_in_bin IS NOT NULL
          AND market_in_bin_prob IS NOT NULL
          AND settled_in_bin IS NOT NULL
          AND direction IS NOT NULL
        """
    ).fetchall()
    return [dict(r) for r in rows]


def build_walk_forward_rows(
    raw_rows: list[dict[str, Any]],
) -> tuple[list[WalkForwardRow], dict[str, dict[str, Any]], dict[str, int]]:
    """Build WalkForwardRow inputs + a row_id -> display-context lookup.

    A row missing decision_posterior_computed_at or settled_at is still
    passed into walk_forward() (as decision_at/settled_at=None) so its
    exclusion is counted there, rather than double-counted here.
    """
    wf_rows: list[WalkForwardRow] = []
    context: dict[str, dict[str, Any]] = {}
    unparsable_lead: dict[str, int] = {"unparsable_target_date": 0, "lead_not_modeled": 0}
    for r in raw_rows:
        row_id = r["attribution_id"]
        decision_at = _parse_ts(r["decision_posterior_computed_at"])
        settled_at = _parse_ts(r["settled_at"]) or _parse_ts(r["graded_at"])
        target_date = _parse_date(r["target_date"])
        lead_bucket = None
        if decision_at is not None and target_date is not None:
            lead_bucket = lead_bucket_of(decision_at.date(), target_date)
            if lead_bucket is None:
                unparsable_lead["lead_not_modeled"] += 1
        elif decision_at is not None:
            unparsable_lead["unparsable_target_date"] += 1

        wf_rows.append(
            WalkForwardRow(
                row_id=row_id,
                p0=r["market_in_bin_prob"],
                q_raw=r["q_in_bin"],
                lead_bucket=lead_bucket,
                y=r["settled_in_bin"],
                decision_at=decision_at,
                settled_at=settled_at,
            )
        )
        context[row_id] = {
            "city": r["city"],
            "target_date": r["target_date"],
            "month": month_of(r["settled_at"] or r["graded_at"]),
            "bucket": q_p_bucket(abs(float(r["q_in_bin"]) - float(r["market_in_bin_prob"]))),
        }
    return wf_rows, context, unparsable_lead


# ---------------------------------------------------------------------------
# Paired scoring.
# ---------------------------------------------------------------------------


def compute_paired_report(
    predictions, context: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Paired (p0, q, r_hat) log-loss, grouped by month and by |q-p| bucket.

    Only rows where the calibrator produced r_hat (not excluded) enter the
    paired tables, so the three columns are always comparing the same rows.
    """
    paired: list[dict[str, Any]] = []
    for p in predictions:
        if p.r_hat is None:
            continue
        ctx = context.get(p.row_id)
        if ctx is None:
            continue
        paired.append(
            {
                "row_id": p.row_id,
                "p0": p.p0,
                "q": p.q_raw,
                "r_hat": p.r_hat,
                "month": ctx["month"],
                "bucket": ctx["bucket"],
                "city": ctx["city"],
                "target_date": ctx["target_date"],
            }
        )
    return {"paired": paired}


def _summarize(group: list[dict[str, Any]], y_by_row: dict[str, int]) -> dict[str, Any]:
    n = len(group)
    if n == 0:
        return {"n": 0, "clusters": 0, "ll_p0": None, "ll_q": None, "ll_r": None}
    clusters = len({cluster_key(g["city"], g["target_date"]) for g in group})
    ll_p0 = sum(logloss(y_by_row[g["row_id"]], g["p0"]) for g in group) / n
    ll_q = sum(logloss(y_by_row[g["row_id"]], g["q"]) for g in group) / n
    ll_r = sum(logloss(y_by_row[g["row_id"]], g["r_hat"]) for g in group) / n
    return {"n": n, "clusters": clusters, "ll_p0": ll_p0, "ll_q": ll_q, "ll_r": ll_r}


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
    raw_rows: list[dict[str, Any]],
    context: dict[str, dict[str, Any]],
    unparsable_lead: dict[str, int],
    y_by_row: dict[str, int],
    result,
) -> str:
    lines: list[str] = [
        "# Market-anchored walk-forward residual calibrator — report",
        "",
        "p0 = settlement_attribution.market_in_bin_prob (derived from OUR fill "
        "price — a proxy for decision-time top-of-book until item 3 lands "
        "explicit p0 provenance; every number below inherits that proxy's "
        "noise).",
        "decision-date source = decision_posterior_computed_at (entry-command "
        "created_at is not reliably joinable in zeus-world.db for these rows "
        "— see module docstring).",
        f"lambda grid = {LAMBDA_GRID}; lambda selected = {result.lambda_selected} "
        f"(tuning-fold selection used: {result.lambda_selection_used_tuning}).",
        CHALLENGER_NOTE,
        "",
        "## Coverage",
        f"settlement_attribution rows with q_in_bin/market_in_bin_prob/"
        f"settled_in_bin/direction all NOT NULL: {len(raw_rows)}",
        f"excluded by walk_forward (missing timestamps / unmapped lead / "
        f"insufficient training data / invalid input): {result.n_excluded_total}",
        f"excluded_reasons: {dict(result.excluded_reasons)}",
        f"unparsable_target_date (decision_at present, target_date not parseable): "
        f"{unparsable_lead['unparsable_target_date']}",
        f"lead_not_modeled (lead outside day0/day1/day2): {unparsable_lead['lead_not_modeled']}",
        "",
    ]

    paired = compute_paired_report(result.predictions, context)["paired"]

    lines.append("## Paired log-loss by month")
    lines.append("| month | n | clusters | logloss(p0) | logloss(q) | logloss(r_hat) |")
    lines.append("|---|---|---|---|---|---|")
    by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in paired:
        by_month[row["month"]].append(row)
    for month in sorted(by_month):
        s = _summarize(by_month[month], y_by_row)
        lines.append(
            f"| {month} | {s['n']} | {s['clusters']} | {_fmt(s['ll_p0'])} | "
            f"{_fmt(s['ll_q'])} | {_fmt(s['ll_r'])} |"
        )
    s_all = _summarize(paired, y_by_row)
    lines.append(
        f"| ALL | {s_all['n']} | {s_all['clusters']} | {_fmt(s_all['ll_p0'])} | "
        f"{_fmt(s_all['ll_q'])} | {_fmt(s_all['ll_r'])} |"
    )
    lines.append("")

    lines.append("## Paired log-loss by |q-p| bucket")
    lines.append("| bucket | n | clusters | logloss(p0) | logloss(q) | logloss(r_hat) |")
    lines.append("|---|---|---|---|---|---|")
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in paired:
        by_bucket[row["bucket"]].append(row)
    for bucket in ("<0.15", "0.15-0.30", "0.30-0.50", ">0.50"):
        s = _summarize(by_bucket.get(bucket, []), y_by_row)
        lines.append(
            f"| {bucket} | {s['n']} | {s['clusters']} | {_fmt(s['ll_p0'])} | "
            f"{_fmt(s['ll_q'])} | {_fmt(s['ll_r'])} |"
        )
    lines.append("")

    lines.append("## Beta trajectory (per refit decision-date, monthly-sampled tail)")
    lines.append("| decision_date | beta |")
    lines.append("|---|---|")
    for decision_date, beta in result.beta_trajectory[-24:]:
        lines.append(f"| {decision_date} | {beta:.4f} |")
    lines.append("")

    lines.append("## Final frozen artifact (as of the last refit)")
    if result.final_artifact is None:
        lines.append("no artifact fitted (insufficient data for even one refit)")
    else:
        a = result.final_artifact
        lines.append(f"alpha: {a.alpha}")
        lines.append(f"beta: {a.beta:.4f}")
        lines.append(f"lambda: {a.lambda_}")
        lines.append(f"clip_d: {a.clip_d}")
        lines.append(f"p_clip: {a.p_clip}")
        lines.append(f"training_cutoff: {a.training_cutoff}")
        lines.append(f"n_train: {a.n_train} n_excluded: {a.n_excluded}")
        lines.append(f"excluded_reasons: {dict(a.excluded_reasons)}")
        lines.append(f"param_hash: {a.param_hash}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Repo root (DB path is relative to this).")
    parser.add_argument("--world", default="state/zeus-world.db", help="World DB path (relative to --root).")
    parser.add_argument(
        "--min-train-rows",
        type=int,
        default=None,
        help="Override the calibrator's MIN_TRAIN_ROWS (for smoke runs on small fixtures).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    root = Path(args.root)
    world_path = root / args.world

    conn = open_ro(world_path)
    try:
        raw_rows = load_rows(conn)
    finally:
        conn.close()

    wf_rows, context, unparsable_lead = build_walk_forward_rows(raw_rows)
    y_by_row = {r["attribution_id"]: int(r["settled_in_bin"]) for r in raw_rows}

    kwargs: dict[str, Any] = {}
    if args.min_train_rows is not None:
        kwargs["min_train_rows"] = args.min_train_rows
    result = walk_forward(wf_rows, **kwargs)

    print(
        render_report(
            raw_rows=raw_rows,
            context=context,
            unparsable_lead=unparsable_lead,
            y_by_row=y_by_row,
            result=result,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
