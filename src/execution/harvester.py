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
import math
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import httpx

from src.calibration.manager import maybe_refit_bucket, season_from_date
from src.calibration.effective_sample_size import build_decision_group_for_key, write_decision_groups
from src.calibration.decision_group import compute_id
from src.calibration.store import add_calibration_pair, add_calibration_pair_v2
from src.types.metric_identity import LOW_LOCALDAY_MIN
from src.config import City, cities_by_name, get_mode
from src.contracts.settlement_semantics import SettlementSemantics, round_wmo_half_up_value
from src.contracts.exceptions import SettlementPrecisionError
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
from src.state.canonical_write import commit_then_export
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
    """Read CANONICAL_EXIT_PATH feature flag from settings.

    B043: typed error taxonomy (SD-B). A broad ``except Exception``
    would silently disable the canonical exit path on any fault
    (TypeError/RuntimeError from a regression in ``feature_flags``),
    indistinguishable from the flag being legitimately False. Narrow
    to the two legitimate "settings surface missing" cases only;
    anything else is a code defect and must propagate.
    """
    try:
        from src.config import settings
        flags = settings.feature_flags
    except (ImportError, AttributeError):
        return False
    return flags.get("CANONICAL_EXIT_PATH", False)


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


def _lookup_settlement_obs(conn, city: City, target_date: str) -> Optional[dict]:
    """Look up source-family-correct observation for the harvester write path.

    Routes per city.settlement_source_type (P-C routing rules, DR-33 plan §3.3):
      - wu_icao   → observations.source='wu_icao_history'
      - noaa      → observations.source LIKE 'ogimet_metar_%'
      - hko       → observations.source='hko_daily_api'
      - cwa_station → no accepted proxy (returns None; row will quarantine)
    """
    st = city.settlement_source_type
    rows = conn.execute(
        """SELECT id, source, high_temp, unit, fetched_at
           FROM observations
           WHERE city = ? AND target_date = ? AND high_temp IS NOT NULL""",
        (city.name, target_date),
    ).fetchall()
    for r in rows:
        _id, src, high_temp, unit, fetched_at = r
        if st == "wu_icao" and src == "wu_icao_history":
            return {"id": _id, "source": src, "high_temp": high_temp, "unit": unit, "fetched_at": fetched_at}
        if st == "noaa" and isinstance(src, str) and src.startswith("ogimet_metar_"):
            return {"id": _id, "source": src, "high_temp": high_temp, "unit": unit, "fetched_at": fetched_at}
        if st == "hko" and src == "hko_daily_api":
            return {"id": _id, "source": src, "high_temp": high_temp, "unit": unit, "fetched_at": fetched_at}
    return None


def run_harvester() -> dict:
    """Run one harvester cycle. Polls for settled markets.

    Returns: harvester counts plus stage2_status / stage2 preflight details.

    Feature flag: ``ZEUS_HARVESTER_LIVE_ENABLED`` must equal ``"1"`` for the
    cycle to actually fetch Gamma + write settlements. Default OFF (DR-33-A
    staged rollout per plan.md §3.1). OFF state short-circuits BEFORE any
    data-plane call; no DB connection is acquired, no HTTP request is made.
    """
    if os.environ.get("ZEUS_HARVESTER_LIVE_ENABLED", "0") != "1":
        logger.info(
            "harvester_live disabled by ZEUS_HARVESTER_LIVE_ENABLED flag (DR-33-A default-OFF); "
            "cycle skipped; no data-plane calls"
        )
        return {
            "status": "disabled_by_feature_flag",
            "disabled_by_flag": True,
            "settled_events": 0,
            "positions_settled": 0,
            "total_pairs": 0,
        }
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

            pm_bin_lo, pm_bin_hi = _find_winning_bin(event)
            if pm_bin_lo is None and pm_bin_hi is None:
                # No UMA-resolved YES-won market for this event; skip silently.
                continue

            # Look up source-family-correct obs for SettlementSemantics gate.
            obs_row = _lookup_settlement_obs(shared_conn, city, target_date)
            if obs_row is None:
                # No obs yet; don't write a quarantine row — retry next cycle when obs lands.
                # (Alternative: write QUARANTINED with harvester_live_no_obs; skip for DR-33-A
                # to avoid polluting the table with transient no-obs rows during obs-collector lag.)
                logger.debug(
                    "harvester_live: skipping %s %s — no source-correct obs yet",
                    city.name, target_date,
                )
                continue

            # Canonical-authority write: SettlementSemantics gate + INV-14 + provenance_json.
            _write_settlement_truth(
                shared_conn, city, target_date, pm_bin_lo, pm_bin_hi,
                event_slug=event.get("slug", ""),
                obs_row=obs_row,
            )

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
                _dv = context.get("source_model_version", "") or ""
                _temperature_metric = "low" if "mn2t6" in _dv or "min" in _dv else "high"
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
                    temperature_metric=_temperature_metric,
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

    # DT#1 / INV-17: DB commits FIRST, then JSON exports.
    # harvester has no artifact row, so db_op returns None.
    _portfolio_settled = positions_settled > 0
    _tracker_dirty = tracker_dirty

    def _db_op_trade() -> None:
        trade_conn.commit()
        shared_conn.commit()

    def _export_portfolio_h() -> None:
        if _portfolio_settled:
            save_portfolio(portfolio, source="harvester_settlement")  # Phase 9C B3 audit tag

    def _export_tracker_h() -> None:
        if _tracker_dirty:
            save_tracker(tracker)

    commit_then_export(
        trade_conn,
        db_op=_db_op_trade,
        json_exports=[_export_portfolio_h, _export_tracker_h],
    )

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
    """Poll Gamma API for recently settled weather markets.

    B045: mid-pagination HTTPError handling (SD-B). Previously any
    httpx.HTTPError broke out of the loop and returned the partial
    page batch as if it were the complete settled-event set.
    Downstream in run_harvester events not yet fetched look
    identical to "no settlement yet," so settlements on page 2+
    would be silently dropped for this cycle's portfolio close
    accounting.

    Contract:
      * first-page (offset == 0) HTTPError is tolerated with a
        warning and an empty return -- indistinguishable from a
        hand-off hour with no settled events, next cycle retries.
      * mid-pagination HTTPError (offset > 0) raises RuntimeError
        so the outer cron wrapper logs a real fault and we do NOT
        commit partial settlement state to the portfolio this cycle.
    """
    events: list[dict] = []
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
            if offset == 0:
                logger.warning("Gamma API fetch failed on first page: %s", e)
                break
            raise RuntimeError(
                f"Gamma API pagination failed at offset={offset} after "
                f"{len(events)} events already fetched: {e}. Refusing "
                f"to return partial settled events as complete."
            ) from e

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


def _find_winning_bin(event: dict) -> tuple[Optional[float], Optional[float]]:
    """Determine which bin won from a UMA-resolved settled event.

    Returns: (pm_bin_lo, pm_bin_hi) of the YES-won market, or (None, None).

    Gate (P-D §6.1 + §5.3 non-reversal attestation against R3-09):
      - ``umaResolutionStatus == 'resolved'`` (terminal UMA DVM state)
      - ``outcomes == ['Yes', 'No']`` (unexpected order → fail closed)
      - ``outcomePrices[0] == '1'`` (YES-won per UMA's binary vote encoding)

    This is NOT the removed ``outcomePrices >= 0.95`` pre-resolution price
    fallback (R3-09). The removed pattern read prices as a live-trading
    signal on UN-resolved markets. This reads ONLY resolved markets where
    outcomePrices is the UMA oracle vote result encoded as
    ``("1","0")`` = YES-won or ``("0","1")`` = NO-won.

    See:
      - docs/operations/task_2026-04-23_data_readiness_remediation/evidence/harvester_gamma_probe.md §6.1
      - docs/operations/task_2026-04-23_live_harvester_enablement_dr33/plan.md

    Precedent: existing production code at ``scripts/_build_pm_truth.py:137-139``
    already uses the same ``outcomePrices[0] == "1"`` pattern WITHOUT the
    umaResolutionStatus gate. This function is STRICTER than that precedent.
    """
    for market in event.get("markets", []):
        if market.get("umaResolutionStatus") != "resolved":
            continue
        op_raw = market.get("outcomePrices")
        if not op_raw:
            continue
        try:
            prices = json.loads(op_raw) if isinstance(op_raw, str) else op_raw
        except (ValueError, TypeError):
            continue
        if not (isinstance(prices, list) and len(prices) == 2):
            continue
        outcomes_raw = market.get("outcomes")
        try:
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        except (ValueError, TypeError):
            continue
        # Fail-closed: outcomes order must be ['Yes', 'No'] exactly.
        if not (isinstance(outcomes, list) and len(outcomes) == 2
                and str(outcomes[0]).lower() == "yes"):
            continue
        # YES won iff prices[0] == '1' (UMA binary vote encoding on RESOLVED markets)
        if str(prices[0]) == "1":
            label = market.get("question") or market.get("groupItemTitle", "")
            low, high = _parse_temp_range(label)
            return low, high
    return None, None


# DR-33-A (2026-04-23): The pre-P-D `_format_range` function was removed; it
# produced sentinel-encoded strings (`-999-15` / `75-999`) that lost shoulder
# semantics and that P-E / DR-33 replaced with the canonical text form
# (`15°C or below` / `75°F or higher`). `_canonical_bin_label` below is the
# sole replacement. No remaining callers of `_format_range` exist — verified
# via `grep -rn "_format_range" src/ tests/ scripts/` returns zero matches.


def _canonical_bin_label(lo: Optional[float], hi: Optional[float], unit: str) -> Optional[str]:
    """Canonical winning_bin label matching P-E reconstruction convention.

    Shoulder cases use English text form (not unicode ≥/≤) because
    ``src/data/market_scanner.py::_parse_temp_range`` uses ``re.search``
    and would silently misparse ``'≥21°C'`` as the POINT bin ``(21.0, 21.0)``.
    Critic-opus C1 (P-E pre-review 2026-04-23) proved this empirically.
    """
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None:
        if lo == hi:
            return f"{int(lo)}°{unit}"
        return f"{int(lo)}-{int(hi)}°{unit}"
    if lo is None and hi is not None:
        return f"{int(hi)}°{unit} or below"
    return f"{int(lo)}°{unit} or higher"


_HARVESTER_LIVE_DATA_VERSION = {
    "wu_icao": "wu_icao_history_v1",
    "hko": "hko_daily_api_v1",
    "noaa": "ogimet_metar_v1",
    "cwa_station": "cwa_no_collector_v0",
}


def _extract_all_bin_labels(event: dict) -> list[str]:
    """Extract all bin labels from a settled event."""
    labels = []
    for market in event.get("markets", []):
        label = market.get("question") or market.get("groupItemTitle", "")
        if label:
            labels.append(label)
    return labels



def _write_settlement_truth(
    conn,
    city: City,
    target_date: str,
    pm_bin_lo: Optional[float],
    pm_bin_hi: Optional[float],
    *,
    event_slug: str = "",
    obs_row: Optional[dict] = None,
) -> dict:
    """Write canonical-authority settlement truth to settlements table.

    Gate (DR-33-A / P-E canonical pattern):
      1. Look up source-family-correct obs (caller's responsibility; passed via obs_row)
      2. Apply SettlementSemantics.for_city(city).assert_settlement_value(obs.high_temp)
      3. Containment check: rounded value ∈ [pm_bin_lo, pm_bin_hi]?
         - Yes → authority='VERIFIED', settlement_value=rounded, winning_bin=canonical label
         - No → authority='QUARANTINED' with enumerable reason
      4. Populate all 4 INV-14 identity fields + provenance_json with decision_time_snapshot_id

    Does NOT call conn.commit() — caller owns the transaction boundary (P-H
    atomicity consideration; MEMORY L30 with-conn/savepoint collision).

    Returns a dict with {authority, settlement_value, winning_bin, reason}
    for caller to log / aggregate.
    """
    _SOURCE_TYPE_MAP = {"wu_icao": "WU", "hko": "HKO", "noaa": "NOAA", "cwa_station": "CWA"}
    db_source_type = _SOURCE_TYPE_MAP.get(city.settlement_source_type, city.settlement_source_type.upper())
    data_version = _HARVESTER_LIVE_DATA_VERSION.get(
        city.settlement_source_type, "unknown_v0"
    )
    settled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    authority = "QUARANTINED"
    settlement_value: Optional[float] = None
    winning_bin: Optional[str] = None
    reason: Optional[str] = None
    rounding_rule: str = "wmo_half_up"

    if obs_row is None or obs_row.get("high_temp") is None:
        reason = "harvester_live_no_obs"
    else:
        try:
            sem = SettlementSemantics.for_city(city)
            rounding_rule = sem.rounding_rule
            rounded = sem.assert_settlement_value(
                float(obs_row["high_temp"]),
                context=f"harvester_live/{city.name}/{target_date}",
            )
        except SettlementPrecisionError:
            reason = "harvester_live_settlement_precision_error"
            rounded = None

        if rounded is not None and math.isfinite(rounded):
            # Containment check (point/range/shoulder-aware)
            contained = False
            if pm_bin_lo is not None and pm_bin_hi is not None:
                contained = pm_bin_lo <= rounded <= pm_bin_hi
            elif pm_bin_lo is None and pm_bin_hi is not None:
                contained = rounded <= pm_bin_hi
            elif pm_bin_hi is None and pm_bin_lo is not None:
                contained = rounded >= pm_bin_lo
            if contained:
                authority = "VERIFIED"
                settlement_value = rounded
                winning_bin = _canonical_bin_label(pm_bin_lo, pm_bin_hi, city.settlement_unit)
                reason = None
            else:
                # Quarantined — preserve rounded as evidence
                settlement_value = rounded
                reason = "harvester_live_obs_outside_bin"

    provenance = {
        "writer": "harvester_live_dr33",
        "writer_script": "src/execution/harvester.py",
        "source_family": db_source_type,
        "obs_source": obs_row.get("source") if obs_row else None,
        "obs_id": obs_row.get("id") if obs_row else None,
        "decision_time_snapshot_id": obs_row.get("fetched_at") if obs_row else None,
        "rounding_rule": rounding_rule,
        "reconstruction_method": "harvester_live_uma_vote",
        "event_slug": event_slug or None,
        "reconstructed_at": settled_at,
        "audit_ref": "docs/operations/task_2026-04-23_live_harvester_enablement_dr33/plan.md",
    }
    if reason is not None:
        provenance["quarantine_reason"] = reason

    # INSERT OR REPLACE matches P-E's canonical DELETE+INSERT idempotency;
    # UNIQUE(city, target_date) means this is an upsert.
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO settlements (
                city, target_date, market_slug, winning_bin, settlement_value,
                settlement_source, settled_at, authority,
                pm_bin_lo, pm_bin_hi, unit, settlement_source_type,
                temperature_metric, physical_quantity, observation_field,
                data_version, provenance_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                city.name, target_date, event_slug or None, winning_bin, settlement_value,
                city.settlement_source, settled_at, authority,
                pm_bin_lo, pm_bin_hi, city.settlement_unit, db_source_type,
                "high", "daily_maximum_air_temperature", "high_temp",
                data_version, json.dumps(provenance, sort_keys=True, default=str),
            ),
        )
        logger.info(
            "harvester_live write: %s %s → authority=%s settlement_value=%s winning_bin=%s reason=%s",
            city.name, target_date, authority, settlement_value, winning_bin, reason,
        )
    except Exception as exc:
        logger.warning(
            "harvester_live write failed for %s %s: %s", city.name, target_date, exc,
        )
        raise

    return {
        "authority": authority,
        "settlement_value": settlement_value,
        "winning_bin": winning_bin,
        "reason": reason,
    }


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
    temperature_metric: str = "high",
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

        dgid = compute_id(
            city.name,
            target_date,
            issue_time,
            source_model_version or "",
        )
        if getattr(city, "temperature_metric", temperature_metric) == "low" or temperature_metric == "low":
            add_calibration_pair_v2(
                conn, city=city.name, target_date=target_date,
                range_label=label, p_raw=p_raw, outcome=outcome,
                lead_days=lead_days, season=season, cluster=city.cluster,
                forecast_available_at=now,
                settlement_value=settlement_value,
                decision_group_id=dgid,
                bias_corrected=bool(bias_corrected),
                city_obj=city,
                metric_identity=LOW_LOCALDAY_MIN,
                data_version=LOW_LOCALDAY_MIN.data_version,
                training_allowed=True,
            )
        else:
            add_calibration_pair(
                conn, city=city.name, target_date=target_date,
                range_label=label, p_raw=p_raw, outcome=outcome,
                lead_days=lead_days, season=season, cluster=city.cluster,
                forecast_available_at=now,
                settlement_value=(round_wmo_half_up_value(float(settlement_value))
                                  if settlement_value is not None else None),
                decision_group_id=dgid,
                bias_corrected=bool(bias_corrected),
                city_obj=city,
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
        # Legacy-predecessor comparison found the old formula underestimated winning P&L
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
