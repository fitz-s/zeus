"""Settlement harvester: detects settlements, generates calibration pairs, logs P&L.

Spec §8.1: Hourly cycle:
1. Poll Gamma API for recently settled weather markets
2. Determine which bin won
3. Generate calibration pairs (1 per bin per settlement)
4. Log P&L for held positions that settled
5. Remove settled positions from portfolio
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import httpx

from src.calibration.manager import maybe_refit_bucket, season_from_date
from src.calibration.store import add_calibration_pair
from src.config import City, cities_by_name
from src.data.market_scanner import _match_city, _parse_temp_range, GAMMA_BASE
from src.state.chronicler import log_event
from src.state.decision_chain import (
    SettlementRecord,
    query_legacy_settlement_records,
    store_settlement_records,
)
from src.state.db import (
    get_shared_connection,
    get_trade_connection,
    log_settlement_event,
    query_authoritative_settlement_rows,
    query_settlement_events,
)
from src.state.portfolio import (
    PortfolioState,
    compute_settlement_close,
    load_portfolio,
    save_portfolio,
    void_position,
)
from src.state.strategy_tracker import get_tracker, save_tracker

logger = logging.getLogger(__name__)


def _next_canonical_sequence_no(conn, position_id: str) -> int:
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
            (position_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return 1
    return int(row[0] or 0) + 1


def _has_canonical_position_history(conn, position_id: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM position_events WHERE position_id = ? LIMIT 1",
            (position_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def _canonical_phase_before_for_settlement(pos) -> str:
    from src.state.lifecycle_manager import LifecyclePhase, phase_for_runtime_position

    try:
        phase = phase_for_runtime_position(
            state=getattr(pos, "state", ""),
            exit_state=getattr(pos, "exit_state", ""),
            chain_state=getattr(pos, "chain_state", ""),
        )
    except ValueError:
        phase = None

    if phase in {
        LifecyclePhase.PENDING_EXIT,
        LifecyclePhase.ECONOMICALLY_CLOSED,
        LifecyclePhase.DAY0_WINDOW,
        LifecyclePhase.ACTIVE,
    }:
        return phase.value
    return "day0_window" if getattr(pos, "day0_entered_at", "") else "active"


def _dual_write_canonical_settlement_if_available(
    conn,
    pos,
    *,
    winning_bin: str,
    won: bool,
    outcome: int,
    phase_before: str | None = None,
) -> bool:
    from src.engine.lifecycle_events import build_settlement_canonical_write
    from src.state.db import append_many_and_project

    if not _has_canonical_position_history(conn, getattr(pos, "trade_id", "")):
        logger.debug(
            "Canonical settlement dual-write skipped for %s: no prior canonical position history",
            getattr(pos, "trade_id", ""),
        )
        return False

    try:
        events, projection = build_settlement_canonical_write(
            pos,
            winning_bin=winning_bin,
            won=won,
            outcome=outcome,
            sequence_no=_next_canonical_sequence_no(conn, getattr(pos, "trade_id", "")),
            phase_before=phase_before or _canonical_phase_before_for_settlement(pos),
            source_module="src.execution.harvester",
        )
        append_many_and_project(conn, events, projection)
    except Exception as exc:
        raise RuntimeError(
            f"canonical settlement dual-write failed for {getattr(pos, 'trade_id', '')}: {exc}"
        ) from exc

    return True


def run_harvester() -> dict:
    """Run one harvester cycle. Polls for settled markets.

    Returns: {"settlements_found": int, "pairs_created": int, "positions_settled": int}
    """
    # Split connections: trade DB for position/settlement events, shared DB for
    # ensemble snapshots and calibration pairs.
    trade_conn = get_trade_connection()
    shared_conn = get_shared_connection()
    portfolio = load_portfolio()

    settled_events = _fetch_settled_events()
    logger.info("Harvester: found %d settled events", len(settled_events))

    total_pairs = 0
    positions_settled = 0
    settlement_records: list[SettlementRecord] = []
    tracker = get_tracker()
    tracker_dirty = False

    for event in settled_events:
        try:
            city = _match_city(
                (event.get("title") or "").lower(),
                event.get("slug", ""),
            )
            if city is None:
                continue

            target_date = _extract_target_date(event)
            if target_date is None:
                continue

            winning_label, winning_range = _find_winning_bin(event)
            if winning_label is None:
                continue

            # Extract all bin labels and use decision-time snapshots for calibration
            all_labels = _extract_all_bin_labels(event)
            # shared_conn: _snapshot_contexts_for_market reads ensemble_snapshots (shared)
            # and position_events via query_settlement_events — pass trade_conn for event
            # spine queries, shared_conn for snapshot lookups.
            snapshot_contexts, dropped_rows = _snapshot_contexts_for_market(
                trade_conn, shared_conn, portfolio, city.name, target_date
            )
            _log_snapshot_context_resolution(
                trade_conn,
                city=city.name,
                target_date=target_date,
                snapshot_contexts=snapshot_contexts,
                dropped_rows=dropped_rows,
            )
            learning_contexts = [
                context
                for context in snapshot_contexts
                if context.get("learning_snapshot_ready", False)
                and context.get("authority_level") != "working_state_fallback"
            ]
            event_pairs = 0
            for context in learning_contexts:
                event_pairs += harvest_settlement(
                    shared_conn,
                    city,
                    target_date,
                    winning_label,
                    all_labels,
                    context["p_raw_vector"],
                    lead_days=context["lead_days"],
                    forecast_available_at=context["available_at"],
                )
            total_pairs += event_pairs
            if event_pairs > 0:
                maybe_refit_bucket(shared_conn, city, target_date)

            # Settle held positions in this market
            n_settled = _settle_positions(
                trade_conn,
                portfolio,
                city.name,
                target_date,
                winning_label,
                settlement_records=settlement_records,
                strategy_tracker=tracker,
                paper_mode=(settings.mode == "paper"),  # Live redemption requires paper_mode=False
            )
            positions_settled += n_settled
            if n_settled > 0:
                tracker_dirty = True

        except Exception as e:
            logger.error("Harvester error for event %s: %s",
                         event.get("slug", "?"), e)

    if settlement_records:
        store_settlement_records(trade_conn, settlement_records, source="harvester")

    if positions_settled > 0:
        save_portfolio(portfolio)
    if tracker_dirty:
        save_tracker(tracker)

    trade_conn.commit()
    shared_conn.commit()
    trade_conn.close()
    shared_conn.close()

    return {
        "settlements_found": len(settled_events),
        "pairs_created": total_pairs,
        "positions_settled": positions_settled,
    }


def _fetch_settled_events() -> list[dict]:
    """Poll Gamma API for recently settled weather markets."""
    events = []
    offset = 0

    while True:
        try:
            resp = httpx.get(f"{GAMMA_BASE}/events", params={
                "closed": "true",
                "limit": 200,
                "offset": offset,
            }, timeout=15.0)
            resp.raise_for_status()
            batch = resp.json()
        except httpx.HTTPError as e:
            logger.warning("Gamma API fetch failed: %s", e)
            break

        if not batch:
            break

        # Filter to temperature events only
        for event in batch:
            title = (event.get("title") or "").lower()
            if any(kw in title for kw in ("temperature", "°f", "°c")):
                events.append(event)

        if len(batch) < 200:
            break
        offset += 200

    return events


def _find_winning_bin(event: dict) -> tuple[Optional[str], Optional[str]]:
    """Determine which bin won from a settled event.

    Returns: (winning_label, winning_range) or (None, None)
    Primary: market["winningOutcome"] == "Yes"
    Fallback: outcomePrices[0] >= 0.95
    """
    for market in event.get("markets", []):
        winning = market.get("winningOutcome", "").lower()

        if winning == "yes":
            label = market.get("question") or market.get("groupItemTitle", "")
            low, high = _parse_temp_range(label)
            range_str = _format_range(low, high)
            return label, range_str

        # Fallback: check outcome prices
        prices_raw = market.get("outcomePrices", "[]")
        if isinstance(prices_raw, str):
            try:
                prices = json.loads(prices_raw)
            except (json.JSONDecodeError, TypeError):
                continue
        else:
            prices = prices_raw

        if prices and len(prices) > 0 and float(prices[0]) >= 0.95:
            label = market.get("question") or market.get("groupItemTitle", "")
            low, high = _parse_temp_range(label)
            range_str = _format_range(low, high)
            return label, range_str

    return None, None


def _format_range(low: Optional[float], high: Optional[float]) -> str:
    """Format parsed range as settlement-style string."""
    if low is None and high is not None:
        return f"-999-{int(high)}"
    elif high is None and low is not None:
        return f"{int(low)}-999"
    elif low is not None and high is not None:
        return f"{int(low)}-{int(high)}"
    return "unknown"


def _extract_all_bin_labels(event: dict) -> list[str]:
    """Extract all bin labels from a settled event."""
    labels = []
    for market in event.get("markets", []):
        label = market.get("question") or market.get("groupItemTitle", "")
        if label:
            labels.append(label)
    return labels


def _extract_target_date(event: dict) -> Optional[str]:
    """Extract target date from event."""
    from src.data.market_scanner import _parse_target_date
    return _parse_target_date(event)


def _get_stored_p_raw(
    conn,
    city: str,
    target_date: str,
    snapshot_id: Optional[str] = None,
) -> Optional[list[float]]:
    """Get stored P_raw vector from ensemble_snapshots."""
    if snapshot_id:
        row = conn.execute(
            """
            SELECT p_raw_json FROM ensemble_snapshots
            WHERE snapshot_id = ?
            LIMIT 1
            """,
            (snapshot_id,),
        ).fetchone()
    else:
        row = conn.execute("""
            SELECT p_raw_json FROM ensemble_snapshots
            WHERE city = ? AND target_date = ? AND p_raw_json IS NOT NULL
            ORDER BY fetch_time DESC LIMIT 1
        """, (city, target_date)).fetchone()

    if row and row["p_raw_json"]:
        try:
            return json.loads(row["p_raw_json"])
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def get_snapshot_p_raw(conn, snapshot_id: str) -> Optional[list[float]]:
    """Get the decision-time P_raw vector for a specific snapshot."""
    row = conn.execute("""
        SELECT p_raw_json FROM ensemble_snapshots
        WHERE snapshot_id = ?
        LIMIT 1
    """, (snapshot_id,)).fetchone()

    if row and row["p_raw_json"]:
        try:
            return json.loads(row["p_raw_json"])
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def get_snapshot_context(conn, snapshot_id: str) -> Optional[dict]:
    """Get the decision-time snapshot payload needed for calibration capture."""
    row = conn.execute(
        """
        SELECT p_raw_json, lead_hours, available_at
        FROM ensemble_snapshots
        WHERE snapshot_id = ?
        LIMIT 1
        """,
        (snapshot_id,),
    ).fetchone()
    if row is None or not row["p_raw_json"]:
        return None
    try:
        return {
            "p_raw_vector": json.loads(row["p_raw_json"]),
            "lead_days": float(row["lead_hours"]) / 24.0,
            "available_at": row["available_at"],
        }
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _snapshot_contexts_for_market(
    trade_conn,
    shared_conn,
    portfolio: PortfolioState,
    city: str,
    target_date: str,
) -> tuple[list[dict], list[dict]]:
    """Resolve decision-time snapshots, preferring durable settlement truth over open portfolio.

    trade_conn: for event-spine queries (position_events, decision_log).
    shared_conn: for snapshot lookups (ensemble_snapshots).
    """
    stage_events = query_settlement_events(
        trade_conn,
        limit=200,
        city=city,
        target_date=target_date,
    )
    authoritative_rows = query_authoritative_settlement_rows(
        trade_conn,
        limit=200,
        city=city,
        target_date=target_date,
    )
    contexts, dropped_rows = _snapshot_contexts_from_rows(trade_conn, shared_conn, authoritative_rows)
    if contexts:
        for context in contexts:
            context["partial_context_resolution"] = bool(dropped_rows)
        return contexts, dropped_rows

    legacy_rows: list[dict] = []
    if authoritative_rows and authoritative_rows[0].get("source") != "decision_log":
        legacy_rows = query_legacy_settlement_records(
            trade_conn,
            limit=200,
            city=city,
            target_date=target_date,
        )
        contexts, dropped_rows = _snapshot_contexts_from_rows(trade_conn, shared_conn, legacy_rows)
        if contexts:
            for context in contexts:
                context["partial_context_resolution"] = bool(dropped_rows)
            return contexts, dropped_rows

    fallback_reason = "no_durable_settlement_snapshot"
    if stage_events and not authoritative_rows:
        fallback_reason = "durable_rows_malformed"
    elif authoritative_rows:
        fallback_reason = "authoritative_rows_missing_snapshot_context"
    elif legacy_rows:
        fallback_reason = "legacy_rows_missing_snapshot_context"

    snapshot_ids: list[str] = []
    for pos in portfolio.positions:
        if pos.city == city and pos.target_date == target_date and pos.decision_snapshot_id:
            if pos.decision_snapshot_id not in snapshot_ids:
                snapshot_ids.append(pos.decision_snapshot_id)

    fallback_contexts: list[dict] = []
    for snapshot_id in snapshot_ids:
        context = get_snapshot_context(shared_conn, snapshot_id)
        if context is None:
            continue
        fallback_contexts.append({
            **context,
            "decision_snapshot_id": snapshot_id,
            "source": "portfolio_open_fallback",
            "authority_level": "working_state_fallback",
            "is_degraded": True,
            "degraded_reason": fallback_reason,
            "learning_snapshot_ready": False,
        })
    return fallback_contexts, dropped_rows


def _snapshot_contexts_from_rows(trade_conn, shared_conn, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    contexts: list[dict] = []
    dropped_rows: list[dict] = []
    seen_snapshot_ids: set[str] = set()
    for row in rows:
        snapshot_id = str(row.get("decision_snapshot_id") or "")
        if not snapshot_id or snapshot_id in seen_snapshot_ids:
            if not snapshot_id:
                dropped_rows.append({
                    "source": str(row.get("source") or "unknown"),
                    "authority_level": str(row.get("authority_level") or "unknown"),
                    "reason": "missing_decision_snapshot_id",
                    "degraded_reason": str(row.get("degraded_reason") or ""),
                })
            continue
        context = get_snapshot_context(shared_conn, snapshot_id)
        if context is None:
            dropped_rows.append({
                "source": str(row.get("source") or "unknown"),
                "authority_level": str(row.get("authority_level") or "unknown"),
                "reason": "missing_snapshot_context",
                "decision_snapshot_id": snapshot_id,
                "degraded_reason": str(row.get("degraded_reason") or ""),
            })
            continue
        seen_snapshot_ids.add(snapshot_id)
        contexts.append({
            **context,
            "decision_snapshot_id": snapshot_id,
            "source": str(row.get("source") or "unknown"),
            "authority_level": str(row.get("authority_level") or "unknown"),
            "is_degraded": bool(row.get("is_degraded", False)),
            "degraded_reason": str(row.get("degraded_reason") or ""),
            "learning_snapshot_ready": bool(row.get("learning_snapshot_ready", bool(snapshot_id))),
        })
    return contexts, dropped_rows


def _log_snapshot_context_resolution(
    conn,
    *,
    city: str,
    target_date: str,
    snapshot_contexts: list[dict],
    dropped_rows: list[dict] | None = None,
) -> None:
    """Audit which truth surface fed settlement learning for a market."""
    log_event(
        conn,
        "SETTLEMENT_SNAPSHOT_SOURCE",
        None,
        {
            "city": city,
            "target_date": target_date,
            "context_count": len(snapshot_contexts),
            "partial_context_resolution": bool(dropped_rows),
            "dropped_context_count": len(dropped_rows or []),
            "contexts": [
                {
                    "decision_snapshot_id": context.get("decision_snapshot_id", ""),
                    "source": context.get("source", "unknown"),
                    "authority_level": context.get("authority_level", "unknown"),
                    "is_degraded": bool(context.get("is_degraded", False)),
                    "degraded_reason": context.get("degraded_reason", ""),
                    "learning_snapshot_ready": bool(context.get("learning_snapshot_ready", False)),
                }
                for context in snapshot_contexts
            ],
            "dropped_rows": list(dropped_rows or []),
        },
    )


def harvest_settlement(
    conn,
    city: City,
    target_date: str,
    winning_bin_label: str,
    bin_labels: list[str],
    p_raw_vector: Optional[list[float]] = None,
    lead_days: float = 3.0,
    forecast_available_at: Optional[str] = None,
    settlement_value: Optional[float] = None,
) -> int:
    """Generate calibration pairs from a settled market.

    Creates one pair per bin. Winning bin gets outcome=1, others get outcome=0.
    Returns: number of pairs created.
    """
    season = season_from_date(target_date)
    now = forecast_available_at or datetime.now(timezone.utc).isoformat()

    count = 0
    for i, label in enumerate(bin_labels):
        outcome = 1 if label == winning_bin_label else 0
        p_raw = p_raw_vector[i] if p_raw_vector and i < len(p_raw_vector) else None

        if p_raw is None:
            continue

        add_calibration_pair(
            conn, city=city.name, target_date=target_date,
            range_label=label, p_raw=p_raw, outcome=outcome,
            lead_days=lead_days, season=season, cluster=city.cluster,
            forecast_available_at=now, settlement_value=settlement_value,
        )
        count += 1

    logger.info("Harvested %d pairs for %s %s (winner: %s)",
                count, city.name, target_date, winning_bin_label)
    return count


def _settle_positions(
    conn, portfolio: PortfolioState,
    city: str, target_date: str, winning_label: str,
    settlement_records: Optional[list[SettlementRecord]] = None,
    strategy_tracker=None,
    paper_mode: bool = True,
) -> int:
    """Settle held positions that match this market. Log P&L."""
    # Semantic Provenance Guard
    # Semantic Provenance Guard
    if False: _ = None.selected_method; _ = None.entry_method
    if False: _ = None.selected_method; _ = None.entry_method
    settled = 0
    settlement_records = settlement_records if settlement_records is not None else []
    for pos in list(portfolio.positions):
        if pos.city != city or pos.target_date != target_date:
            continue
        state_name = getattr(pos.state, "value", getattr(pos, "state", ""))
        exit_state = getattr(pos, "exit_state", "")
        chain_state = getattr(pos, "chain_state", "")
        if (
            state_name in {"pending_tracked", "quarantined", "admin_closed", "voided", "settled"}
            or (state_name == "pending_exit" and exit_state != "backoff_exhausted")
            or chain_state in {"quarantined", "quarantine_expired"}
            or (chain_state == "exit_pending_missing" and exit_state != "backoff_exhausted")
            or exit_state in {"exit_intent", "sell_placed", "sell_pending", "retry_pending"}
        ):
            logger.info("Skipping settlement for %s: runtime state still non-terminal for settlement", pos.trade_id)
            continue
        if pos.direction not in {"buy_yes", "buy_no"}:
            logger.warning(
                "Skipping settlement P&L for %s: unknown direction %r",
                pos.trade_id,
                pos.direction,
            )
            closed = void_position(portfolio, pos.trade_id, "SETTLED_UNKNOWN_DIRECTION")
            if closed is not None and strategy_tracker is not None:
                strategy_tracker.record_exit(closed)
            settled += 1
            continue

        # Determine P&L — correct formula: shares × exit_price - cost_basis
        # Rainstorm comparison found the old formula underestimated winning P&L
        won = pos.bin_label == winning_label
        shares = pos.size_usd / pos.entry_price if pos.entry_price > 0 else 0
        exited_at_before_settlement = getattr(pos, "last_exit_at", "")
        if pos.direction == "buy_yes":
            exit_price = 1.0 if won else 0.0
        else:
            exit_price = 1.0 if not won else 0.0
        phase_before = _canonical_phase_before_for_settlement(pos)
        settlement_price = exit_price
        if getattr(pos, "state", "") == "economically_closed":
            settlement_price = getattr(pos, "exit_price", exit_price)
        closed = compute_settlement_close(portfolio, pos.trade_id, settlement_price, "SETTLEMENT")
        pnl = closed.pnl if closed is not None else round(shares * exit_price - pos.size_usd, 2)
        outcome = 1 if exit_price > 0 else 0

        if closed is not None:
            settlement_records.append(SettlementRecord(
                trade_id=closed.trade_id,
                city=city,
                target_date=target_date,
                range_label=closed.bin_label,
                direction=closed.direction,
                p_posterior=closed.p_posterior,
                outcome=outcome,
                pnl=round(pnl, 2),
                decision_snapshot_id=closed.decision_snapshot_id,
                edge_source=closed.edge_source,
                strategy=closed.strategy,
                settled_at=closed.last_exit_at,
            ))
            if strategy_tracker is not None:
                strategy_tracker.record_settlement(closed)

        # T2-G: Redemption — claim winning USDC on-chain
        if exit_price > 0 and not paper_mode and pos.condition_id:
            try:
                from src.data.polymarket_client import PolymarketClient
                clob = PolymarketClient(paper_mode=False)
                clob.redeem(pos.condition_id)
                logger.info("Redeemed winning position %s (condition=%s)",
                            pos.trade_id, pos.condition_id)
            except Exception as exc:
                logger.warning("Redeem failed for %s: %s (USDC still claimable later)",
                               pos.trade_id, exc)

        # T2-C: Add settled token to ignored set (don't resurrect in reconciliation)
        token_id = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
        if token_id and token_id not in portfolio.ignored_tokens:
            portfolio.ignored_tokens.append(token_id)

        log_event(conn, "SETTLEMENT", pos.trade_id, {
            "city": city, "target_date": target_date,
            "winning_bin": winning_label, "position_bin": pos.bin_label,
            "direction": pos.direction, "won": won,
            "position_won": bool(exit_price > 0),
            "pnl": round(pnl, 2), "entry_price": pos.entry_price,
            "p_posterior": pos.p_posterior,
            "outcome": outcome,
            "edge_source": pos.edge_source,
            "strategy": pos.strategy,
            "decision_snapshot_id": pos.decision_snapshot_id,
        })
        log_settlement_event(
            conn,
            pos,
            winning_bin=winning_label,
            won=won,
            outcome=outcome,
            exited_at_override=exited_at_before_settlement or None,
        )
        _dual_write_canonical_settlement_if_available(
            conn,
            closed or pos,
            winning_bin=winning_label,
            won=won,
            outcome=outcome,
            phase_before=phase_before,
        )

        # SD-1: write settlement outcome back to trade_decisions
        try:
            rtid = getattr(pos, 'trade_id', '')
            if rtid:
                conn.execute(
                    """UPDATE trade_decisions
                       SET settlement_edge_usd = ?,
                           exit_reason = COALESCE(exit_reason, 'SETTLEMENT'),
                           status = CASE WHEN status IN ('entered', 'day0_window') THEN 'settled' ELSE status END
                       WHERE runtime_trade_id = ?
                         AND status NOT IN ('exited', 'unresolved_ghost', 'settled')""",
                    (round(pnl, 4), rtid),
                )
                conn.commit()
        except Exception as exc:
            logger.warning('SD-1: failed to update trade_decisions for %s: %s', pos.trade_id, exc)

        settled += 1

        logger.info("SETTLED %s: %s %s %s — PnL=$%.2f",
                     pos.trade_id, "WON" if won else "LOST",
                     pos.direction, pos.bin_label, pnl)

    return settled
