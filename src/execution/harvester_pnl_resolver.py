# Lifecycle: created=2026-04-30; last_reviewed=2026-06-03; last_reused=2026-06-03
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §5 Phase 1.5; docs/archive/2026-Q2/task_2026-05-16_deep_alignment_audit/REPORT.md Finding #4
# W2 (2026-06-03): repointed from forecasts.settlements → forecasts.settlement_outcomes.
"""Trading-side P&L resolver (Phase 1.5 harvester split).

Reads forecasts.settlement_outcomes and exact Gamma resolutions for held events.
Writes trade.decision_log via store_settlement_records() and settles positions
via _settle_positions() — both are trading-side operations. Gamma resolution is
economic payout truth only; it never writes or grades forecast observations.

Design invariants:
- Does NOT write to settlements, settlement_outcomes, market_events, or any forecast table.
- If neither forecast truth nor an exact held-event payout is resolved, returns
  awaiting_truth_writer status.
- Feature-flagged: ZEUS_HARVESTER_LIVE_ENABLED must equal "1" or function is a no-op.
- May import from src.execution.harvester (trading side, no circular reference).
- Does NOT import from src.ingest_main or scripts.ingest.*.

K1 (2026-05-11): settlements moved from zeus-world.db to zeus-forecasts.db.
W2 (2026-06-03): reader repointed from legacy settlements → canonical settlement_outcomes.
Callers pass get_forecasts_connection() as the second argument.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _row_value(row, key: str, index: int, default=None):
    if hasattr(row, "keys") and key in row.keys():
        return row[key]
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _portfolio_settlement_keys(portfolio) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for pos in getattr(portfolio, "positions", []) or []:
        city = str(getattr(pos, "city", "") or "").strip()
        target_date = str(getattr(pos, "target_date", "") or "").strip()
        metric = str(getattr(pos, "temperature_metric", "") or "high").strip().lower()
        if city and target_date and metric in {"high", "low"}:
            keys.add((city, target_date, metric))
    return keys


def _open_position_settlement_keys(trade_conn, portfolio) -> set[tuple[str, str, str]]:
    """Return settlement keys that currently have non-terminal trade inventory.

    The resolver used to scan only the newest settlement rows. During backlog
    catch-up, a live position can sit far outside that global recency window and
    never settle. Keying the truth read from open trade inventory makes the
    resolver consume the exact markets that matter without broad historical scans.
    """
    keys: set[tuple[str, str, str]] = set()
    try:
        rows = trade_conn.execute(
            """
            SELECT DISTINCT city, target_date, COALESCE(temperature_metric, 'high') AS temperature_metric
            FROM position_current
            WHERE phase IN ('active', 'day0_window', 'pending_exit', 'economically_closed')
            """
        ).fetchall()
        for row in rows:
            city = str(_row_value(row, "city", 0, "") or "").strip()
            target_date = str(_row_value(row, "target_date", 1, "") or "").strip()
            metric = str(_row_value(row, "temperature_metric", 2, "high") or "high").strip().lower()
            if city and target_date and metric in {"high", "low"}:
                keys.add((city, target_date, metric))
    except Exception as exc:
        logger.warning(
            "harvester_pnl_resolver: open position key query failed; falling back to portfolio keys: %s",
            exc,
        )

    return keys or _portfolio_settlement_keys(portfolio)


def _read_verified_settlement_rows(forecasts_conn, keys: set[tuple[str, str, str]]):
    if not keys:
        return []
    rows = []
    key_list = sorted(keys)
    batch_size = 250
    for offset in range(0, len(key_list), batch_size):
        batch = key_list[offset: offset + batch_size]
        placeholders = ",".join(["(?, ?, ?)"] * len(batch))
        params: list[str] = []
        for city, target_date, metric in batch:
            params.extend([city, target_date, metric])
        rows.extend(
            forecasts_conn.execute(
                f"""
                SELECT city, target_date, market_slug, winning_bin, temperature_metric,
                       authority, settlement_source, settlement_value
                FROM settlement_outcomes
                WHERE authority = 'VERIFIED'
                  AND (city, target_date, COALESCE(temperature_metric, 'high')) IN ({placeholders})
                ORDER BY settled_at DESC
                """,
                params,
            ).fetchall()
        )
    return rows


def _read_venue_resolved_settlement_rows(trade_conn, portfolio, keys):
    """Resolve exact held events for economic P&L without grading observations.

    Venue payout and physical-observation quality are separate facts. A missing
    or disputed hourly observation must stay out of calibration, but it cannot
    keep a position open after Gamma publishes an unambiguous binary payout.
    """
    if not keys:
        return []

    positions = [
        pos
        for pos in getattr(portfolio, "positions", []) or []
        if (
            str(getattr(pos, "city", "") or "").strip(),
            str(getattr(pos, "target_date", "") or "").strip(),
            str(getattr(pos, "temperature_metric", "") or "high").strip().lower(),
        ) in keys
        and str(getattr(pos, "condition_id", "") or "").strip()
    ]
    condition_ids = {
        str(getattr(pos, "condition_id", "") or "").strip() for pos in positions
    }
    if not condition_ids:
        return []

    placeholders = ",".join("?" for _ in condition_ids)
    try:
        snapshot_rows = trade_conn.execute(
            f"""
            SELECT condition_id, event_slug
            FROM executable_market_snapshots
            WHERE condition_id IN ({placeholders})
              AND event_slug IS NOT NULL
              AND event_slug != ''
            ORDER BY captured_at DESC
            """,
            sorted(condition_ids),
        ).fetchall()
    except Exception as exc:
        logger.warning("venue settlement slug lookup failed: %s", exc)
        return []

    slugs_by_condition: dict[str, str] = {}
    for row in snapshot_rows:
        condition_id = str(_row_value(row, "condition_id", 0, "") or "")
        slug = str(_row_value(row, "event_slug", 1, "") or "")
        if condition_id and slug and condition_id not in slugs_by_condition:
            slugs_by_condition[condition_id] = slug

    from src.data.market_scanner import GAMMA_BASE, _match_city, infer_temperature_metric
    from src.execution.harvester import (
        _canonical_bin_label,
        _extract_resolved_market_outcomes,
        _extract_target_date,
    )
    import httpx

    rows = []
    seen_keys: set[tuple[str, str, str]] = set()
    for slug in dict.fromkeys(slugs_by_condition.values()):
        try:
            response = httpx.get(
                f"{GAMMA_BASE}/events",
                params={"slug": slug},
                timeout=15.0,
            )
            response.raise_for_status()
            events = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("exact venue settlement fetch failed slug=%s: %s", slug, exc)
            continue
        if not isinstance(events, list):
            continue
        for event in events:
            if (
                not isinstance(event, dict)
                or str(event.get("slug") or "") != slug
                or event.get("closed") is not True
            ):
                continue
            outcomes = _extract_resolved_market_outcomes(event)
            winners = [outcome for outcome in outcomes if outcome.yes_won]
            if len(winners) != 1:
                continue
            event_condition_ids = {outcome.condition_id for outcome in outcomes}
            held_event_ids = {
                condition_id
                for condition_id, event_slug in slugs_by_condition.items()
                if event_slug == slug
            }
            if not held_event_ids.issubset(event_condition_ids):
                continue

            city = _match_city(
                str(event.get("title") or "").lower(),
                slug,
            )
            target_date = _extract_target_date(event)
            if city is None or target_date is None:
                continue
            metric = infer_temperature_metric(
                event.get("title", ""),
                slug,
                *[
                    str(market.get("question") or market.get("groupItemTitle") or "")
                    for market in event.get("markets", []) or []
                ],
            )
            key = (city.name, target_date, metric)
            if key not in keys or key in seen_keys:
                continue
            winner = winners[0]
            winning_bin = _canonical_bin_label(
                winner.range_low,
                winner.range_high,
                city.settlement_unit,
            )
            if winning_bin is None:
                continue
            rows.append({
                "city": city.name,
                "target_date": target_date,
                "market_slug": slug,
                "winning_bin": winning_bin,
                "temperature_metric": metric,
                "authority": "VENUE_RESOLVED",
                "settlement_source": "polymarket_gamma",
                "settlement_value": None,
            })
            seen_keys.add(key)
    return rows


def resolve_pnl_for_settled_markets(trade_conn, forecasts_conn) -> dict:
    """Resolve P&L for markets that have been settled in forecasts.settlements.

    Reads settled rows from forecasts.settlements that have not yet been processed
    by the trading side. Settles matching positions and writes decision_log rows.

    Parameters
    ----------
    trade_conn:
        Connection returned by get_trade_connection(). All trade-side writes go here.
    forecasts_conn:
        Connection returned by get_forecasts_connection(). Read-only access to settlements.
        K1 (2026-05-11): settlements moved from zeus-world.db to zeus-forecasts.db.

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

    # Import trading-side dependencies before reading settlements so the truth
    # query can be keyed by currently open inventory instead of a global recency
    # window that starves older-but-still-open positions during backlog catch-up.
    from src.execution.harvester import _settle_positions
    from src.state.decision_chain import SettlementRecord, store_settlement_records
    from src.state.portfolio import load_portfolio, save_portfolio
    from src.state.strategy_tracker import get_tracker, save_tracker
    from src.state.canonical_write import commit_then_export

    portfolio = load_portfolio()
    settlement_keys = _open_position_settlement_keys(trade_conn, portfolio)

    # Read settled rows from forecasts.settlement_outcomes (VERIFIED authority only).
    # W2 (2026-06-03): repointed from legacy settlements table to canonical settlement_outcomes.
    try:
        verified_rows = _read_verified_settlement_rows(forecasts_conn, settlement_keys)
    except Exception as exc:
        logger.warning("harvester_pnl_resolver: settlement_outcomes read failed: %s", exc)
        return {
            "status": "settlement_outcomes_read_error",
            "error": str(exc),
            "positions_settled": 0,
            "decision_log_rows_written": 0,
            "errors": 1,
        }

    verified_keys = {
        (
            str(_row_value(row, "city", 0, "") or ""),
            str(_row_value(row, "target_date", 1, "") or ""),
            str(_row_value(row, "temperature_metric", 4, "") or ""),
        )
        for row in verified_rows
    }
    venue_rows = _read_venue_resolved_settlement_rows(
        trade_conn,
        portfolio,
        settlement_keys - verified_keys,
    )
    rows = [*verified_rows, *venue_rows]

    if not rows:
        logger.debug(
            "harvester_pnl_resolver: no VERIFIED rows in forecasts.settlement_outcomes "
            "for open position keys; truth writer may not have run yet"
        )
        return {
            "status": "awaiting_truth_writer",
            "open_position_keys_checked": len(settlement_keys),
            "positions_settled": 0,
            "decision_log_rows_written": 0,
            "errors": 0,
        }

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
        if authority not in {"VERIFIED", "VENUE_RESOLVED"}:
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
                settlement_truth_source=(
                    "forecasts.settlement_outcomes"
                    if authority == "VERIFIED"
                    else "gamma_exact_held_event"
                ),
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
        "venue_resolutions_checked": len(venue_rows),
        "open_position_keys_checked": len(settlement_keys),
    }
