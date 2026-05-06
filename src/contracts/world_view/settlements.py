# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §3.2
"""World-view accessor: settlements.

Provides get_settlement_truth(world_conn, city, target_date) -> SettlementView | None.

Uses world_conn opened by the caller — no ATTACH, no module-level singleton.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class SettlementView:
    """Read-only view of a settlement record from world.settlements.

    Callers must not write back through this object.
    """
    city: str
    target_date: str
    settlement_value: Optional[float]
    outcome: Optional[str]
    source: Optional[str]
    authority: Optional[str]
    data_version: Optional[str]
    settled_at: Optional[str]
    range_label: Optional[str]
    winning_bin: Optional[str]


def get_settlement_truth(
    world_conn: sqlite3.Connection,
    city: str,
    target_date: date | str,
    temperature_metric: str = "high",
) -> Optional[SettlementView]:
    """Return settlement truth for (city, target_date) from world DB.

    world_conn must already be open — caller manages lifecycle.
    Returns None if no VERIFIED settlement row exists for the requested
    temperature metric.
    """
    target_date_str = str(target_date)

    try:
        row = world_conn.execute(
            """
            SELECT city, target_date, settlement_value, NULL AS outcome,
                   settlement_source AS source, authority, data_version, settled_at,
                   winning_bin AS range_label, winning_bin
              FROM settlements
             WHERE city = ? AND target_date = ?
               AND temperature_metric = ?
               AND authority = 'VERIFIED'
             ORDER BY settled_at DESC
             LIMIT 1
            """,
            (city, target_date_str, temperature_metric),
        ).fetchone()
    except sqlite3.OperationalError:
        return None

    if row is None:
        return None

    def _col(name, idx):
        try:
            return row[name]
        except (IndexError, KeyError):
            try:
                return row[idx]
            except (IndexError, TypeError):
                return None

    return SettlementView(
        city=_col("city", 0),
        target_date=_col("target_date", 1),
        settlement_value=_col("settlement_value", 2),
        outcome=_col("outcome", 3),
        source=_col("source", 4),
        authority=_col("authority", 5),
        data_version=_col("data_version", 6),
        settled_at=_col("settled_at", 7),
        range_label=_col("range_label", 8),
        winning_bin=_col("winning_bin", 9),
    )
