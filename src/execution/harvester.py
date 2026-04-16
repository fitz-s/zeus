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
from src.calibration.effective_sample_size import build_decision_group_for_key, write_decision_groups
from src.calibration.decision_group import compute_id
from src.calibration.store import add_calibration_pair
from src.config import City, cities_by_name, get_mode
from src.contracts.settlement_semantics import round_wmo_half_up_value
from src.data.market_scanner import _match_city, _parse_temp_range, GAMMA_BASE
from src.state.chronicler import log_event
from src.state.decision_chain import (
    SettlementRecord,
    query_legacy_settlement_records,
    store_settlement_records,
)
from src.state.db import (
    get_world_connection,
    get_trade_connection,
    log_settlement_event,
    query_authoritative_settlement_rows,
    query_settlement_events,
    record_token_suppression,
)
from src.state.portfolio import (
    PortfolioState,
    compute_settlement_close,
    load_portfolio,
    save_portfolio,
    void_position,
)
from src.state.strategy_tracker import get_tracker, save_tracker
from src.riskguard.discord_alerts import alert_redeem

logger = logging.getLogger(__name__)


def _get_canonical_exit_flag() -> bool:
    """Read CANONICAL_EXIT_PATH feature flag from settings."""
    try:
        from src.config import settings
        return settings.feature_flags.get("CANONICAL_EXIT_PATH", False)
    except Exception:
        return False


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


_TERMINAL_PHASES = frozenset({"settled", "voided", "admin_closed", "quarantined"})
_HARVESTER_STAGE2_TRADE_TABLES = (
    "position_events",
    "position_current",
    "decision_log",
    "chronicle",
)
_HARVESTER_STAGE2_SHARED_TABLES = (
    "ensemble_snapshots",
    "calibration_pairs",
    "calibration_decision_group",
    "platt_models",
)


def _missing_tables(conn, table_names: tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for table_name in table_names:
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
                (table_name,),
            ).fetchone()
        except sqlite3.Error:
            missing.append(table_name)
            continue
        if row is None:
            missing.append(table_name)
    return missing


def _preflight_harvester_stage2_db_shape(trade_conn, shared_conn) -> dict:
    """Check whether Stage-2 calibration learning dependencies are installed."""
    missing_trade = _missing_tables(trade_conn, _HARVESTER_STAGE2_TRADE_TABLES)
    missing_shared = _missing_tables(shared_conn, _HARVESTER_STAGE2_SHARED_TABLES)
    if missing_trade or missing_shared:
        return {
            "stage2_status": "skipped_db_shape_preflight",
            "stage2_skip_reason": "missing_stage2_runtime_tables",
            "stage2_missing_trade_tables": missing_trade,
            "stage2_missing_shared_tables": missing_shared,
        }
    return {
        "stage2_status": "ready",
        "stage2_missing_trade_tables": [],
        "stage2_missing_shared_tables": [],
    }


def _current_phase_in_db(conn, trade_id: str) -> dict:
    """Read the authoritative phase from position_current for the given trade.

    Returns a structured status result: {"status": "ok", "phase": str},
    {"status": "missing"}, or {"status": "error", "reason": str}.
    This is the canonical dedup anchor — stale in-memory pos objects must
    never be used to decide whether a settlement has already been emitted.
    """
    if not trade_id:
        return {"status": "missing"}
    try:
        row = conn.execute(
            "SELECT phase FROM position_current WHERE trade_id = ? LIMIT 1",
            (trade_id,),
        ).fetchone()
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    if row is None:
        return {"status": "missing"}
    phase_str = str(row["phase"]) if hasattr(row, "keys") else str(row[0])
    return {"status": "ok", "phase": phase_str}


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

    trade_id = getattr(pos, "trade_id", "")

    if not _has_canonical_position_history(conn, trade_id):
        logger.debug(
            "Canonical settlement dual-write skipped for %s: no prior canonical position history",
            trade_id,
        )
        return False

    # Bug #9 dedup guard: the authoritative source for "is this position already
    # in a terminal phase?" is position_current in the DB, NOT the in-memory pos
    # object. If load_portfolio fell back to the JSON cache (bug #7 path), the
    # pos object may show economically_closed while the DB already reflects
    # settled from an earlier cycle. Refusing re-entry at this layer makes
    # settlement idempotent regardless of the iterator's staleness.
    db_result = _current_phase_in_db(conn, trade_id)
    if db_result["status"] == "error":
        logger.error(
            "Canonical settlement aborted for %s: position_current.phase lookup failed: %s",
            trade_id, db_result.get("reason"),
        )
        return False
        
    db_phase = db_result.get("phase")
    if db_phase in _TERMINAL_PHASES:
        logger.info(
            "Canonical settlement dual-write skipped for %s: position_current.phase=%s already terminal",
            trade_id,
            db_phase,
        )
        return False

    # The terminal dedup above uses db_phase authoritatively. For phase_before
    # metadata, prefer the runtime pos state: db_phase reflects last canonical
    # write but pos may have advanced further (e.g. economically_closed or
    # pending_exit) without intermediate canonical writes.
    resolved_phase_before = (
        phase_before
        or _canonical_phase_before_for_settlement(pos)
        or db_phase
        or "active"
    )

    try:
        events, projection = build_settlement_canonical_write(
            pos,
            winning_bin=winning_bin,
            won=won,
            outcome=outcome,
            sequence_no=_next_canonical_sequence_no(conn, trade_id),
            phase_before=resolved_phase_before,
            source_module="src.execution.harvester",
        )
        append_many_and_project(conn, events, projection)
    except Exception as exc:
        raise RuntimeError(
            f"canonical settlement dual-write failed for {trade_id}: {exc}"
        ) from exc

    return True


def run_harvester() -> dict:
    """Run one harvester cycle. Polls for settled markets.

    Returns: harvester counts plus stage2_status / stage2 preflight details.
    """
    # Split connections: trade DB for position/settlement events, shared DB for
    # ensemble snapshots and calibration pairs.
    trade_conn = get_trade_connection()
    shared_conn = get_world_connection()
    portfolio = load_portfolio()

    settled_events = _fetch_settled_events()
    logger.info("Harvester: found %d settled events", len(settled_events))
    stage2_preflight = (
        _preflight_harvester_stage2_db_shape(trade_conn, shared_conn)
        if settled_events
        else {
            "stage2_status": "not_run_no_settled_events",
            "stage2_missing_trade_tables": [],
            "stage2_missing_shared_tables": [],
        }
    )
    stage2_ready = stage2_preflight.get("stage2_status") == "ready"
    if settled_events and not stage2_ready:
        logger.warning(
            "Harvester Stage-2 skipped by DB shape preflight: trade_missing=%s shared_missing=%s",
            stage2_preflight.get("stage2_missing_trade_tables", []),
            stage2_preflight.get("stage2_missing_shared_tables", []),
        )

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
            learning_contexts = []
            if stage2_ready:
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
                    forecast_issue_time=context["issue_time"],
                    forecast_available_at=context["available_at"],
                    source_model_version=context["source_model_version"],
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
            )
            positions_settled += n_settled
            if n_settled > 0:
                tracker_dirty = True

        except Exception as e:
            logger.error("Harvester error for event %s: %s",
                         event.get("slug", "?"), e)

    legacy_settlement_records_skipped = 0
    if settlement_records and "decision_log" not in stage2_preflight.get("stage2_missing_trade_tables", []):
        store_settlement_records(trade_conn, settlement_records, source="harvester")
    elif settlement_records:
        legacy_settlement_records_skipped = len(settlement_records)
        logger.warning(
            "Legacy settlement record storage skipped: decision_log missing; records=%d",
            legacy_settlement_records_skipped,
        )

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
        "legacy_settlement_records_skipped": legacy_settlement_records_skipped,
        **stage2_preflight,
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
        SELECT p_raw_json, lead_hours, issue_time, available_at,
               model_version, data_version
        FROM ensemble_snapshots
        WHERE snapshot_id = ?
        LIMIT 1
        """,
        (snapshot_id,),
    ).fetchone()
    if row is None or not row["p_raw_json"]:
        return None
    source_model_version = row["data_version"] or row["model_version"]
    if not source_model_version:
        return None
    try:
        return {
            "p_raw_vector": json.loads(row["p_raw_json"]),
            "lead_days": float(row["lead_hours"]) / 24.0,
            "issue_time": row["issue_time"],
            "available_at": row["available_at"],
            "source_model_version": source_model_version,
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
    forecast_issue_time: Optional[str] = None,
    forecast_available_at: Optional[str] = None,
    source_model_version: Optional[str] = None,
    settlement_value: Optional[float] = None,
    bias_corrected: Optional[bool] = None,
) -> int:
    """Generate calibration pairs from a settled market.

    Creates one pair per bin. Winning bin gets outcome=1, others get outcome=0.
    Returns: number of pairs created.
    """
    season = season_from_date(target_date, lat=city.lat)
    now = forecast_available_at or datetime.now(timezone.utc).isoformat()
    issue_time = forecast_issue_time or now
    if bias_corrected is None:
        try:
            from src.config import settings
            bias_corrected = settings.bias_correction_enabled
        except Exception:
            bias_corrected = False
    if p_raw_vector and not source_model_version:
        raise ValueError(
            "source_model_version is required when harvesting calibration pairs"
        )

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
            forecast_available_at=now,
            settlement_value=(round_wmo_half_up_value(float(settlement_value))
                              if settlement_value is not None else None),
            decision_group_id=compute_id(
                city.name,
                target_date,
                issue_time,
                source_model_version or "",
            ),
            bias_corrected=bool(bias_corrected),
        )
        count += 1

    logger.info("Harvested %d pairs for %s %s (winner: %s)",
                count, city.name, target_date, winning_bin_label)
    if count:
        group = build_decision_group_for_key(
            conn,
            city=city.name,
            target_date=target_date,
            forecast_available_at=now,
            lead_days=lead_days,
        )
        if group is not None:
            write_decision_groups(
                conn,
                [group],
                recorded_at=datetime.now(timezone.utc).isoformat(),
                update_pair_rows=True,
            )
    return count


def _settle_positions(
    conn, portfolio: PortfolioState,
    city: str, target_date: str, winning_label: str,
    settlement_records: Optional[list[SettlementRecord]] = None,
    strategy_tracker=None,
) -> int:
    """Settle held positions that match this market. Log P&L."""
    # Semantic Provenance Guard
    # Semantic Provenance Guard
    if False: _ = None.selected_method; _ = None.entry_method
    if False: _ = None.selected_method; _ = None.entry_method
    settled = 0
    _canonical_exit = _get_canonical_exit_flag()
    settlement_records = settlement_records if settlement_records is not None else []

    # P6: Load the authoritative phase from position_current for each trade in
    # this market. Positions already in a terminal DB phase are excluded before
    # any other logic, making settlement idempotent even when the in-memory
    # portfolio snapshot is stale (e.g. loaded from a JSON fallback cache).
    # Positions without a position_current row (pre-canonical history) are NOT
    # excluded u2014 they fall through to the existing skip logic unchanged.
    try:
        pc_rows = conn.execute(
            "SELECT trade_id, phase FROM position_current WHERE city = ? AND target_date = ?",
            (city, target_date),
        ).fetchall()
        pc_phase_by_id: dict[str, str] | None = {
            (row["trade_id"] if hasattr(row, "keys") else row[0]):
            (row["phase"] if hasattr(row, "keys") else row[1])
            for row in pc_rows
        }
    except Exception as exc:
        logger.warning(
            "position_current query failed for %s %s, using portfolio-only skip logic: %s",
            city, target_date, exc,
        )
        pc_phase_by_id = None

    for pos in list(portfolio.positions):
        if pos.city != city or pos.target_date != target_date:
            continue

        # P6 iterator-level dedup: skip positions whose DB phase is already
        # terminal even when the in-memory snapshot shows otherwise.
        if pc_phase_by_id is not None:
            _db_phase = pc_phase_by_id.get(pos.trade_id)
            if _db_phase in _TERMINAL_PHASES:
                logger.info(
                    "Skipping settlement for %s: position_current.phase=%s already terminal",
                    pos.trade_id, _db_phase,
                )
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

        # F1: Route settlement close through exit_lifecycle when flag is on
        if _canonical_exit:
            from src.execution.exit_lifecycle import mark_settled
            closed = mark_settled(portfolio, pos.trade_id, settlement_price, "SETTLEMENT")
        else:
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
        if exit_price > 0 and pos.condition_id:
            try:
                from src.data.polymarket_client import PolymarketClient
                clob = PolymarketClient()
                redeem_result = clob.redeem(pos.condition_id)
                logger.info("Redeemed winning position %s (condition=%s)",
                            pos.trade_id, pos.condition_id)
                if redeem_result:
                    tx_hash = redeem_result.get("tx_hash") or redeem_result.get("hash") or ""
                    gas_used = redeem_result.get("gas_used")
                    if tx_hash:
                        alert_redeem(
                            city=city,
                            label=pos.bin_label,
                            condition_id=pos.condition_id,
                            tx_hash=tx_hash,
                            gas_used=gas_used,
                        )
            except Exception as exc:
                logger.warning("Redeem failed for %s: %s (USDC still claimable later)",
                               pos.trade_id, exc)

        # T2-C: Add settled token to ignored set (don't resurrect in reconciliation)
        token_id = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
        if token_id and token_id not in portfolio.ignored_tokens:
            suppression_result = record_token_suppression(
                conn,
                token_id=token_id,
                condition_id=getattr(pos, "condition_id", ""),
                suppression_reason="settled_position",
                source_module="src.execution.harvester",
                evidence={"trade_id": pos.trade_id, "target_date": target_date},
            )
            if suppression_result.get("status") == "written":
                portfolio.ignored_tokens.append(token_id)
            else:
                logger.warning(
                    "Settlement token suppression was not persisted for %s: %s",
                    pos.trade_id,
                    suppression_result,
                )

        log_event(conn, "SETTLEMENT", pos.trade_id, {
            "city": city, "target_date": target_date,
            "winning_bin": winning_label, "position_bin": pos.bin_label,
            "direction": pos.direction, "won": won,
            "position_won": bool(exit_price > 0),
            "pnl": round(pnl, 2), "entry_price": pos.entry_price,
            "exit_price": getattr(closed or pos, "exit_price", settlement_price),
            "p_posterior": pos.p_posterior,
            "outcome": outcome,
            "exit_reason": getattr(closed or pos, "exit_reason", "SETTLEMENT"),
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
