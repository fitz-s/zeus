# Lifecycle: created=2026-04-30; last_reviewed=2026-08-13; last_reused=2026-08-13
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
- May import from src.execution.harvester (trading side, no circular reference).
- Does NOT import from src.ingest_main or scripts.ingest.*.

K1 (2026-05-11): settlements moved from zeus-world.db to zeus-forecasts.db.
W2 (2026-06-03): reader repointed from legacy settlements → canonical settlement_outcomes.
Callers pass get_forecasts_connection() as the second argument.
"""

from __future__ import annotations

import copy
import logging

logger = logging.getLogger(__name__)

_FINALIZED_PAYOUT_SOURCE = "chain_rpc_finalized_v1"
_TERMINAL_PAYOUT_STATES = frozenset({"RESOLVED_ZERO", "RESOLVED_NONZERO"})


def _row_value(row, key: str, index: int, default=None):
    if hasattr(row, "keys"):
        return row[key] if key in row.keys() else default
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
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


def _read_finalized_payout_settlement_rows(trade_conn, portfolio, keys):
    """Translate complete finalized CTF payouts into condition-scoped closes.

    This is economic payout truth only.  It never creates a physical
    temperature, family winner, forecast settlement row, or calibration fact.
    CTF binary outcome slots are YES=0 and NO=1 by the observer's explicit
    adapter contract; the executable snapshot must independently bind those
    labels to the same YES/NO tokens carried by every open position before the
    payout can authorize a close.

    The observer appends only when a value changes, so the two latest outcome
    facts need not share a block.  Each fact must independently be finalized,
    complete, and terminal; together they must form the strict binary vector
    ``[denominator, 0]`` or ``[0, denominator]``.
    """
    positions_by_condition: dict[str, list] = {}
    for pos in getattr(portfolio, "positions", []) or []:
        key = (
            str(getattr(pos, "city", "") or "").strip(),
            str(getattr(pos, "target_date", "") or "").strip(),
            str(getattr(pos, "temperature_metric", "") or "high").strip().lower(),
        )
        condition_id = str(getattr(pos, "condition_id", "") or "").strip()
        if key in keys and condition_id:
            positions_by_condition.setdefault(condition_id, []).append(pos)
    if not positions_by_condition:
        return []

    condition_ids = sorted(positions_by_condition)
    placeholders = ",".join("?" for _ in condition_ids)
    payout_rows = trade_conn.execute(
        f"""
        WITH latest AS (
            SELECT condition_id, outcome_index, payout_numerator,
                   payout_denominator, state, block_number, block_hash, source,
                   observed_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY condition_id, outcome_index ORDER BY id DESC
                   ) AS row_rank
              FROM payout_observations
             WHERE condition_id IN ({placeholders})
               AND outcome_index IN (0, 1)
        )
        SELECT condition_id, outcome_index, payout_numerator,
               payout_denominator, state, block_number, block_hash, source,
               observed_at
          FROM latest
         WHERE row_rank = 1
         ORDER BY condition_id, outcome_index
        """,
        condition_ids,
    ).fetchall()
    snapshot_rows = trade_conn.execute(
        f"""
        WITH latest AS (
            SELECT condition_id, yes_token_id, no_token_id, event_slug,
                   ROW_NUMBER() OVER (
                       PARTITION BY condition_id ORDER BY captured_at DESC
                   ) AS row_rank
              FROM executable_market_snapshots
             WHERE condition_id IN ({placeholders})
        )
        SELECT condition_id, yes_token_id, no_token_id, event_slug
          FROM latest
         WHERE row_rank = 1
        """,
        condition_ids,
    ).fetchall()

    observations: dict[str, dict[int, object]] = {}
    for row in payout_rows:
        condition_id = str(_row_value(row, "condition_id", 0, "") or "")
        outcome_index = int(_row_value(row, "outcome_index", 1, -1))
        observations.setdefault(condition_id, {})[outcome_index] = row
    snapshots = {
        str(_row_value(row, "condition_id", 0, "") or ""): row
        for row in snapshot_rows
    }

    resolved = []
    for condition_id, positions in positions_by_condition.items():
        by_index = observations.get(condition_id, {})
        if set(by_index) != {0, 1}:
            continue
        rows = [by_index[0], by_index[1]]
        if any(
            str(_row_value(row, "source", 7, "") or "") != _FINALIZED_PAYOUT_SOURCE
            or str(_row_value(row, "state", 4, "") or "") not in _TERMINAL_PAYOUT_STATES
            or _row_value(row, "block_number", 5, None) is None
            or not str(_row_value(row, "block_hash", 6, "") or "").strip()
            for row in rows
        ):
            continue
        try:
            denominators = [int(_row_value(row, "payout_denominator", 3, 0)) for row in rows]
            numerators = [int(_row_value(row, "payout_numerator", 2, -1)) for row in rows]
        except (TypeError, ValueError):
            continue
        if (
            denominators[0] <= 0
            or denominators[0] != denominators[1]
            or sorted(numerators) != [0, denominators[0]]
        ):
            continue

        snapshot = snapshots.get(condition_id)
        if snapshot is None:
            continue
        yes_token = str(_row_value(snapshot, "yes_token_id", 1, "") or "").strip()
        no_token = str(_row_value(snapshot, "no_token_id", 2, "") or "").strip()
        if not yes_token or not no_token or yes_token == no_token:
            continue
        identity_keys = {
            (
                str(getattr(pos, "city", "") or "").strip(),
                str(getattr(pos, "target_date", "") or "").strip(),
                str(getattr(pos, "temperature_metric", "") or "high").strip().lower(),
            )
            for pos in positions
        }
        if len(identity_keys) != 1 or any(
            str(getattr(pos, "token_id", "") or "").strip() != yes_token
            or str(getattr(pos, "no_token_id", "") or "").strip() != no_token
            for pos in positions
        ):
            continue
        city, target_date, metric = next(iter(identity_keys))
        resolved.append({
            "city": city,
            "target_date": target_date,
            "market_slug": str(_row_value(snapshot, "event_slug", 3, "") or ""),
            "winning_bin": None,
            "temperature_metric": metric,
            "authority": "VENUE_RESOLVED",
            "settlement_source": "polymarket_chain_rpc_finalized_v1",
            "settlement_value": None,
            "settlement_scope": "condition",
            "condition_id": condition_id,
            "condition_yes_won": numerators[0] == denominators[0],
        })
    return resolved


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
    seen_family_keys: set[tuple[str, str, str]] = set()
    seen_condition_ids: set[str] = set()
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
            ):
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
            if key not in keys:
                continue

            outcomes = _extract_resolved_market_outcomes(event)
            outcomes_by_condition = {
                outcome.condition_id: outcome for outcome in outcomes
            }
            held_event_ids = {
                condition_id
                for condition_id, event_slug in slugs_by_condition.items()
                if event_slug == slug
            }
            winners = [outcome for outcome in outcomes if outcome.yes_won]

            # A fully closed family with one YES winner supplies the canonical
            # family winning bin and can settle every held sibling in one pass.
            if (
                event.get("closed") is True
                and len(winners) == 1
                and held_event_ids.issubset(outcomes_by_condition)
                and key not in seen_family_keys
            ):
                winner = winners[0]
                winning_bin = _canonical_bin_label(
                    winner.range_low,
                    winner.range_high,
                    city.settlement_unit,
                )
                if winning_bin is not None:
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
                    seen_family_keys.add(key)
                    seen_condition_ids.update(held_event_ids)
                continue

            # Gamma can finalize child binary conditions before flipping the
            # parent weather event to closed. That child payout is exact
            # economic truth for the matching held condition only. Consume it
            # without inventing a family winner or touching unresolved siblings.
            for condition_id in sorted(held_event_ids):
                if condition_id in seen_condition_ids:
                    continue
                outcome = outcomes_by_condition.get(condition_id)
                if outcome is None:
                    continue
                rows.append({
                    "city": city.name,
                    "target_date": target_date,
                    "market_slug": slug,
                    "winning_bin": (
                        _canonical_bin_label(
                            outcome.range_low,
                            outcome.range_high,
                            city.settlement_unit,
                        )
                        if outcome.yes_won
                        else None
                    ),
                    "temperature_metric": metric,
                    "authority": "VENUE_RESOLVED",
                    "settlement_source": "polymarket_gamma",
                    "settlement_value": None,
                    "settlement_scope": "condition",
                    "condition_id": condition_id,
                    "condition_yes_won": bool(outcome.yes_won),
                })
                seen_condition_ids.add(condition_id)
    return rows


def resolve_pnl_for_settled_markets(trade_conn, forecasts_conn) -> dict:
    """Resolve P&L from physical settlement or exact economic payout truth.

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
    # Import trading-side dependencies before reading settlements so the truth
    # query can be keyed by currently open inventory instead of a global recency
    # window that starves older-but-still-open positions during backlog catch-up.
    from src.execution.harvester import _settle_positions
    from src.state.decision_chain import SettlementRecord, store_settlement_records
    from src.state.portfolio import load_portfolio, save_portfolio
    from src.state.strategy_tracker import get_tracker, save_tracker
    from src.state.canonical_write import commit_then_export

    # Reuse the caller's connection. Opening a second trade connection here can
    # pin a read snapshot on one handle and later deadlock its write upgrade on
    # the other under the continuously-writing live daemon.
    portfolio = load_portfolio(
        connection=trade_conn,
        open_positions_only=True,
    )
    settlement_keys = _open_position_settlement_keys(trade_conn, portfolio)

    # Read settled rows from forecasts.settlement_outcomes (VERIFIED authority only).
    # W2 (2026-06-03): repointed from legacy settlements table to canonical settlement_outcomes.
    forecast_read_errors = 0
    try:
        verified_rows = _read_verified_settlement_rows(forecasts_conn, settlement_keys)
    except Exception as exc:
        logger.warning("harvester_pnl_resolver: settlement_outcomes read failed: %s", exc)
        # Forecast truth and finalized chain payout are independent authorities.
        # Preserve the forecast error, but do not let it veto a complete local
        # condition payout that can safely release canonical trade exposure.
        verified_rows = []
        forecast_read_errors = 1

    verified_keys = {
        (
            str(_row_value(row, "city", 0, "") or ""),
            str(_row_value(row, "target_date", 1, "") or ""),
            str(_row_value(row, "temperature_metric", 4, "") or ""),
        )
        for row in verified_rows
    }
    payout_rows = _read_finalized_payout_settlement_rows(
        trade_conn,
        portfolio,
        settlement_keys - verified_keys,
    )
    venue_rows = _read_venue_resolved_settlement_rows(
        trade_conn,
        portfolio,
        settlement_keys - verified_keys,
    )

    # End every discovery/read snapshot before requesting the WAL writer slot.
    # A DEFERRED read transaction cannot be safely upgraded after another live
    # writer advances the WAL (SQLITE_BUSY_SNAPSHOT ignores busy_timeout). Take
    # write authority first, then re-read canonical exposure and finalized local
    # payout truth inside that fresh transaction. No HTTP occurs after this line.
    if trade_conn.in_transaction:
        trade_conn.rollback()
    trade_conn.execute("BEGIN IMMEDIATE")
    portfolio = load_portfolio(
        connection=trade_conn,
        open_positions_only=True,
    )
    settlement_keys = _open_position_settlement_keys(trade_conn, portfolio)
    verified_rows = [
        row
        for row in verified_rows
        if (
            str(_row_value(row, "city", 0, "") or ""),
            str(_row_value(row, "target_date", 1, "") or ""),
            str(_row_value(row, "temperature_metric", 4, "") or ""),
        ) in settlement_keys
    ]
    payout_rows = _read_finalized_payout_settlement_rows(
        trade_conn,
        portfolio,
        settlement_keys - verified_keys,
    )
    current_condition_ids = {
        str(getattr(pos, "condition_id", "") or "").strip()
        for pos in getattr(portfolio, "positions", []) or []
    }
    payout_condition_ids = {
        str(row.get("condition_id") or "").strip() for row in payout_rows
    }
    venue_rows = [
        row
        for row in venue_rows
        if (
            str(row.get("settlement_scope") or "family") == "condition"
            and str(row.get("condition_id") or "").strip() in current_condition_ids
            and str(row.get("condition_id") or "").strip() not in payout_condition_ids
        )
        or (
            str(row.get("settlement_scope") or "family") == "family"
            and (
                str(row.get("city") or ""),
                str(row.get("target_date") or ""),
                str(row.get("temperature_metric") or ""),
            ) in settlement_keys
        )
    ]
    rows = [*verified_rows, *payout_rows, *venue_rows]

    if not rows:
        trade_conn.rollback()
        logger.debug(
            "harvester_pnl_resolver: no VERIFIED rows in forecasts.settlement_outcomes "
            "for open position keys; truth writer may not have run yet"
        )
        return {
            "status": "awaiting_truth_writer",
            "open_position_keys_checked": len(settlement_keys),
            "positions_settled": 0,
            "decision_log_rows_written": 0,
            "errors": forecast_read_errors,
        }

    settlement_records: list[SettlementRecord] = []
    tracker = get_tracker()
    tracker_dirty = False

    positions_settled = 0
    errors = forecast_read_errors
    batch_portfolio_snapshot = copy.deepcopy(portfolio.__dict__)
    batch_tracker_snapshot = copy.deepcopy(getattr(tracker, "__dict__", {}))
    batch_savepoint = "harvester_pnl_resolver_batch"
    trade_conn.execute(f"SAVEPOINT {batch_savepoint}")

    for row_index, row in enumerate(rows):
        city_name = _row_value(row, "city", 0, "")
        target_date = _row_value(row, "target_date", 1, "")
        market_slug = _row_value(row, "market_slug", 2, "")
        winning_bin = _row_value(row, "winning_bin", 3, "")
        temperature_metric = _row_value(row, "temperature_metric", 4, "")
        authority = str(_row_value(row, "authority", 5, "") or "").upper()
        settlement_source = _row_value(row, "settlement_source", 6, "")
        settlement_value = _row_value(row, "settlement_value", 7, None)
        settlement_scope = str(
            _row_value(row, "settlement_scope", 8, "family") or "family"
        ).strip().lower()
        settlement_condition_id = str(
            _row_value(row, "condition_id", 9, "") or ""
        ).strip()
        settlement_condition_yes_won = _row_value(
            row,
            "condition_yes_won",
            10,
            None,
        )

        if not city_name or not target_date:
            continue
        if settlement_scope == "condition":
            if (
                not settlement_condition_id
                or not isinstance(settlement_condition_yes_won, bool)
            ):
                logger.warning(
                    "harvester_pnl_resolver: skipping malformed exact-condition "
                    "settlement row for %s %s condition=%r yes_won=%r",
                    city_name,
                    target_date,
                    settlement_condition_id,
                    settlement_condition_yes_won,
                )
                continue
        elif not winning_bin:
            continue
        if authority not in {"VERIFIED", "VENUE_RESOLVED"}:
            logger.warning(
                "harvester_pnl_resolver: skipping non-VERIFIED settlement row for %s %s: %s",
                city_name, target_date, authority,
            )
            continue

        # A settlement row is one economic unit even when it matches multiple
        # positions.  _settle_positions() has a per-position savepoint for
        # partial-exit economics, but that is too narrow: a later matching
        # position can still fail after an earlier one has closed.  Keep the
        # database and all mutable resolver state at the same row boundary.
        portfolio_snapshot = copy.deepcopy(portfolio.__dict__)
        tracker_snapshot = copy.deepcopy(getattr(tracker, "__dict__", {}))
        settlement_records_start = len(settlement_records)
        positions_settled_before_row = positions_settled
        tracker_dirty_before_row = tracker_dirty
        row_savepoint = f"harvester_pnl_settlement_row_{row_index}"
        trade_conn.execute(f"SAVEPOINT {row_savepoint}")
        row_failed = False
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
                    else (
                        "trades.payout_observations"
                        if str(settlement_source or "")
                        == "polymarket_chain_rpc_finalized_v1"
                        else (
                        "gamma_exact_held_condition"
                        if settlement_scope == "condition"
                        else "gamma_exact_held_event"
                        )
                    )
                ),
                settlement_market_slug=str(market_slug or ""),
                settlement_temperature_metric=str(temperature_metric or ""),
                settlement_source=str(settlement_source or ""),
                settlement_value=settlement_value,
                settlement_condition_id=settlement_condition_id,
                settlement_condition_yes_won=settlement_condition_yes_won,
            )
            positions_settled += n_settled
            if n_settled > 0:
                tracker_dirty = True
        except Exception as exc:
            row_failed = True
            trade_conn.execute(f"ROLLBACK TO SAVEPOINT {row_savepoint}")
            trade_conn.execute(f"RELEASE SAVEPOINT {row_savepoint}")
            portfolio.__dict__.clear()
            portfolio.__dict__.update(portfolio_snapshot)
            tracker.__dict__.clear()
            tracker.__dict__.update(tracker_snapshot)
            del settlement_records[settlement_records_start:]
            positions_settled = positions_settled_before_row
            tracker_dirty = tracker_dirty_before_row
            logger.error(
                "harvester_pnl_resolver: rolled back settlement row %s for %s %s: %s",
                row_index,
                city_name,
                target_date,
                exc,
            )
            errors += 1
        finally:
            if not row_failed:
                trade_conn.execute(f"RELEASE SAVEPOINT {row_savepoint}")

    # Write decision_log if we have settlement records.
    decision_log_rows_written = 0
    if settlement_records:
        try:
            store_settlement_records(trade_conn, settlement_records, source="harvester_pnl_resolver")
            decision_log_rows_written = len(settlement_records)
        except Exception as exc:
            logger.error("harvester_pnl_resolver: store_settlement_records failed: %s", exc)
            trade_conn.execute(f"ROLLBACK TO SAVEPOINT {batch_savepoint}")
            trade_conn.execute(f"RELEASE SAVEPOINT {batch_savepoint}")
            portfolio.__dict__.clear()
            portfolio.__dict__.update(batch_portfolio_snapshot)
            tracker.__dict__.clear()
            tracker.__dict__.update(batch_tracker_snapshot)
            settlement_records.clear()
            positions_settled = 0
            tracker_dirty = False
            errors += 1
        else:
            trade_conn.execute(f"RELEASE SAVEPOINT {batch_savepoint}")
    else:
        trade_conn.execute(f"RELEASE SAVEPOINT {batch_savepoint}")

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
