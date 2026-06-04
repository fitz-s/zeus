# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: Operator directive 2026-06-04 #3 — the candidate LIST the operator
#   reviews (the arm review set) = ONLY order-able ∩ bias-PASS. Order-able = an
#   edli_no_submit_receipts row EXISTS for the candidate (a no-submit cert = it cleared
#   ALL gates and WOULD submit if armed). bias-PASS = mainstream_agreement_pass = 1 (the
#   forecast point is within tolerance of the traded bin, per the mainstream-agreement
#   annotation written by the reactor proof path). This is an OBSERVABILITY filter for the
#   ARM decision — NOT a trade gate (the trade still fires on trade_score; this only
#   filters what is DISPLAYED/reviewed). Mainstream is observational/display-only.
"""Read-only query: order-able ∩ bias-pass EDLI candidates for the operator arm review.

The `edli_no_submit_receipts` table is owned by zeus-world.db (the reactor proof path
writes the no-submit cert + the mainstream annotation there). A row's existence means
the candidate cleared all gates (order-able). The mainstream_agreement_pass column is
the bias verdict: 1 = PASS (forecast agrees with the independent mainstream within
tolerance), 0 = FAIL, NULL = UNKNOWN (cold cache, no mainstream point). Only 1 (PASS)
is shown — UNKNOWN != PASS and FAIL != PASS are both excluded (correct: until the
annotation deploys + warms, receipts carry NULL and will not pass the bias filter).

Usage (CLI):
    python -m scripts.ops.orderable_bias_pass_candidates [--limit N] [--db PATH]
"""
from __future__ import annotations

import argparse
import sqlite3
from typing import Any

# Order-able = the no-submit receipt EXISTS (it is a cleared-gates cert). bias-PASS =
# mainstream_agreement_pass = 1. ORDER BY created_at DESC (newest first). city /
# target_date / metric live in receipt_json (not dedicated columns); surface them via
# json_extract so the operator review set has the city per candidate.
_QUERY = """
SELECT
    json_extract(receipt_json, '$.city')        AS city,
    json_extract(receipt_json, '$.target_date') AS target_date,
    json_extract(receipt_json, '$.metric')      AS metric,
    direction,
    q_live,
    q_lcb_5pct,
    c_fee_adjusted,
    c_cost_95pct,
    trade_score,
    kelly_size_usd,
    mainstream_point,
    mainstream_delta,
    mainstream_bin_label,
    json_extract(receipt_json, '$.bin_label') AS bin_label,
    condition_id,
    receipt_id,
    created_at
FROM edli_no_submit_receipts
WHERE mainstream_agreement_pass = 1
ORDER BY created_at DESC
LIMIT :limit
"""


def query_orderable_bias_pass(
    conn: sqlite3.Connection, *, limit: int = 200
) -> list[sqlite3.Row]:
    """Return order-able ∩ bias-PASS receipts (newest first).

    Order-able = a no-submit receipt row exists (cleared all gates). bias-PASS =
    mainstream_agreement_pass = 1. UNKNOWN (NULL) and FAIL (0) are excluded — only an
    explicit PASS reaches the operator review set. Returns sqlite3.Row objects exposing
    city/direction/q/cost/trade_score/kelly_size/mainstream_delta per candidate.

    This is a READ-ONLY observability helper for the ARM decision. It does NOT affect
    any trade decision (the trade fires on trade_score; this only filters the display).
    """
    prior_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(_QUERY, {"limit": int(limit)}).fetchall()
    finally:
        conn.row_factory = prior_factory


def _world_db_path() -> str:
    from src.state.db import ZEUS_WORLD_DB_PATH

    return str(ZEUS_WORLD_DB_PATH)


def _format_row(row: sqlite3.Row) -> str:
    def _f(key: str, fmt: str = "{}") -> str:
        v = row[key] if key in row.keys() else None
        return fmt.format(v) if v is not None else "-"

    return (
        f"{_f('city'):<16} {_f('target_date'):<11} {_f('metric'):<5} "
        f"{_f('direction'):<7} bin={_f('bin_label'):<14} "
        f"q={_f('q_live', '{:.4f}')} cost={_f('c_fee_adjusted', '{:.4f}')} "
        f"score={_f('trade_score', '{:.4f}')} kelly=${_f('kelly_size_usd', '{:.2f}')} "
        f"Δ_mainstream={_f('mainstream_delta', '{:+.2f}')} "
        f"({_f('created_at')})"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Order-able ∩ bias-PASS EDLI candidates (operator arm review set)."
    )
    parser.add_argument("--limit", type=int, default=200, help="max rows (newest first)")
    parser.add_argument(
        "--db",
        default=None,
        help="path to zeus-world.db (default: configured ZEUS_WORLD_DB_PATH)",
    )
    args = parser.parse_args(argv)

    db_path = args.db or _world_db_path()
    conn = sqlite3.connect(db_path)
    try:
        rows = query_orderable_bias_pass(conn, limit=args.limit)
    finally:
        conn.close()

    if not rows:
        print(
            "no order-able ∩ bias-PASS candidates "
            "(receipts carry mainstream_agreement_pass=NULL until the annotation "
            "deploys + the warm cache populates; UNKNOWN != PASS)."
        )
        return 0

    print(f"order-able ∩ bias-PASS candidates ({len(rows)}, newest first):")
    for row in rows:
        print("  " + _format_row(row))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
