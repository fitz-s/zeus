# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: Runtime-to-registry assertions for live-release money-path schema
#   columns that must remain visible to future agents and gates.
# Reuse: Run when changing DB schema columns, db_table_ownership.yaml, or
#   live-release registry assertions.
# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_live_release_proof_p0p3/task.md P2-2
"""Runtime-to-registry assertions for live release proof columns.

This is intentionally narrower than the legacy global registry coherence suite:
it verifies the live-release money-path columns added or relied on by this
packet are present in both fresh runtime schema and db_table_ownership.yaml.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from src.state.db import init_schema
from src.state.snapshot_repo import init_snapshot_schema


def _columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _registry_entries(table_name: str) -> list[dict]:
    registry = yaml.safe_load(Path("architecture/db_table_ownership.yaml").read_text())
    return [entry for entry in registry["tables"] if entry.get("name") == table_name]


def _registry_mentions(table_name: str, column_name: str) -> bool:
    return any(column_name in yaml.safe_dump(entry) for entry in _registry_entries(table_name))


def test_live_release_runtime_columns_are_registry_visible() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        init_schema(conn)
        init_snapshot_schema(conn)

        required = {
            "settlement_commands": {"autoretry_eligible", "polymarket_end_anchor_source"},
            "no_trade_events": {"schema_compatibility"},
            "executable_market_snapshots": {"tradeability_status_json", "depth_at_best_ask"},
        }
        for table_name, column_names in required.items():
            runtime_columns = _columns(conn, table_name)
            for column_name in column_names:
                assert column_name in runtime_columns, f"{table_name}.{column_name} missing from runtime schema"
                assert _registry_mentions(table_name, column_name), (
                    f"{table_name}.{column_name} missing from architecture/db_table_ownership.yaml"
                )
    finally:
        conn.close()
