# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: OPERATOR LAW 2026-06-11 ~13:20Z "每一个做的决策为什么都需要被查阅。我需要
#   一切可被溯源" — every decision must be queryable / fully traceable. This is the human query
#   entry for the DecisionProvenanceEnvelope. Plan:
#   docs/evidence/settlement_guard/2026-06-11_decision_provenance_plan.md.
"""Query decision-provenance envelopes from the canonical receipt surfaces (read-only).

Dumps the FULL provenance envelope (data combination, per-input ages, time-to-settlement,
economics, FULL untruncated rejection reason) for the most recent decisions, filtered by
condition-id or scope (city/target_date/metric). Reads no_trade_regret_events (REJECTED, every
stage) and edli_no_submit_receipts (no-submit) on zeus-world.db, both read-only.

Usage:
    .venv/bin/python scripts/query_decision_provenance.py --condition-id 0x70b8...
    .venv/bin/python scripts/query_decision_provenance.py --city "Helsinki" \
        --target-date 2026-06-12 --metric high --last 5
    .venv/bin/python scripts/query_decision_provenance.py --last 10          # newest decisions
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORLD = ROOT / "state" / "zeus-world.db"


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        return column in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return False


def _pretty(envelope_text: str | None) -> str:
    if not envelope_text:
        return "  (no envelope on this receipt — legacy row or builder fail-soft NULL)"
    try:
        from src.contracts.decision_provenance import pretty_envelope

        return "\n".join("  " + line for line in pretty_envelope(json.loads(envelope_text)).splitlines())
    except Exception as exc:  # noqa: BLE001 — display-only
        return f"  (envelope present but unrenderable: {exc})"


def _query_regret(conn: sqlite3.Connection, args: argparse.Namespace) -> list[sqlite3.Row]:
    if not _has_column(conn, "no_trade_regret_events", "envelope_json"):
        return []
    where, params = _where_clause(args, scope_cols=("city", "target_date", "metric"))
    sql = (
        "SELECT decision_time, rejection_stage, rejection_reason, condition_id, token_id, "
        "       city, target_date, metric, bin_label, envelope_json "
        "FROM no_trade_regret_events "
        f"{where} ORDER BY decision_time DESC LIMIT ?"
    )
    return conn.execute(sql, (*params, args.last)).fetchall()


def _query_no_submit(conn: sqlite3.Connection, args: argparse.Namespace) -> list[sqlite3.Row]:
    if not _has_column(conn, "edli_no_submit_receipts", "envelope_json"):
        return []
    # no_submit receipts have no city/target_date/metric columns; scope filter applies via
    # condition_id only (the scope filter degrades to "all" when only city/date/metric are given).
    clauses: list[str] = []
    params: list[object] = []
    if args.condition_id:
        clauses.append("condition_id = ?")
        params.append(args.condition_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        "SELECT decision_time, side_effect_status, condition_id, token_id, direction, "
        "       envelope_json "
        "FROM edli_no_submit_receipts "
        f"{where} ORDER BY decision_time DESC LIMIT ?"
    )
    return conn.execute(sql, (*params, args.last)).fetchall()


def _where_clause(args: argparse.Namespace, *, scope_cols: tuple[str, str, str]) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if args.condition_id:
        clauses.append("condition_id = ?")
        params.append(args.condition_id)
    city_col, date_col, metric_col = scope_cols
    if args.city:
        clauses.append(f"{city_col} = ?")
        params.append(args.city)
    if args.target_date:
        clauses.append(f"{date_col} = ?")
        params.append(args.target_date)
    if args.metric:
        clauses.append(f"{metric_col} = ?")
        params.append(args.metric)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--condition-id", dest="condition_id", default=None)
    ap.add_argument("--city", default=None)
    ap.add_argument("--target-date", dest="target_date", default=None)
    ap.add_argument("--metric", default=None, choices=("high", "low"))
    ap.add_argument("--last", type=int, default=5, help="max decisions per surface (default 5)")
    args = ap.parse_args()

    conn = _ro(WORLD)
    try:
        regret_rows = _query_regret(conn, args)
        no_submit_rows = _query_no_submit(conn, args)
    finally:
        conn.close()

    print("=" * 100)
    print("DECISION PROVENANCE QUERY  —  every decision, fully traceable (operator law 2026-06-11)")
    filt = args.condition_id or f"{args.city}|{args.target_date}|{args.metric}" or "ALL"
    print(f"filter={filt}  last={args.last}")
    print("=" * 100)

    print(f"\n--- REJECTED decisions (no_trade_regret_events) : {len(regret_rows)} ---")
    for row in regret_rows:
        print(
            f"\n[{row['decision_time']}] REJECTED stage={row['rejection_stage']} "
            f"scope={row['city']}|{row['target_date']}|{row['metric']} bin={row['bin_label']}"
        )
        print(f"  FULL reason: {row['rejection_reason']}")
        print(_pretty(row["envelope_json"]))

    print(f"\n--- NO-SUBMIT decisions (edli_no_submit_receipts) : {len(no_submit_rows)} ---")
    for row in no_submit_rows:
        print(
            f"\n[{row['decision_time']}] {row['side_effect_status']} "
            f"condition={row['condition_id']} direction={row['direction']}"
        )
        print(_pretty(row["envelope_json"]))

    if not regret_rows and not no_submit_rows:
        print("\n(no matching decisions — check filter, or the envelope column is not yet migrated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
