#!/usr/bin/env python3
# Created: 2026-07-28
# Last reused/audited: 2026-07-28
# Authority basis: operator-directed trade DB growth and decision-evidence audit.
"""Bounded, read-only trade DB growth census.

The live trade DB is too large for recurring ``dbstat`` or whole-table
aggregation. This probe reads schema metadata, rowid high-water marks, and a
small indexed tail from known high-growth tables. Its output is diagnostic
evidence only; it never deletes, vacuums, checkpoints, or authorizes retention.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.state.db import _connect_read_only  # noqa: E402

DEFAULT_DB = ROOT / "state" / "zeus_trades.db"


@dataclass(frozen=True)
class TableProbe:
    time_column: str
    payload_columns: tuple[str, ...] = ()
    category_column: str | None = None
    retention_class: str = "inspect"
    rationale: str = ""


TABLE_PROBES: Final[dict[str, TableProbe]] = {
    "execution_feasibility_evidence": TableProbe(
        time_column="created_at",
        payload_columns=("depth_before_json",),
        retention_class="legacy_append_history_with_latest_mirror",
        rationale=(
            "Hot reads have a compact execution_feasibility_latest mirror; "
            "the append history needs a citation/retention proof, not indefinite growth."
        ),
    ),
    "executable_market_snapshots": TableProbe(
        time_column="captured_at",
        payload_columns=(
            "orderbook_depth_json",
            "fee_details_json",
            "token_map_json",
            "tradeability_status_json",
        ),
        category_column="capture_trigger",
        retention_class="mixed_current_and_immutable_evidence",
        rationale=(
            "JIT/cited snapshots are immutable money evidence; broad recurring "
            "captures should move to a bounded current projection plus keyframes."
        ),
    ),
    "book_hash_transitions": TableProbe(
        time_column="observed_at",
        retention_class="derived_transition_history",
        rationale=(
            "Useful for bounded microstructure diagnosis, but reconstructable "
            "from retained snapshot/keyframe hashes."
        ),
    ),
    "decision_log": TableProbe(
        time_column="timestamp",
        payload_columns=("artifact_json",),
        category_column="mode",
        retention_class="decision_evidence_with_content_addressed_deltas",
        rationale=(
            "Action and rejection evidence is durable; repeated auction state "
            "should remain hash-verifiable while using full anchors plus deltas."
        ),
    ),
    "position_events": TableProbe(
        time_column="occurred_at",
        payload_columns=("payload_json",),
        category_column="event_type",
        retention_class="canonical_append_only",
        rationale=(
            "Lifecycle authority is not deletable telemetry. Recurring monitor "
            "payloads may be encoded more compactly without deleting the event spine."
        ),
    ),
    "token_price_log": TableProbe(
        time_column="timestamp",
        retention_class="resampleable_market_history",
        rationale=(
            "Decision-linked ticks matter; non-linked dense history can use "
            "time-bucket/turning-point retention after citation analysis."
        ),
    ),
    "collateral_ledger_snapshots": TableProbe(
        time_column="captured_at",
        payload_columns=(
            "ctf_token_balances_json",
            "ctf_token_allowances_json",
            "reserved_tokens_for_sells_json",
        ),
        retention_class="rolling_authority_with_command_bound_anchors",
        rationale=(
            "Current capital authority and command-bound witnesses matter; "
            "unchanged background snapshots are suitable for rolling retention."
        ),
    ),
}


def _connect(path: Path) -> sqlite3.Connection:
    conn = _connect_read_only(path)
    conn.execute("PRAGMA mmap_size = 0")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    # Table names come exclusively from TABLE_PROBES, never from user input.
    return {
        str(row["name"])
        for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _probe_table(
    conn: sqlite3.Connection,
    *,
    table: str,
    spec: TableProbe,
    tail_rows: int,
) -> dict[str, object]:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if exists is None:
        return {"present": False}
    columns = _table_columns(conn, table)
    required = {
        spec.time_column,
        *spec.payload_columns,
        *(() if spec.category_column is None else (spec.category_column,)),
    }
    missing = sorted(required - columns)
    if missing:
        return {"present": True, "schema_mismatch": missing}

    high_watermark = conn.execute(
        f'SELECT max(rowid) FROM "{table}"'
    ).fetchone()[0]
    if high_watermark is None:
        return {
            "present": True,
            "rowid_high_watermark": None,
            "sample_rows": 0,
            "retention_class": spec.retention_class,
            "rationale": spec.rationale,
        }
    lower_bound = max(0, int(high_watermark) - tail_rows)
    selected = [spec.time_column, *spec.payload_columns]
    if spec.category_column is not None:
        selected.append(spec.category_column)
    select_sql = ", ".join(f'"{column}"' for column in selected)
    rows = conn.execute(
        f'SELECT {select_sql} FROM "{table}" '
        "WHERE rowid > ? ORDER BY rowid",
        (lower_bound,),
    ).fetchall()
    payload_lengths = [
        sum(
            len(str(row[column]).encode("utf-8"))
            for column in spec.payload_columns
            if row[column] is not None
        )
        for row in rows
    ]
    payload_column_stats: dict[str, dict[str, int | float]] = {}
    for column in spec.payload_columns:
        lengths = [
            len(str(row[column]).encode("utf-8"))
            for row in rows
            if row[column] is not None
        ]
        payload_column_stats[column] = {
            "nonnull_rows": len(lengths),
            "nonnull_fraction": (
                round(len(lengths) / len(rows), 4) if rows else 0.0
            ),
            "mean_nonnull_bytes": (
                round(sum(lengths) / len(lengths), 1) if lengths else 0
            ),
            "max_bytes": max(lengths, default=0),
        }
    categories: dict[str, int] = {}
    category_payload_lengths: dict[str, list[int]] = {}
    if spec.category_column is not None:
        for row, payload_length in zip(rows, payload_lengths, strict=True):
            category = str(row[spec.category_column] or "NULL")
            categories[category] = categories.get(category, 0) + 1
            category_payload_lengths.setdefault(category, []).append(payload_length)
    times = [str(row[spec.time_column]) for row in rows if row[spec.time_column]]
    return {
        "present": True,
        "rowid_high_watermark": int(high_watermark),
        "sample_rows": len(rows),
        "sample_rowid_span": tail_rows,
        "sample_oldest_at": min(times) if times else None,
        "sample_newest_at": max(times) if times else None,
        "sample_payload_mean_bytes": (
            round(sum(payload_lengths) / len(payload_lengths), 1)
            if payload_lengths
            else 0
        ),
        "sample_payload_max_bytes": max(payload_lengths, default=0),
        "sample_payload_columns": payload_column_stats,
        "sample_categories": dict(
            sorted(categories.items(), key=lambda item: (-item[1], item[0]))
        ),
        "sample_category_payloads": {
            category: {
                "rows": len(lengths),
                "mean_bytes": round(sum(lengths) / len(lengths), 1),
                "max_bytes": max(lengths),
            }
            for category, lengths in sorted(
                category_payload_lengths.items(),
                key=lambda item: (-len(item[1]), item[0]),
            )
        },
        "retention_class": spec.retention_class,
        "rationale": spec.rationale,
    }


def audit(path: Path, *, tail_rows: int) -> dict[str, object]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    with _connect(resolved) as conn:
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        tables = {
            table: _probe_table(
                conn,
                table=table,
                spec=spec,
                tail_rows=tail_rows,
            )
            for table, spec in TABLE_PROBES.items()
        }
    return {
        "schema_version": 1,
        "method": "bounded_rowid_tail_v1",
        "authority": "read_only_diagnostic_not_retention_authority",
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(resolved),
        "file_bytes": resolved.stat().st_size,
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "allocated_bytes": page_size * page_count,
        "freelist_bytes": page_size * freelist_count,
        "tail_rows": tail_rows,
        "tables": tables,
        "method_limits": [
            "rowid high-water marks are upper bounds when rows were deleted",
            "tail samples estimate current write shape, not whole-history size",
            "no dbstat, whole-table count, vacuum, checkpoint, or mutation is run",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--tail-rows", type=int, default=2_000)
    args = parser.parse_args()
    if not 10 <= args.tail_rows <= 100_000:
        parser.error("--tail-rows must be between 10 and 100000")
    print(
        json.dumps(
            audit(args.db, tail_rows=args.tail_rows),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
