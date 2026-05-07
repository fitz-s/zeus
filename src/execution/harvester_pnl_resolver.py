# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §5 Phase 1.5
"""Trading-side P&L resolver (Phase 1.5 harvester split).

Reads world.settlements via get_world_connection() (read-only).
Writes trade.decision_log via store_settlement_records() and settles positions
via _settle_positions() — both are trading-side operations.

Design invariants:
- Does NOT write to settlements, settlements_v2, market_events_v2, or any world table.
- If world.settlements has no new rows, returns awaiting_truth_writer status.
- Feature-flagged: ZEUS_HARVESTER_LIVE_ENABLED must equal "1" or function is a no-op.
- May import from src.execution.harvester (trading side, no circular reference).
- Does NOT import from src.ingest_main or scripts.ingest.*.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _row_value(row, key: str, index: int, default=None):
    if hasattr(row, "keys") and key in row.keys():
        return row[key]
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def resolve_pnl_for_settled_markets(trade_conn, world_conn) -> dict:
    """Resolve P&L for markets that have been settled in world.settlements.

    Reads settled rows from world.settlements that have not yet been processed
    by the trading side. Settles matching positions and writes decision_log rows.

    Parameters
    ----------
    trade_conn:
        Connection returned by get_trade_connection(). All trade-side writes go here.
    world_conn:
        Connection returned by get_world_connection(). Read-only access to settlements.

    Returns
    -------
    dict with keys: positions_settled, decision_log_rows_written, errors,
    and optionally status="awaiting_truth_writer" if no settled rows found.
    """
    if os.environ.get("ZEUS_HARVESTER_LIVE_ENABLED", "0") != "1":
        logger.info(
            "harvester_pnl_resolver disabled by ZEUS_HARVESTER_LIVE_ENABLED flag "
            "(DR-33-A default-OFF); cycle skipped"
        )
        return {
            "status": "disabled_by_feature_flag",
            "disabled_by_flag": True,
            "positions_settled": 0,
            "decision_log_rows_written": 0,
            "errors": 0,
        }

    # Read settled rows from world.settlements (VERIFIED authority only).
    try:
        rows = world_conn.execute(
            """
            SELECT city, target_date, market_slug, winning_bin, temperature_metric,
                   authority, settlement_source, settlement_value
            FROM settlements
            WHERE authority = 'VERIFIED'
            ORDER BY settled_at DESC
            LIMIT 200
            """
        ).fetchall()
    except Exception as exc:
        logger.warning("harvester_pnl_resolver: settlements read failed: %s", exc)
        return {
            "status": "settlements_read_error",
            "error": str(exc),
            "positions_settled": 0,
            "decision_log_rows_written": 0,
            "errors": 1,
        }

    if not rows:
        logger.debug(
            "harvester_pnl_resolver: no VERIFIED settlements in world.settlements; "
            "truth writer may not have run yet"
        )
        return {
            "status": "awaiting_truth_writer",
            "positions_settled": 0,
            "decision_log_rows_written": 0,
            "errors": 0,
        }

    # Import trading-side dependencies.
    from src.execution.harvester import _settle_positions
    from src.state.decision_chain import SettlementRecord, store_settlement_records
    from src.state.portfolio import load_portfolio, save_portfolio
    from src.state.strategy_tracker import get_tracker, save_tracker
    from src.state.canonical_write import commit_then_export

    portfolio = load_portfolio()
    settlement_records: list[SettlementRecord] = []
    tracker = get_tracker()
    tracker_dirty = False

    positions_settled = 0
    errors = 0

    for row in rows:
        city_name = _row_value(row, "city", 0, "")
        target_date = _row_value(row, "target_date", 1, "")
        market_slug = _row_value(row, "market_slug", 2, "")
        winning_bin = _row_value(row, "winning_bin", 3, "")
        temperature_metric = _row_value(row, "temperature_metric", 4, "")
        authority = str(_row_value(row, "authority", 5, "") or "").upper()
        settlement_source = _row_value(row, "settlement_source", 6, "")
        settlement_value = _row_value(row, "settlement_value", 7, None)

        if not city_name or not target_date or not winning_bin:
            continue
        if authority != "VERIFIED":
            logger.warning(
                "harvester_pnl_resolver: skipping non-VERIFIED settlement row for %s %s: %s",
                city_name, target_date, authority,
            )
            continue

        try:
            n_settled = _settle_positions(
                trade_conn,
                portfolio,
                city_name,
                target_date,
                winning_bin,
                settlement_records=settlement_records,
                strategy_tracker=tracker,
                settlement_authority=authority,
                settlement_truth_source="world.settlements",
                settlement_market_slug=str(market_slug or ""),
                settlement_temperature_metric=str(temperature_metric or ""),
                settlement_source=str(settlement_source or ""),
                settlement_value=settlement_value,
            )
            positions_settled += n_settled
            if n_settled > 0:
                tracker_dirty = True
        except Exception as exc:
            logger.error(
                "harvester_pnl_resolver: _settle_positions failed for %s %s: %s",
                city_name, target_date, exc,
            )
            errors += 1

    # Write decision_log if we have settlement records.
    decision_log_rows_written = 0
    if settlement_records:
        try:
            store_settlement_records(trade_conn, settlement_records, source="harvester_pnl_resolver")
            decision_log_rows_written = len(settlement_records)
        except Exception as exc:
            logger.error("harvester_pnl_resolver: store_settlement_records failed: %s", exc)
            errors += 1

    # DT#1 / INV-17: DB commits first, then JSON exports.
    _portfolio_settled = positions_settled > 0
    _tracker_dirty = tracker_dirty

    def _db_op() -> None:
        trade_conn.commit()

    def _export_portfolio() -> None:
        if _portfolio_settled:
            save_portfolio(portfolio, source="harvester_pnl_resolver")

    def _export_tracker() -> None:
        if _tracker_dirty:
            save_tracker(tracker)

    try:
        commit_then_export(
            trade_conn,
            db_op=_db_op,
            json_exports=[_export_portfolio, _export_tracker],
        )
    except Exception as exc:
        logger.error("harvester_pnl_resolver: commit_then_export failed: %s", exc)
        errors += 1

    return {
        "status": "ok",
        "positions_settled": positions_settled,
        "decision_log_rows_written": decision_log_rows_written,
        "errors": errors,
        "settlements_checked": len(rows),
    }
