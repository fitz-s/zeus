#!/usr/bin/env python3
"""Emit the read-only paper money-path proof consumed by check_live_release_gate.

This script does not authorize live submit.  It turns concrete DB evidence into
the legacy paper-proof booleans so the release gate is not satisfied by hand
written ``true`` values.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_live_release_gate import PAPER_PROOF_KEYS, PASS, FAIL


@dataclass(frozen=True)
class Probe:
    ok: bool
    detail: str
    evidence: dict[str, Any]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise RuntimeError("query returned no row")
    return row


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _probe_scanner(forecasts: sqlite3.Connection) -> Probe:
    if not _table_exists(forecasts, "market_events"):
        return Probe(False, "missing_table:market_events", {})
    row = _one(
        forecasts,
        """
        SELECT COUNT(*) AS n,
               COUNT(DISTINCT condition_id) AS conditions,
               COUNT(DISTINCT city) AS cities,
               MAX(COALESCE(recorded_at, created_at, '')) AS latest
        FROM market_events
        WHERE COALESCE(condition_id, '') != ''
        """,
    )
    n = int(row["n"] or 0)
    return Probe(
        n > 0,
        f"market_events={n}",
        {
            "market_events": n,
            "conditions": int(row["conditions"] or 0),
            "cities": int(row["cities"] or 0),
            "latest": row["latest"],
        },
    )


def _probe_forecast(forecasts: sqlite3.Connection, *, now: datetime) -> Probe:
    if not _table_exists(forecasts, "readiness_state"):
        return Probe(False, "missing_table:readiness_state", {})
    row = _one(
        forecasts,
        """
        SELECT COUNT(*) AS n,
               COUNT(DISTINCT city_id) AS cities,
               MAX(computed_at) AS latest_computed,
               MAX(expires_at) AS latest_expiry
        FROM readiness_state
        WHERE status = 'LIVE_ELIGIBLE'
          AND (expires_at IS NULL OR expires_at >= ?)
        """,
        (_iso(now),),
    )
    n = int(row["n"] or 0)
    return Probe(
        n > 0,
        f"live_eligible_readiness={n}",
        {
            "live_eligible_readiness": n,
            "cities": int(row["cities"] or 0),
            "latest_computed": row["latest_computed"],
            "latest_expiry": row["latest_expiry"],
        },
    )


def _probe_evaluator(world: sqlite3.Connection, *, cutoff: datetime) -> Probe:
    if not _table_exists(world, "edli_no_submit_receipts"):
        return Probe(False, "missing_table:edli_no_submit_receipts", {})
    row = _one(
        world,
        """
        SELECT COUNT(*) AS n,
               COUNT(DISTINCT condition_id) AS conditions,
               COUNT(DISTINCT json_extract(receipt_json, '$.city')) AS cities,
               MAX(created_at) AS latest
        FROM edli_no_submit_receipts
        WHERE created_at >= ?
          AND side_effect_status = 'NO_SUBMIT'
          AND q_live IS NOT NULL
          AND q_lcb_5pct IS NOT NULL
          AND trade_score IS NOT NULL
        """,
        (_iso(cutoff),),
    )
    n = int(row["n"] or 0)
    return Probe(
        n > 0,
        f"recent_valid_receipts={n}",
        {
            "recent_valid_receipts": n,
            "conditions": int(row["conditions"] or 0),
            "cities": int(row["cities"] or 0),
            "latest": row["latest"],
        },
    )


def _probe_event_persistence(world: sqlite3.Connection, *, cutoff: datetime) -> Probe:
    if not _table_exists(world, "edli_live_order_events"):
        return Probe(False, "missing_table:edli_live_order_events", {})
    required = (
        "DecisionProofAccepted",
        "SubmitPlanBuilt",
        "PreSubmitRevalidated",
        "LiveCapReserved",
        "ExecutionCommandCreated",
        "CapTransitioned",
    )
    rows = world.execute(
        """
        SELECT event_type, COUNT(*) AS n, MAX(created_at) AS latest
        FROM edli_live_order_events
        WHERE created_at >= ?
        GROUP BY event_type
        """,
        (_iso(cutoff),),
    ).fetchall()
    by_type = {str(row["event_type"]): {"count": int(row["n"] or 0), "latest": row["latest"]} for row in rows}
    missing = [name for name in required if by_type.get(name, {}).get("count", 0) <= 0]
    return Probe(
        not missing,
        "complete_recent_shadow_chain" if not missing else "missing_event_types:" + ",".join(missing),
        {"event_types": by_type, "required": list(required)},
    )


def _probe_command_repo(world: sqlite3.Connection, *, cutoff: datetime) -> Probe:
    if not _table_exists(world, "decision_certificates"):
        return Probe(False, "missing_table:decision_certificates", {})
    required = (
        "FinalIntentCertificate",
        "PreSubmitRevalidationCertificate",
        "ExecutorExpressibilityCertificate",
        "ExecutionCommandCertificate",
        "ExecutionReceiptCertificate",
        "LiveCapCertificate",
        "LiveCapTransitionCertificate",
    )
    rows = world.execute(
        """
        SELECT certificate_type, COUNT(*) AS n, MAX(persisted_at) AS latest
        FROM decision_certificates
        WHERE persisted_at >= ?
          AND certificate_type IN ({})
        GROUP BY certificate_type
        """.format(",".join("?" for _ in required)),
        (_iso(cutoff), *required),
    ).fetchall()
    by_type = {str(row["certificate_type"]): {"count": int(row["n"] or 0), "latest": row["latest"]} for row in rows}
    missing = [name for name in required if by_type.get(name, {}).get("count", 0) <= 0]
    return Probe(
        not missing,
        "recent_execution_command_certificates" if not missing else "missing_certificate_types:" + ",".join(missing),
        {"certificate_types": by_type, "required": list(required)},
    )


def _probe_reconcile(world: sqlite3.Connection) -> Probe:
    required_tables = ("edli_live_order_projection", "exchange_reconcile_findings")
    missing = [table for table in required_tables if not _table_exists(world, table)]
    if missing:
        return Probe(False, "missing_table:" + ",".join(missing), {})
    projection = _one(
        world,
        """
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN pending_reconcile = 1 THEN 1 ELSE 0 END) AS pending,
               MAX(updated_at) AS latest
        FROM edli_live_order_projection
        """,
    )
    findings = _one(
        world,
        """
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN resolved_at IS NULL THEN 1 ELSE 0 END) AS unresolved
        FROM exchange_reconcile_findings
        """,
    )
    projection_n = int(projection["n"] or 0)
    pending = int(projection["pending"] or 0)
    unresolved = int(findings["unresolved"] or 0)
    ok = projection_n > 0 and pending == 0 and unresolved == 0
    detail = (
        f"projection_rows={projection_n}:pending_reconcile={pending}:"
        f"unresolved_findings={unresolved}"
    )
    return Probe(
        ok,
        detail,
        {
            "projection_rows": projection_n,
            "pending_reconcile": pending,
            "unresolved_findings": unresolved,
            "latest_projection": projection["latest"],
            "findings": int(findings["n"] or 0),
        },
    )


def _probe_redeem_reconciler(trade: sqlite3.Connection) -> Probe:
    if not _table_exists(trade, "settlement_commands"):
        return Probe(False, "missing_table:settlement_commands", {})
    blocking_states = ("REDEEM_OPERATOR_REQUIRED", "REDEEM_REVIEW_REQUIRED")
    row = _one(
        trade,
        """
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN state IN ({}) THEN 1 ELSE 0 END) AS blocking
        FROM settlement_commands
        """.format(",".join("?" for _ in blocking_states)),
        blocking_states,
    )
    blocking = int(row["blocking"] or 0)
    n = int(row["n"] or 0)
    return Probe(
        blocking == 0,
        f"settlement_commands={n}:blocking={blocking}",
        {"settlement_commands": n, "blocking": blocking},
    )


def build_proof(
    *,
    world_db: Path,
    forecasts_db: Path,
    trade_db: Path,
    now: datetime | None = None,
    lookback_hours: float = 24.0,
) -> dict[str, Any]:
    observed_at = now or _utc_now()
    cutoff = observed_at - timedelta(hours=float(lookback_hours))
    with _connect_ro(world_db) as world, _connect_ro(forecasts_db) as forecasts, _connect_ro(trade_db) as trade:
        probes = {
            "scanner": _probe_scanner(forecasts),
            "forecast": _probe_forecast(forecasts, now=observed_at),
            "evaluator": _probe_evaluator(world, cutoff=cutoff),
            "event_persistence": _probe_event_persistence(world, cutoff=cutoff),
            "command_repo": _probe_command_repo(world, cutoff=cutoff),
            "reconcile": _probe_reconcile(world),
            "redeem_reconciler": _probe_redeem_reconciler(trade),
        }
    missing = [key for key in PAPER_PROOF_KEYS if key not in probes]
    if missing:
        raise RuntimeError("unimplemented paper proof keys: " + ",".join(missing))
    status = PASS if all(probes[key].ok for key in PAPER_PROOF_KEYS) else FAIL
    return {
        "schema": "zeus_live_release_paper_proof_v1",
        "status": status,
        "live_eligibility": "UNKNOWN",
        "generated_at": _iso(observed_at),
        "lookback_hours": float(lookback_hours),
        **{key: probes[key].ok for key in PAPER_PROOF_KEYS},
        "probes": {
            key: {
                "ok": probes[key].ok,
                "detail": probes[key].detail,
                "evidence": probes[key].evidence,
            }
            for key in PAPER_PROOF_KEYS
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world-db", type=Path, default=ROOT / "state" / "zeus-world.db")
    parser.add_argument("--forecasts-db", type=Path, default=ROOT / "state" / "zeus-forecasts.db")
    parser.add_argument("--trade-db", type=Path, default=ROOT / "state" / "zeus_trades.db")
    parser.add_argument("--output", type=Path, default=ROOT / "state" / "paper_money_path_proof.json")
    parser.add_argument("--lookback-hours", type=float, default=24.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    proof = build_proof(
        world_db=args.world_db,
        forecasts_db=args.forecasts_db,
        trade_db=args.trade_db,
        lookback_hours=args.lookback_hours,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(proof, indent=2, sort_keys=True))
    else:
        print(f"paper_money_path_proof={proof['status']} output={args.output}")
        for key in PAPER_PROOF_KEYS:
            probe = proof["probes"][key]
            print(f"{key}={proof[key]} {probe['detail']}")
    return 0 if proof["status"] == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
