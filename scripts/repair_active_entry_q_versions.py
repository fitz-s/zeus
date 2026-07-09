#!/usr/bin/env python3
# Lifecycle: created=2026-07-09; last_reviewed=2026-07-09; last_reused=2026-07-09
# Purpose: Repair legacy live ENTRY commands whose q_version is missing while exposure remains open.
# Reuse: Run with --json first; use --apply only after operator approval.
# Authority basis: AGENTS.md probability/execution proof gates; scripts/AGENTS.md repair contract.
"""Repair missing active ENTRY q_version stamps from existing decision certificates.

This script does not place, cancel, or query venue orders.  It reconstructs the
forecast_posteriors.posterior_identity_hash from persisted FinalIntentCertificate
evidence and writes only empty venue_commands.q_version cells for currently open
exposure rows.  Ambiguous or missing certificate evidence refuses the apply path.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.control.live_health import _entry_q_version_reconstruction_sample
from src.state.db import (
    _connect,
    _connect_read_only,
    get_trade_connection,
    get_trade_connection_read_only,
)


DEFAULT_TRADE_DB = ROOT / "state" / "zeus_trades.db"
ACTIVE_PHASES = ("active", "day0_window", "pending_exit")
RECONSTRUCTED_STATUSES = frozenset(
    {
        "reconstructed_from_final_intent_certificate",
        "reconstructed_from_final_intent_edge",
    }
)
SOURCE_MODULE = "scripts.repair_active_entry_q_versions"


@dataclass(frozen=True)
class QVersionCandidate:
    command_id: str
    position_id: str
    q_version: str
    reconstruction_status: str
    certificate_id: str | None
    certificate_hash: str | None
    snapshot_id: str | None
    decision_snapshot_id: str | None
    decision_id: str | None
    phase: str | None
    city: str | None
    target_date: str | None
    bin_label: str | None
    direction: str | None
    created_at: str | None
    q_live: Any
    q_lcb_5pct: Any

    def as_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "position_id": self.position_id,
            "q_version": self.q_version,
            "reconstruction_status": self.reconstruction_status,
            "certificate_id": self.certificate_id,
            "certificate_hash": self.certificate_hash,
            "snapshot_id": self.snapshot_id,
            "decision_snapshot_id": self.decision_snapshot_id,
            "decision_id": self.decision_id,
            "phase": self.phase,
            "city": self.city,
            "target_date": self.target_date,
            "bin_label": self.bin_label,
            "direction": self.direction,
            "created_at": self.created_at,
            "q_live": self.q_live,
            "q_lcb_5pct": self.q_lcb_5pct,
        }


def _same_path(left: Path, right: Path) -> bool:
    return left.expanduser().resolve() == right.expanduser().resolve()


def _open_trade_connection(db_path: Path, *, apply: bool) -> sqlite3.Connection:
    if _same_path(db_path, DEFAULT_TRADE_DB):
        return get_trade_connection(write_class="live") if apply else get_trade_connection_read_only()
    return _connect(db_path, write_class="live") if apply else _connect_read_only(db_path)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _optional_sql_column(alias: str, columns: set[str], column: str) -> str:
    if column in columns:
        return f"{alias}.{column} AS {column}"
    return f"NULL AS {column}"


def _active_missing_rows(db_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = _open_trade_connection(db_path, apply=False)
    conn.row_factory = sqlite3.Row
    try:
        venue_columns = _table_columns(conn, "venue_commands")
        position_columns = _table_columns(conn, "position_current")
        blocked: list[dict[str, Any]] = []
        required_venue = {
            "command_id",
            "position_id",
            "intent_kind",
            "created_at",
            "q_version",
        }
        required_position = {"position_id", "phase", "shares", "chain_shares"}
        missing_venue = sorted(required_venue - venue_columns)
        missing_position = sorted(required_position - position_columns)
        if missing_venue or missing_position:
            blocked.append(
                {
                    "reason": "required_columns_missing",
                    "missing_venue_columns": missing_venue,
                    "missing_position_columns": missing_position,
                }
            )
            return [], blocked

        select_columns = [
            "pc.position_id AS position_id",
            "pc.phase AS phase",
            _optional_sql_column("pc", position_columns, "order_status"),
            "pc.shares AS shares",
            "pc.chain_shares AS chain_shares",
            _optional_sql_column("pc", position_columns, "decision_snapshot_id"),
            _optional_sql_column("pc", position_columns, "city"),
            _optional_sql_column("pc", position_columns, "target_date"),
            _optional_sql_column("pc", position_columns, "bin_label"),
            _optional_sql_column("pc", position_columns, "direction"),
            _optional_sql_column("pc", position_columns, "p_posterior"),
            "vc.command_id AS command_id",
            _optional_sql_column("vc", venue_columns, "state"),
            "vc.created_at AS created_at",
            _optional_sql_column("vc", venue_columns, "decision_id"),
            _optional_sql_column("vc", venue_columns, "snapshot_id"),
            _optional_sql_column("vc", venue_columns, "price"),
            _optional_sql_column("vc", venue_columns, "size"),
        ]
        rows = conn.execute(
            f"""
            SELECT {", ".join(select_columns)}
              FROM position_current pc
              JOIN venue_commands vc
                ON vc.position_id = pc.position_id
             WHERE vc.intent_kind = 'ENTRY'
               AND pc.phase IN ({",".join("?" for _ in ACTIVE_PHASES)})
               AND (
                   COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
                   OR COALESCE(CAST(pc.shares AS REAL), 0.0) > 0.0
               )
               AND (vc.q_version IS NULL OR TRIM(CAST(vc.q_version AS TEXT)) = '')
             ORDER BY datetime(vc.created_at) DESC, vc.command_id DESC
            """,
            ACTIVE_PHASES,
        ).fetchall()
        return [dict(row) for row in rows], blocked
    finally:
        conn.close()


def _classify_reconstruction(
    row: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[QVersionCandidate | None, dict[str, Any] | None]:
    status = str(evidence.get("reconstruction_status") or "")
    q_version = str(evidence.get("posterior_identity_hash") or "").strip()
    if status not in RECONSTRUCTED_STATUSES:
        blocked = {**row, **evidence, "reason": status or "reconstruction_status_missing"}
        return None, blocked
    if not q_version:
        blocked = {**row, **evidence, "reason": "posterior_identity_hash_missing"}
        return None, blocked
    command_id = str(row.get("command_id") or "").strip()
    position_id = str(row.get("position_id") or "").strip()
    if not command_id or not position_id:
        blocked = {**row, **evidence, "reason": "command_or_position_id_missing"}
        return None, blocked

    return (
        QVersionCandidate(
            command_id=command_id,
            position_id=position_id,
            q_version=q_version,
            reconstruction_status=status,
            certificate_id=evidence.get("certificate_id"),
            certificate_hash=evidence.get("certificate_hash"),
            snapshot_id=row.get("snapshot_id"),
            decision_snapshot_id=row.get("decision_snapshot_id"),
            decision_id=row.get("decision_id"),
            phase=row.get("phase"),
            city=row.get("city"),
            target_date=row.get("target_date"),
            bin_label=row.get("bin_label"),
            direction=row.get("direction"),
            created_at=row.get("created_at"),
            q_live=evidence.get("q_live"),
            q_lcb_5pct=evidence.get("q_lcb_5pct"),
        ),
        None,
    )


def build_plan(db_path: Path) -> dict[str, Any]:
    rows, blocked = _active_missing_rows(db_path)
    candidates: list[QVersionCandidate] = []
    if rows:
        evidence_rows = _entry_q_version_reconstruction_sample(db_path, rows)
        for row, evidence in zip(rows, evidence_rows, strict=True):
            candidate, block = _classify_reconstruction(row, evidence)
            if candidate is not None:
                candidates.append(candidate)
            elif block is not None:
                blocked.append(block)

    seen_commands: set[str] = set()
    duplicate_commands: list[dict[str, Any]] = []
    unique_candidates: list[QVersionCandidate] = []
    for candidate in candidates:
        if candidate.command_id in seen_commands:
            duplicate_commands.append(
                {**candidate.as_dict(), "reason": "duplicate_command_candidate"}
            )
            continue
        seen_commands.add(candidate.command_id)
        unique_candidates.append(candidate)
    blocked.extend(duplicate_commands)

    return {
        "db": str(db_path),
        "source_module": SOURCE_MODULE,
        "active_missing_count": len(rows),
        "candidate_count": len(unique_candidates),
        "blocked_count": len(blocked),
        "candidates": [candidate.as_dict() for candidate in unique_candidates],
        "blocked": blocked,
        "venue_action": False,
        "db_backup_created": False,
        "write_targets": ["venue_commands.q_version"],
    }


def apply_candidates(db_path: Path, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conn = _open_trade_connection(db_path, apply=True)
    conn.row_factory = sqlite3.Row
    applied: list[dict[str, Any]] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        for candidate in candidates:
            command_id = str(candidate["command_id"])
            q_version = str(candidate["q_version"]).strip()
            cur = conn.execute(
                """
                UPDATE venue_commands
                   SET q_version = ?
                 WHERE command_id = ?
                   AND intent_kind = 'ENTRY'
                   AND (q_version IS NULL OR TRIM(CAST(q_version AS TEXT)) = '')
                """,
                (q_version, command_id),
            )
            if cur.rowcount != 1:
                conn.rollback()
                raise RuntimeError(
                    f"refused partial q_version repair for command_id={command_id}: "
                    f"rowcount={cur.rowcount}"
                )
            applied.append({"command_id": command_id, "q_version": q_version})
        conn.commit()
        return applied
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run(*, db_path: Path = DEFAULT_TRADE_DB, apply: bool = False) -> dict[str, Any]:
    plan = build_plan(db_path)
    blocked_count = int(plan["blocked_count"])
    result: dict[str, Any] = {
        "ok": blocked_count == 0,
        "apply": apply,
        "dry_run": not apply,
        **plan,
        "applied_count": 0,
        "applied": [],
    }
    if blocked_count:
        result["issue"] = f"Q_VERSION_REPAIR_BLOCKED:n={blocked_count}"
        return result
    if apply:
        result["applied"] = apply_candidates(db_path, plan["candidates"])
        result["applied_count"] = len(result["applied"])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_TRADE_DB, help="Trade DB path.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Write missing q_version values.")
    mode.add_argument("--dry-run", action="store_true", help="Explicit read-only mode.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)
    result = run(db_path=args.db, apply=bool(args.apply))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(
            "active entry q_version repair: "
            f"{mode} candidates={result['candidate_count']} "
            f"blocked={result['blocked_count']} applied={result['applied_count']}"
        )
        if result.get("issue"):
            print(result["issue"])
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
